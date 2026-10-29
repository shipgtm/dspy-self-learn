"""The DSPy email program and how versions of it are stored and loaded.

Version 0 is the unoptimized baseline built from code. Every later version is
the JSON state of an optimized program, stored in Vercel Blob (or under
DATA_DIR when no Blob token is configured, for local development).
"""

import json
import os

import dspy

from app import config, db


class WriteEmail(dspy.Signature):
    """Write a short, personalized cold outreach email to the contact on behalf of the campaign.
    If a previous draft and reviewer feedback are given, revise the draft to address every point."""

    campaign: str = dspy.InputField(desc="What we offer and the goal of the email")
    contact: str = dspy.InputField(desc="JSON profile of the recipient")
    previous_draft: str = dspy.InputField(desc="Earlier draft that failed review; empty on the first attempt")
    reviewer_feedback: str = dspy.InputField(desc="Why the previous draft failed; empty on the first attempt")
    subject: str = dspy.OutputField(desc="Subject line, under 70 characters")
    body: str = dspy.OutputField(desc="Plain-text email body, signed by the sender")


class EmailProgram(dspy.Module):
    def __init__(self) -> None:
        super().__init__()
        self.write = dspy.Predict(WriteEmail)

    def forward(self, campaign: str, contact: str, previous_draft: str = "", reviewer_feedback: str = ""):
        return self.write(
            campaign=campaign,
            contact=contact,
            previous_draft=previous_draft,
            reviewer_feedback=reviewer_feedback,
        )

    async def aforward(self, campaign: str, contact: str, previous_draft: str = "", reviewer_feedback: str = ""):
        return await self.write.acall(
            campaign=campaign,
            contact=contact,
            previous_draft=previous_draft,
            reviewer_feedback=reviewer_feedback,
        )


def make_lm(model: str, **kwargs) -> dspy.LM:
    return dspy.LM(
        f"vercel_ai_gateway/{model}",
        api_key=config.gateway_api_key(),
        **kwargs,
    )


def generation_lm() -> dspy.LM:
    return make_lm(config.GENERATION_MODEL, temperature=0.7, max_tokens=2000)


def instructions(program: EmailProgram) -> str:
    return program.write.signature.instructions


# --- versioned storage ------------------------------------------------------


def _use_blob() -> bool:
    return bool(os.environ.get("BLOB_READ_WRITE_TOKEN"))


async def save_program(program: EmailProgram, name: str) -> str:
    """Persist a program's state and return where it was stored."""
    data = json.dumps(program.dump_state(), indent=2).encode()
    pathname = f"programs/email-{name}.json"
    if _use_blob():
        from vercel.blob import put_blob

        result = await put_blob(
            pathname,
            data,
            access="private",
            content_type="application/json",
            allow_overwrite=True,
        )
        return result.url
    path = config.DATA_DIR / pathname
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


async def _read_location(location: str) -> bytes:
    if location.startswith("http"):
        from vercel.blob import get_blob

        return (await get_blob(location, access="private")).body
    with open(location, "rb") as f:
        return f.read()


_cache: dict[int, dict] = {}


async def load_program(version: int) -> EmailProgram:
    """Build the program for a version. Versions are immutable, so their state
    is cached for the lifetime of the process."""
    program = EmailProgram()
    if version == 0:
        return program
    if version not in _cache:
        row = await db.get_version(version)
        if row is None:
            raise LookupError(f"program version {version} not found")
        _cache[version] = json.loads(await _read_location(row["location"]))
    program.load_state(_cache[version])
    return program

