"""GEPA optimization of the email program against the Jev judge."""

import asyncio
import json
import itertools
import random
import threading
import time
from collections.abc import Coroutine
from typing import Any

import dspy
import litellm  # noqa: F401  # load fully before GEPA's threads race to import it

from app import config, judge
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
        contact=json.dumps(row["profile"]),
        previous_draft="",
        reviewer_feedback="",
        # Kept for analysis only; GEPA scores fresh generations with the judge.
        past_feedback=row["first"]["feedback"],
        past_score=row["first"]["score"],
    ).with_inputs(*INPUTS)


_judge_calls = itertools.count(1)


def metric(gold, pred, trace=None, pred_name=None, pred_trace=None, program_trace=None):
    next(_judge_calls)
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


def split(examples: list[dspy.Example], seed: int) -> tuple[list, list, list]:
    """Shuffle deterministically into (train, val, test).

    ~30% is held out as test for the head-to-head comparison. GEPA learns from
    train and scores promising prompts on a small val set, so each full score
    costs GEPA_VALSET_SIZE generations instead of the whole training set.
    """
    examples = examples[:]
    random.Random(seed).shuffle(examples)
    n_test = max(3, round(len(examples) * 0.3))
    n_val = min(config.GEPA_VALSET_SIZE, max(3, (len(examples) - n_test) // 3))
    test, val, train = examples[:n_test], examples[n_test : n_test + n_val], examples[n_test + n_val :]
    return train, val, test


def _log(message: str) -> None:
    print(f"[optimize] {message}", flush=True)


def score(program: EmailProgram, devset: list[dspy.Example]) -> float:
    evaluator = dspy.Evaluate(devset=devset, metric=metric, num_threads=8, display_progress=False)
    return float(evaluator(program).score) / 100


def run(base: EmailProgram, rows: list[dict], seed: int) -> dict:
    """Optimize `base` on `rows` and score both programs. Blocking; call from a thread."""
    examples = [to_example(r) for r in rows]
    if len(examples) < 10:
        return {"status": "skipped", "detail": f"only {len(examples)} examples"}

    trainset, valset, testset = split(examples, seed)
    _log(f"train={len(trainset)} val={len(valset)} test={len(testset)} budget={config.GEPA_MAX_METRIC_CALLS or config.GEPA_AUTO}")
    gen_lm = generation_lm()
    reflection_lm = make_lm(config.REFLECTION_MODEL, temperature=1.0, max_tokens=16000)
    judge_start = next(_judge_calls)
    timings = {}

    with dspy.context(lm=gen_lm):
        gepa = dspy.GEPA(
            metric=metric,
            auto=None if config.GEPA_MAX_METRIC_CALLS else config.GEPA_AUTO,
            max_metric_calls=config.GEPA_MAX_METRIC_CALLS,
            reflection_lm=reflection_lm,
            num_threads=8,
            track_stats=False,
            seed=seed,
        )
        started = time.monotonic()
        candidate = gepa.compile(base.deepcopy(), trainset=trainset, valset=valset)
        timings["gepa_s"] = round(time.monotonic() - started, 1)
        _log(f"GEPA done in {timings['gepa_s']}s")

        started = time.monotonic()
        base_score = score(base, testset)
        candidate_score = score(candidate, testset)
        timings["compare_s"] = round(time.monotonic() - started, 1)
        _log(f"comparison done in {timings['compare_s']}s: base={base_score:.3f} candidate={candidate_score:.3f}")

    # LM history includes cache hits, so these are upper bounds on paid calls.
    timings["generation_calls"] = len(gen_lm.history)
    timings["reflection_calls"] = len(reflection_lm.history)
    timings["judge_calls"] = next(_judge_calls) - judge_start - 1
    _log(f"calls: {timings}")

    return {
        "status": "evaluated",
        "candidate": candidate,
        "base_score": base_score,
        "candidate_score": candidate_score,
        "instructions": instructions(candidate),
        "n_train": len(trainset),
        "n_val": len(valset),
        "n_test": len(testset),
        "timings": timings,
    }
