"""Email judge built on the Jev evaluation model through AI Gateway.

Jev answers typed questions about a shared state. Each rubric criterion is a
Noul question ("probability this statement is true"). The judge combines the
probabilities into a pass/fail verdict and turns weak criteria into written
feedback, which drives both regeneration and GEPA's reflection.
"""

import pydantic

import ai
from ai.ops.experimental import NoulAnswer, NoulQuestion, evaluate

from app import config

# criterion -> (statement Jev scores, feedback given when it scores low)
RUBRIC: dict[str, tuple[str, str]] = {
    "personalized": (
        "The email references specific details from the contact's profile (role, company, "
        "or notes) in a way that could not be sent unchanged to someone else.",
        "Personalize it: reference concrete details from the contact's profile, not generic praise.",
    ),
    "relevant_value": (
        "The email clearly connects the campaign's offer to a problem this contact plausibly has.",
        "Tie the offer to a specific problem this contact is likely to have, given their role and company.",
    ),
    "clear_cta": (
        "The email ends with a single, specific, low-friction call to action that matches the campaign goal.",
        "End with one specific, low-friction call to action that matches the campaign goal.",
    ),
    "concise": (
        "The body is under about 150 words and contains no filler or repetition.",
        "Cut it to under ~150 words; remove filler, repetition and throat-clearing.",
    ),
    "tone": (
        "The tone is professional, warm and human, without hype, pressure, or salesy clichés.",
        "Use a warmer, more natural tone; drop hype, pressure tactics and sales clichés.",
    ),
    "subject_line": (
        "The subject line is short, specific to the contact, and not clickbait or spammy.",
        "Write a short, specific subject line for this contact; avoid clickbait and spam triggers.",
    ),
    "accurate": (
        "The email makes no claims about the contact or the offer that are not supported by the "
        "provided profile and campaign brief, and contains no placeholders like [Name].",
        "Remove invented facts and placeholders; only claim what the profile and campaign brief support.",
    ),
}


class Questions(pydantic.BaseModel):
    personalized: NoulQuestion
    relevant_value: NoulQuestion
    clear_cta: NoulQuestion
    concise: NoulQuestion
    tone: NoulQuestion
    subject_line: NoulQuestion
    accurate: NoulQuestion


class Answers(pydantic.BaseModel):
    personalized: NoulAnswer
    relevant_value: NoulAnswer
    clear_cta: NoulAnswer
    concise: NoulAnswer
    tone: NoulAnswer
    subject_line: NoulAnswer
    accurate: NoulAnswer


QUESTIONS = Questions(
    **{name: NoulQuestion(instructions=statement) for name, (statement, _) in RUBRIC.items()}
)


class Verdict(pydantic.BaseModel):
    passed: bool
    score: float
    criteria: dict[str, float]
    feedback: str


def verdict_from_scores(criteria: dict[str, float]) -> Verdict:
    score = sum(criteria.values()) / len(criteria)
    passed = score >= config.PASS_THRESHOLD and min(criteria.values()) >= config.CRITERION_FLOOR
    # Feedback lists the weakest criteria first; anything under the pass bar is worth fixing.
    weak = sorted(
        (p, name) for name, p in criteria.items() if p < max(config.PASS_THRESHOLD, config.CRITERION_FLOOR)
    )
    lines = [f"- {name} ({p:.2f}): {RUBRIC[name][1]}" for p, name in weak]
    feedback = "\n".join(lines) if lines else "Meets every criterion."
    return Verdict(passed=passed, score=round(score, 4), criteria=criteria, feedback=feedback)


async def judge(*, campaign: str, contact: dict, subject: str, body: str) -> Verdict:
    result = await evaluate(
        ai.get_model(config.JUDGE_MODEL),
        {
            "campaign_brief": campaign,
            "contact_profile": contact,
            "email": {"subject": subject, "body": body},
        },
        QUESTIONS,
        output_type=Answers,
    )
    criteria = {name: round(getattr(result.value, name).noul, 4) for name in RUBRIC}
    return verdict_from_scores(criteria)
