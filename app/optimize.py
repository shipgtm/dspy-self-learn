"""GEPA optimization of the email program against the Jev judge."""

import asyncio
import json
import random
import threading
from collections.abc import Coroutine
from typing import Any

import dspy

from app import config, db, judge
from app.program import EmailProgram, generation_lm, instructions, make_lm

INPUTS = ("campaign", "contact", "previous_draft", "reviewer_feedback")


class _Loop:
    """A single background event loop for running the async judge from GEPA's
    worker threads, so the AI SDK's HTTP clients stay bound to one loop."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def run[T](self, coro: Coroutine[Any, Any, T]) -> T:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()


_loop: _Loop | None = None


def _run(coro):
    global _loop
    if _loop is None:
        _loop = _Loop()
    return _loop.run(coro)


def to_example(row: dict) -> dspy.Example:
    """A first-attempt example: the same inputs the email workflow uses on attempt 1."""
    return dspy.Example(
        campaign=config.CAMPAIGN_BRIEF,
        contact=row["profile"],
        previous_draft="",
        reviewer_feedback="",
        # Kept for analysis only; GEPA scores fresh generations with the judge.
        past_feedback=row["feedback"] or "",
        past_score=row["score"],
    ).with_inputs(*INPUTS)


def metric(gold, pred, trace=None, pred_name=None, pred_trace=None, program_trace=None):
    subject = (getattr(pred, "subject", "") or "").strip()
    body = (getattr(pred, "body", "") or "").strip()
    if not subject or not body:
        return dspy.Prediction(score=0.0, feedback="The program produced an empty subject or body.")
    verdict = _run(
        judge.judge(
            campaign=gold.campaign,
            contact=json.loads(gold.contact),
            subject=subject,
            body=body,
        )
    )
    scores = ", ".join(f"{k}={v:.2f}" for k, v in verdict.criteria.items())
    status = "PASSED" if verdict.passed else "FAILED"
    feedback = f"{status} review (score {verdict.score:.2f}; {scores}).\n{verdict.feedback}"
    return dspy.Prediction(score=verdict.score, feedback=feedback)


def split(examples: list[dspy.Example], seed: int) -> tuple[list, list]:
    """Shuffle deterministically and hold out ~30% for the head-to-head comparison."""
    examples = examples[:]
    random.Random(seed).shuffle(examples)
    n_test = max(3, round(len(examples) * 0.3))
    return examples[n_test:], examples[:n_test]


def score(program: EmailProgram, devset: list[dspy.Example]) -> float:
    evaluator = dspy.Evaluate(devset=devset, metric=metric, num_threads=8, display_progress=False)
    return float(evaluator(program).score) / 100


def run(base: EmailProgram, seed: int) -> dict:
    """Optimize `base` and score both programs. Blocking; call from a thread."""
    rows = db.training_rows(config.OPTIMIZE_RECENT, config.OPTIMIZE_HISTORY)
    examples = [to_example(r) for r in rows]
    if len(examples) < 10:
        return {"status": "skipped", "detail": f"only {len(examples)} examples"}

    trainset, testset = split(examples, seed)
    with dspy.context(lm=generation_lm()):
        gepa = dspy.GEPA(
            metric=metric,
            auto=None if config.GEPA_MAX_METRIC_CALLS else config.GEPA_AUTO,
            max_metric_calls=config.GEPA_MAX_METRIC_CALLS,
            reflection_lm=make_lm(config.REFLECTION_MODEL, temperature=1.0, max_tokens=16000),
            num_threads=8,
            track_stats=False,
            seed=seed,
        )
        candidate = gepa.compile(base.deepcopy(), trainset=trainset)
        base_score = score(base, testset)
        candidate_score = score(candidate, testset)

    return {
        "status": "evaluated",
        "candidate": candidate,
        "base_score": base_score,
        "candidate_score": candidate_score,
        "instructions": instructions(candidate),
        "n_train": len(trainset),
        "n_test": len(testset),
    }
