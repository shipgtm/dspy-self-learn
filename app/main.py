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


@app.get("/api/contacts/{contact_id}")
async def get_contact(contact_id: int) -> dict:
    row = await db.get_contact(contact_id)
    if row is None:
        raise HTTPException(404, "contact not found")
    return row


@app.get("/api/versions")
async def list_versions() -> dict:
    return {"active_version": await db.active_version(), "versions": await db.list_versions()}


@app.get("/api/optimizations")
async def list_optimizations() -> dict:
    return {"optimizations": await db.list_optimizations()}


@app.get("/api/stats")
async def stats() -> dict:
    return await db.stats()
