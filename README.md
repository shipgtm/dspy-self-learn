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
| `app/main.py` | FastAPI: submit contacts, inspect contacts, versions, optimizations, stats |
| `app/workflows/email.py` | Per-contact workflow: claim → generate → judge (×3) → send/flag → count |
| `app/workflows/optimize.py` | GEPA optimization workflow and promotion |
| `app/program.py` | DSPy signature/module, LM setup, versioned storage (Vercel Blob) |
| `app/judge.py` | Jev rubric judge via the AI SDK for Python (`ai.ops.experimental.evaluate`) |
| `app/optimize.py` | GEPA metric, dataset building, head-to-head scoring |
| `app/db.py` | SQLite schema and all reads/writes |
| `app/config.py` | Env-driven settings |

## How it works

**Email workflow** (`email_contact`)
1. `claim_contact` marks the contact running and *pins* the active program
   version on the row. Retries and later steps use that version even if a new
   one is promoted mid-run.
2. `generate_draft` → `evaluate_draft`, up to `MAX_ATTEMPTS` times. Failed
   attempts feed the previous draft and the judge's feedback into the next
   generation.
3. If the email passes, `send_email` records it. Otherwise the contact is flagged.
4. `finish_contact` assigns a global completion number. Every
   `OPTIMIZE_EVERY`-th completion runs `trigger_optimization`.

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
   promoted with a compare-and-swap on the active version. A concurrent
   optimization therefore can't overwrite a newer winner.

**Idempotency**
- Contacts are keyed by `external_id`, or by email if there is no ID.
  Resubmitting a contact does not start a new run, and a contact is owned by
  one run ID.
- Attempts are keyed by `(contact_id, attempt)`. A retried generate step
  returns the stored draft instead of regenerating.
- The outbox is keyed by contact, so a contact cannot be sent twice.
- Completion numbers are assigned inside a `BEGIN IMMEDIATE` transaction and
  keyed by contact, so a retry gets the same number back.
- Optimization batches are keyed by `completion // 25` and record the run ID
  that owns them.

## Running locally

```bash
uv sync
vercel link            # once
vercel env pull        # AI_GATEWAY_API_KEY / OIDC, BLOB_READ_WRITE_TOKEN
vercel dev
npx workflow web       # inspect runs
```

```bash
curl -X POST localhost:3000/api/contacts -H 'content-type: application/json' -d '{
  "contacts": [{"email": "dana@example.com", "name": "Dana Lee", "company": "Northwind",
                "role": "VP Support", "notes": "Hiring 10 support agents this quarter"}]
}'
curl localhost:3000/api/stats
```

Without `BLOB_READ_WRITE_TOKEN`, program versions are written to
`.data/programs/` so the loop still works offline.

## Configuration

| Var | Default |
| --- | --- |
| `GENERATION_MODEL` | `anthropic/claude-sonnet-5` |
| `REFLECTION_MODEL` | `anthropic/claude-sonnet-5` (GEPA reflection; a stronger model helps) |
| `JUDGE_MODEL` | `typesafe-ai/jev-1.14.0` |
| `CAMPAIGN_BRIEF` | sample B2B brief: set this to your real offer |
| `MAX_ATTEMPTS` / `PASS_THRESHOLD` / `CRITERION_FLOOR` | `3` / `0.8` / `0.5` |
| `OPTIMIZE_EVERY` / `OPTIMIZE_RECENT` / `OPTIMIZE_HISTORY` | `25` / `25` / `75` |
| `GEPA_AUTO` / `GEPA_MAX_METRIC_CALLS` | `light` / unset |
| `MIN_IMPROVEMENT` | `0.01` |
| `DATA_DIR` / `DATABASE_PATH` | `.data` / `.data/app.db` |

## Caveats

- **SQLite is local-only.** Vercel functions have an ephemeral, per-instance
  filesystem, so on a deployment the counter, idempotency rows and active
  version would not persist or be shared. To deploy, swap `app/db.py` for a
  hosted SQLite (Turso/libSQL) or Postgres. All SQL lives in that one module.
- **Sending is stubbed.** `send_email` only writes to the `outbox` table.
  Replace it with a provider and pass the contact ID as the idempotency key.
- **GEPA runs in one step.** `optimize_and_compare` can take many minutes.
  Keep it within your function's max duration by lowering `GEPA_AUTO` or
  setting `GEPA_MAX_METRIC_CALLS`.
- The AI SDK's `evaluate` API is experimental and may change.
