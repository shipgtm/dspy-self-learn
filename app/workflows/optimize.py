"""Optimization workflow: GEPA-optimize the active program and promote it if it wins."""

from app.workflow import wf


@wf.step(max_retries=1)
async def optimize_and_compare(*, batch: int) -> dict:
    """Run GEPA from the active version, compare on held-out contacts, and
    store the candidate if it wins. A single step because the compiled
    program only exists in memory until it is saved.

    The queue re-sends a step that runs for more than a few minutes even
    though the first attempt is still working. A Postgres advisory lock makes
    those extra attempts exit instead of starting a second, paid GEPA run.
    """
    import asyncio

    from app import config, db, optimize
    from app.program import load_program, save_program

    async with db.optimization_lock(batch) as acquired:
        if not acquired:
            print(f"[optimize] batch {batch} is already running in another attempt; skipping", flush=True)
            return {"status": "duplicate", "batch": batch}

        base_version = await db.active_version()
        base = await load_program(base_version)
        rows = await db.training_rows(config.OPTIMIZE_RECENT, config.OPTIMIZE_HISTORY)
        result = await asyncio.to_thread(optimize.run, base, rows, batch)

        if result["status"] == "skipped":
            summary = {"detail": result["detail"]}
            await db.record_optimization(batch, "skipped", summary)
            return {"status": "skipped", **summary}

        scores = {
            "base": result["base_score"],
            "candidate": result["candidate_score"],
            "n_train": result["n_train"],
            "n_val": result["n_val"],
            "n_test": result["n_test"],
        }
        summary = {
            "base_version": base_version,
            "scores": scores,
            "instructions": result["instructions"],
            "timings": result["timings"],
        }
        if scores["candidate"] < scores["base"] + config.MIN_IMPROVEMENT:
            await db.record_optimization(batch, "rejected", summary)
            return {"status": "rejected", **summary}

        location = await save_program(result["candidate"], f"batch{batch}")
        version = await db.add_version(location, result["instructions"], scores)
        summary["version"] = version
        await db.record_optimization(batch, "promoted", summary)
        return {"status": "promoted", **summary}


@wf.workflow
async def optimize_program(*, batch: int) -> dict:
    return await optimize_and_compare(batch=batch)
