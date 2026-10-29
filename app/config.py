"""Runtime settings, read from the environment with local-dev defaults."""

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", ".data"))
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/emails"
)

# Generation goes through AI Gateway via LiteLLM's vercel_ai_gateway provider.
GENERATION_MODEL = os.environ.get("GENERATION_MODEL", "deepseek/deepseek-v4.1-flash-1017")
REFLECTION_MODEL = os.environ.get("REFLECTION_MODEL", "deepseek/deepseek-v4.1-flash-1017")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "typesafe-ai/jev-1.14.0")

MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))
# An email passes when its mean criterion probability clears PASS_THRESHOLD
# and no single criterion falls below CRITERION_FLOOR.
PASS_THRESHOLD = float(os.environ.get("PASS_THRESHOLD", "0.8"))
CRITERION_FLOOR = float(os.environ.get("CRITERION_FLOOR", "0.5"))

OPTIMIZE_EVERY = int(os.environ.get("OPTIMIZE_EVERY", "25"))
# How many recent / historical contacts feed each GEPA run.
OPTIMIZE_RECENT = int(os.environ.get("OPTIMIZE_RECENT", "25"))
OPTIMIZE_HISTORY = int(os.environ.get("OPTIMIZE_HISTORY", "75"))
GEPA_AUTO = os.environ.get("GEPA_AUTO", "light")
# Caps GEPA's scored generations. Defaults to 30 in code because `vercel dev`
# doesn't pass .env.local to the workflow worker; set to 0 to use GEPA_AUTO
# instead (light allows ~470).
GEPA_MAX_METRIC_CALLS = int(os.environ.get("GEPA_MAX_METRIC_CALLS", "30")) or None
# GEPA scores each promising prompt on this many contacts (separate from training).
GEPA_VALSET_SIZE = int(os.environ.get("GEPA_VALSET_SIZE", "8"))
# The candidate must beat the active program by at least this much to be promoted.
MIN_IMPROVEMENT = float(os.environ.get("MIN_IMPROVEMENT", "0.01"))

CAMPAIGN_BRIEF = os.environ.get(
    "CAMPAIGN_BRIEF",
    "We help B2B teams cut support costs with an AI triage assistant. "
    "Goal: book a 20-minute intro call. Sender: Alex from Acme.",
)


def gateway_api_key() -> str | None:
    return os.environ.get("AI_GATEWAY_API_KEY") or os.environ.get("VERCEL_OIDC_TOKEN")
