# dspy-self-learn

A self-improving email generator. Each contact runs through a durable Vercel
Workflow that drafts an email with a DSPy program, has it judged by
[Jev](https://vercel.com/ai-gateway/models/jev-1.14.0), retries with the
judge's feedback, and then sends or flags it. Every 25th completed contact
starts a second workflow that runs GEPA on the accumulated feedback. If the
optimized program beats the active one on held-out contacts, it becomes the
new active version.

## Layout

| Path | Purpose |
| --- | --- |
| `app/main.py` | FastAPI: submit a contact, inspect contacts, versions, optimizations, stats |
| `public/` | Dashboard (served at `/`): contacts, failures, optimizations with prompt diffs |
| `app/workflows/email.py` | Per-contact workflow: generate → judge (×3) → send/flag → count |
| `app/workflows/optimize.py` | GEPA optimization workflow and promotion |
| `app/program.py` | DSPy signature/module, LM setup, versioned storage (Vercel Blob) |
| `app/judge.py` | Jev rubric judge via the AI SDK for Python (`ai.ops.experimental.evaluate`) |
| `app/optimize.py` | GEPA metric, dataset building, head-to-head scoring |
| `app/db.py` | All Postgres reads/writes |
| `schema.sql` | Postgres schema, applied when the Docker volume is created |
| `app/config.py` | Env-driven settings |

## How it works

**Email workflow** (`email_contact`)
1. `get_active_version` reads the active program version. Workflows replay
   recorded step results, so the run keeps this version even if a new one is
   promoted mid-run.
2. `generate_draft` → `evaluate_draft`, up to `MAX_ATTEMPTS` times. Failed
   attempts feed the previous draft and the judge's feedback into the next
   generation.
   Each attempt is appended to the contact's `attempts` JSONB array.
3. If the email passes, `send_email` logs it. Otherwise the contact is flagged.
4. `finish_contact` takes the next value of the `completions` sequence. Every
   `OPTIMIZE_EVERY`-th completion runs `start_optimization`.

**Judge.** Jev answers seven yes/no rubric questions (personalized, relevant
value, clear CTA, concise, tone, subject line, accurate), each returned as a
probability. An email passes when the mean is ≥ `PASS_THRESHOLD` and no
criterion is below `CRITERION_FLOOR`. Low-scoring criteria become written
feedback, used for regeneration and as GEPA's reflection feedback.

**Optimization workflow** (`optimize_program`)
1. Collects first attempts: the latest `OPTIMIZE_RECENT` plus up to
   `OPTIMIZE_HISTORY` older ones, split evenly between passes and failures.
2. Holds out 30% as a test set and runs GEPA on the rest. GEPA scores fresh
   generations with the Jev metric.
3. Scores the active and candidate programs on the held-out set. If the
   candidate wins by at least `MIN_IMPROVEMENT`, it is uploaded to Blob and
   inserted into `program_versions`. The highest version is always the
   active one.

## Running locally

```bash
docker compose up -d   # Postgres 17 on localhost:5432 (works with OrbStack)
uv sync
vercel link            # once
vercel env pull        # AI_GATEWAY_API_KEY / OIDC, BLOB_READ_WRITE_TOKEN
vercel dev
npx workflow web       # inspect runs
```

```bash
curl -X POST localhost:3000/api/contacts -H 'content-type: application/json' -d '{
  "email": "dana@example.com", "name": "Dana Lee", "company": "Northwind",
  "role": "VP Support", "notes": "Hiring 10 support agents this quarter"
}'
curl localhost:3000/api/stats
```

`schema.sql` only runs when the volume is first created. After changing it,
reset with `docker compose down -v && docker compose up -d`.

Without `BLOB_READ_WRITE_TOKEN`, program versions are written to
`.data/programs/` so the loop still works offline.

## Configuration

| Var | Default |
| --- | --- |
| `GENERATION_MODEL` | `deepseek/deepseek-v4.1-flash-1017` |
| `REFLECTION_MODEL` | `deepseek/deepseek-v4.1-flash-1017` (GEPA reflection; a stronger model helps) |
| `JUDGE_MODEL` | `typesafe-ai/jev-1.14.0` |
| `CAMPAIGN_BRIEF` | sample B2B brief: set this to your real offer |
| `MAX_ATTEMPTS` / `PASS_THRESHOLD` / `CRITERION_FLOOR` | `3` / `0.8` / `0.5` |
| `OPTIMIZE_EVERY` / `OPTIMIZE_RECENT` / `OPTIMIZE_HISTORY` | `25` / `25` / `75` |
| `GEPA_AUTO` / `GEPA_MAX_METRIC_CALLS` | `light` / unset (`.env.example` sets 30; `light` allows ~470) |
| `GEPA_VALSET_SIZE` | `8` (contacts GEPA scores promising prompts on) |
| `MIN_IMPROVEMENT` | `0.01` |
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/emails` |
| `DATA_DIR` | `.data` (local program storage) |

## Caveats

- **No idempotency (demo).** Submitting a contact twice starts two runs, and
  a step that fails after writing can retry and write again (for example, a
  duplicate attempt entry).
- **Sending is stubbed.** `send_email` only logs.
- **Deploying:** point `DATABASE_URL` at a hosted Postgres (e.g. Neon) and
  apply `schema.sql` to it with `psql`.
- **GEPA runs in one step.** `optimize_and_compare` can take many minutes.
  Keep it within your function's max duration by lowering `GEPA_AUTO` or
  setting `GEPA_MAX_METRIC_CALLS`.
- The AI SDK's `evaluate` API is experimental and may change.
