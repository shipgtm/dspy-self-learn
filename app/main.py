import pydantic
from fastapi import FastAPI, HTTPException
from vercel import workflow

from app import db
from app.workflows.email import email_contact

app = FastAPI(title="Self-learning email generation")


class Contact(pydantic.BaseModel):
    email: str = pydantic.Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str
    company: str | None = None
    role: str | None = None
    notes: str | None = None


@app.post("/api/contacts")
async def submit_contact(contact: Contact) -> dict:
    """Store the contact and start its email workflow."""
    profile = contact.model_dump(exclude_none=True)
    contact_id = await db.create_contact(contact.email, profile)
    run = await workflow.start(email_contact, contact_id=contact_id, email=contact.email, profile=profile)
    return {"contact_id": contact_id, "run_id": run.run_id}


@app.get("/api/contacts")
async def list_contacts(status: str | None = None) -> dict:
    return {"contacts": await db.list_contacts(status)}


@app.get("/api/contacts/{contact_id}")
async def get_contact(contact_id: int) -> dict:
    row = await db.get_contact(contact_id)
    if row is None:
        raise HTTPException(404, "contact not found")
    return row


@app.get("/api/dashboard")
async def dashboard() -> dict:
    """Everything the dashboard needs in one request. `vercel dev` drops its
    Python server when several requests overlap, so the page polls only this."""
    return {
        "stats": await stats(),
        "contacts": (await list_contacts())["contacts"],
        "optimizations": (await list_optimizations())["optimizations"],
        "versions": (await list_versions())["versions"],
    }


@app.get("/api/versions")
async def list_versions() -> dict:
    from app.program import EmailProgram, instructions

    baseline = {"version": 0, "instructions": instructions(EmailProgram()), "location": None}
    return {
        "active_version": await db.active_version(),
        "versions": [*await db.list_versions(), baseline],
    }


@app.get("/api/optimizations")
async def list_optimizations() -> dict:
    return {"optimizations": await db.list_optimizations()}


@app.get("/api/stats")
async def stats() -> dict:
    from app import config

    return {
        **await db.stats(),
        "pass_threshold": config.PASS_THRESHOLD,
        "criterion_floor": config.CRITERION_FLOOR,
        "optimize_every": config.OPTIMIZE_EVERY,
        "max_attempts": config.MAX_ATTEMPTS,
        "min_improvement": config.MIN_IMPROVEMENT,
        "gepa_max_metric_calls": config.GEPA_MAX_METRIC_CALLS,
        "gepa_valset_size": config.GEPA_VALSET_SIZE,
        "generation_model": config.GENERATION_MODEL,
        "reflection_model": config.REFLECTION_MODEL,
        "judge_model": config.JUDGE_MODEL,
    }
