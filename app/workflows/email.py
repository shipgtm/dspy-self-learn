"""Per-contact email workflow: generate -> judge -> retry with feedback -> send or flag.

The workflow body only orchestrates. Heavy modules (DSPy, sqlite, the AI SDK)
are imported inside step bodies so the deterministic workflow sandbox never
has to load them.
"""

from vercel import workflow
from vercel.workflow import FatalError

from app.workflow import wf
from app.workflows.optimize import optimize_program


@wf.step
async def claim_contact(*, contact_id: str) -> dict:
    """Mark the contact running and pin the program version it will use."""
    from app import db

    run_id = workflow.get_step_metadata().run_id
    try:
        row = db.claim_contact(contact_id, run_id)
    except LookupError:
        raise FatalError(f"unknown contact {contact_id}") from None
    except RuntimeError as e:
        raise FatalError(str(e)) from None
    return {"status": row["status"], "version": row["program_version"]}


@wf.step
async def generate_draft(
    *, contact_id: str, attempt: int, version: int, previous_draft: str, feedback: str
) -> dict:
    from app import config, db
    from app.program import generation_lm, load_program

    import dspy

    if (existing := db.get_attempt(contact_id, attempt)) is not None:
        return {"subject": existing["subject"], "body": existing["body"]}

    contact = db.get_contact(contact_id)
    program = await load_program(version)
    with dspy.context(lm=generation_lm()):
        pred = await program.acall(
            campaign=config.CAMPAIGN_BRIEF,
            contact=contact["profile"],
            previous_draft=previous_draft,
            reviewer_feedback=feedback,
        )
    row = db.save_draft(contact_id, attempt, version, pred.subject.strip(), pred.body.strip(), feedback)
    return {"subject": row["subject"], "body": row["body"]}


@wf.step
async def evaluate_draft(*, contact_id: str, attempt: int, subject: str, body: str) -> dict:
    import json

    from app import config, db, judge

    contact = db.get_contact(contact_id)
    verdict = await judge.judge(
        campaign=config.CAMPAIGN_BRIEF,
        contact=json.loads(contact["profile"]),
        subject=subject,
        body=body,
    )
    db.save_verdict(contact_id, attempt, verdict.score, verdict.passed, verdict.feedback, verdict.criteria)
    return verdict.model_dump()


@wf.step
async def send_email(*, contact_id: str, subject: str, body: str) -> bool:
    """Stub sender: records the email in the outbox. The outbox is keyed by
    contact, so a contact can never be sent twice. Swap in a real provider
    here, using the contact ID as the provider's idempotency key."""
    import logging

    from app import db

    contact = db.get_contact(contact_id)
    first = db.record_send(contact_id, contact["email"], subject, body)
    if first:
        logging.getLogger(__name__).info("sent email to %s: %s", contact["email"], subject)
    return first


@wf.step
async def finish_contact(*, contact_id: str, status: str, attempts: int, score: float | None) -> int:
    """Close out the contact and take its completion sequence number."""
    from app import db

    db.finish_contact(contact_id, status, attempts, score)
    return db.record_completion(contact_id)


@wf.step
async def trigger_optimization(*, batch: int) -> str:
    """Start the optimization workflow for this batch, at most once."""
    from app import db

    claim = db.claim_optimization(batch)
    if claim["run_id"]:
        return claim["run_id"]
    run = await workflow.start(optimize_program, batch=batch)
    db.update_optimization(batch, run_id=run.run_id)
    return run.run_id


@wf.workflow
async def email_contact(*, contact_id: str) -> dict:
    from app import config

    claim = await claim_contact(contact_id=contact_id)
    if claim["status"] != "running":
        # Already finished by an earlier run; nothing to do.
        return {"contact_id": contact_id, "status": claim["status"], "skipped": True}

    version = claim["version"]
    previous_draft, feedback = "", ""
    draft, verdict, attempt = None, None, 0
    for attempt in range(1, config.MAX_ATTEMPTS + 1):
        draft = await generate_draft(
            contact_id=contact_id,
            attempt=attempt,
            version=version,
            previous_draft=previous_draft,
            feedback=feedback,
        )
        verdict = await evaluate_draft(contact_id=contact_id, attempt=attempt, **draft)
        if verdict["passed"]:
            break
        previous_draft = f"Subject: {draft['subject']}\n\n{draft['body']}"
        feedback = verdict["feedback"]

    if verdict["passed"]:
        await send_email(contact_id=contact_id, **draft)
        status = "sent"
    else:
        status = "flagged"

    seq = await finish_contact(
        contact_id=contact_id, status=status, attempts=attempt, score=verdict["score"]
    )
    if seq % config.OPTIMIZE_EVERY == 0:
        await trigger_optimization(batch=seq // config.OPTIMIZE_EVERY)

    return {
        "contact_id": contact_id,
        "status": status,
        "attempts": attempt,
        "score": verdict["score"],
        "program_version": version,
    }
