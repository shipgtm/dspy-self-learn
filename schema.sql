CREATE TABLE contacts (
    id              BIGSERIAL PRIMARY KEY,
    email           TEXT NOT NULL,
    profile         JSONB NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | sent | flagged
    program_version INTEGER,
    attempts        JSONB NOT NULL DEFAULT '[]',      -- [{subject, body, score, passed, feedback, criteria}]
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

-- Optimized programs. The highest version is the active one; version 0 is
-- the code-defined baseline and has no row.
CREATE TABLE program_versions (
    version         SERIAL PRIMARY KEY,
    location        TEXT NOT NULL,                    -- Blob URL or local path
    instructions    TEXT NOT NULL,
    scores          JSONB NOT NULL,                   -- {base, candidate, n_train, n_test}
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE optimization_runs (
    id              BIGSERIAL PRIMARY KEY,
    batch           INTEGER NOT NULL,
    status          TEXT NOT NULL,                    -- promoted | rejected | skipped
    result          JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every OPTIMIZE_EVERY-th value triggers an optimization.
CREATE SEQUENCE completions;
