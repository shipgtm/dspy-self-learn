"""Optimization workflow: GEPA-optimize the active program and promote it if it wins."""

from vercel import workflow
from vercel.workflow import FatalError

from app.workflow import wf


@wf.step(max_retries=1)
async def optimize_and_compare(*, batch: int) -> dict:
    """Run GEPA from the active version, compare on held-out contacts, and
    store the candidate if it wins. A single step because the compiled
    program only exists in memory until it is saved."""
    import asyncio

    from app import config, db, optimize
    from app.program import load_program, save_program

    run_id = workflow.get_step_metadata().run_id
    claim = db.claim_optimization(batch)
    if claim["run_id"] not in (None, run_id):
        raise FatalError(f"batch {batch} is owned by run {claim['run_id']}")
    if claim["status"] not in ("pending", "running", "failed"):
        return claim  # already decided on an earlier attempt

    base_version = db.active_version()
    db.update_optimization(batch, run_id=run_id, status="running", base_version=base_version)
    base = await load_program(base_version)

    try:
        result = await asyncio.to_thread(optimize.run, base, batch)
    except Exception as e:
        db.update_optimization(batch, status="failed", detail=repr(e)[:2000], finished_at=db.now())
        raise

    if result["status"] == "skipped":
        db.update_optimization(batch, status="skipped", detail=result["detail"], finished_at=db.now())
        return {"status": "skipped", "detail": result["detail"]}

    base_score, candidate_score = result["base_score"], result["candidate_score"]
    summary = {
        "base_version": base_version,
        "base_score": base_score,
        "candidate_score": candidate_score,
    }
    if candidate_score < base_score + config.MIN_IMPROVEMENT:
        db.update_optimization(batch, status="rejected", finished_at=db.now(), **summary)
        return {"status": "rejected", **summary}

    version = db.next_version()
    location = await save_program(result["candidate"], f"v{version}-batch{batch}")
    promoted = db.promote_version(
        version=version,
        parent_version=base_version,
        location=location,
        instructions=result["instructions"],
        score=candidate_score,
        parent_score=base_score,
    )
    status = "promoted" if promoted else "superseded"
    db.update_optimization(
        batch,
        status=status,
        candidate_version=version if promoted else None,
        detail=None if promoted else "active version changed during optimization",
        finished_at=db.now(),
        **summary,
    )
    return {"status": status, "version": version, **summary}


@wf.workflow
async def optimize_program(*, batch: int) -> dict:
    return await optimize_and_compare(batch=batch)
