import hashlib
import json

import pydantic
from fastapi import FastAPI, HTTPException
from vercel import workflow

from app import db
from app.workflows.email import email_contact

app = FastAPI(title="Self-learning email generation")
db.init()


class Contact(pydantic.BaseModel):
    email: str = pydantic.Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str
    external_id: str | None = None
    company: str | None = None
    role: str | None = None
    notes: str | None = None

    def contact_id(self) -> str:
        key = self.external_id or self.email.lower()
        return "c_" + hashlib.sha256(key.encode()).hexdigest()[:20]


@app.post("/api/contacts")
async def submit_contact(contact: Contact) -> dict:
    """Start the email workflow for a contact.

    Contacts are deduplicated by external_id (or email), so resubmitting a
    contact returns its existing run instead of starting a second one.
    """
    profile = contact.model_dump(exclude={"external_id"}, exclude_none=True)
    row, created = db.upsert_contact(contact.contact_id(), contact.email.lower(), profile)
    if row["run_id"] is None:
        run = await workflow.start(
            email_contact, contact_id=row["id"], email=row["email"], profile=profile
        )
        db.set_run_id(row["id"], run.run_id)
        row = db.get_contact(row["id"])
    return {"contact_id": row["id"], "run_id": row["run_id"], "status": row["status"], "created": created}


@app.get("/api/contacts/{contact_id}")
async def get_contact(contact_id: str) -> dict:
    row = db.get_contact(contact_id)
    if row is None:
        raise HTTPException(404, "contact not found")
    with db.read() as conn:
        attempts = [
            {**dict(a), "criteria": json.loads(a["criteria"]) if a["criteria"] else None}
            for a in conn.execute(
                "SELECT * FROM attempts WHERE contact_id = ? ORDER BY attempt", (contact_id,)
            )
        ]
    return {**row, "profile": json.loads(row["profile"]), "attempts": attempts}


@app.get("/api/versions")
async def list_versions() -> dict:
    return {"active_version": db.active_version(), "versions": db.list_versions()}


@app.get("/api/optimizations")
async def list_optimizations() -> dict:
    return {"optimizations": db.list_optimizations()}


@app.get("/api/stats")
async def stats() -> dict:
    return db.stats()
