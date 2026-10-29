"""Per-contact email workflow: generate -> judge -> retry with feedback -> send or flag.

The workflow body only orchestrates. Heavy modules (DSPy, psycopg, the AI SDK)
are imported inside step bodies so the deterministic workflow sandbox never
has to load them.
"""

from vercel import workflow

from app.workflow import wf
from app.workflows.optimize import optimize_program


@wf.step
async def get_active_version() -> int:
    """The program version this run uses. Step results are replayed, so the
    run keeps this version even if a newer one is promoted mid-run."""
    from app import db

    return await db.active_version()


@wf.step
async def generate_draft(*, profile: dict, version: int, previous_draft: str, feedback: str) -> dict:
    import json

    import dspy

    from app import config
    from app.program import generation_lm, load_program

    program = await load_program(version)
    with dspy.context(lm=generation_lm()):
        pred = await program.acall(
            campaign=config.CAMPAIGN_BRIEF,
            contact=json.dumps(profile),
            previous_draft=previous_draft,
            reviewer_feedback=feedback,
        )
    return {"subject": pred.subject.strip(), "body": pred.body.strip()}


@wf.step
async def evaluate_draft(*, contact_id: int, profile: dict, subject: str, body: str) -> dict:
    """Judge the draft and log the attempt on the contact."""
    from app import config, db, judge

    verdict = await judge.judge(
        campaign=config.CAMPAIGN_BRIEF, contact=profile, subject=subject, body=body
    )
    await db.add_attempt(contact_id, {"subject": subject, "body": body, **verdict.model_dump()})
    return verdict.model_dump()


@wf.step
async def send_email(*, email: str, subject: str, body: str) -> None:
    """Stub sender: logs instead of delivering."""
    import logging

    logging.getLogger(__name__).info("sent email to %s: %s", email, subject)


@wf.step
async def finish_contact(*, contact_id: int, status: str, version: int) -> int:
    from app import db

    return await db.finish_contact(contact_id, status, version)


@wf.step
async def start_optimization(*, batch: int) -> str:
    run = await workflow.start(optimize_program, batch=batch)
    return run.run_id


@wf.workflow
async def email_contact(*, contact_id: int, email: str, profile: dict) -> dict:
    from app import config

    version = await get_active_version()
    previous_draft, feedback = "", ""
    for attempt in range(1, config.MAX_ATTEMPTS + 1):
        draft = await generate_draft(
            profile=profile, version=version, previous_draft=previous_draft, feedback=feedback
        )
        verdict = await evaluate_draft(contact_id=contact_id, profile=profile, **draft)
        if verdict["passed"]:
            break
        previous_draft = f"Subject: {draft['subject']}\n\n{draft['body']}"
        feedback = verdict["feedback"]

    if verdict["passed"]:
        await send_email(email=email, **draft)
        status = "sent"
    else:
        status = "flagged"

    seq = await finish_contact(contact_id=contact_id, status=status, version=version)
    if seq % config.OPTIMIZE_EVERY == 0:
        await start_optimization(batch=seq // config.OPTIMIZE_EVERY)

    return {"contact_id": contact_id, "status": status, "attempts": attempt, "version": version}
