CREATE TABLE IF NOT EXISTS bus_runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    profile TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bus_skill_status (
    run_id TEXT NOT NULL,
    skill TEXT NOT NULL,
    status TEXT NOT NULL,
    code TEXT,
    message TEXT,
    counts_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (run_id, skill)
);

CREATE TABLE IF NOT EXISTS bus_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    producer_skill TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    artifact_key TEXT NOT NULL DEFAULT 'default',
    payload_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(run_id, producer_skill, artifact_type, artifact_key)
);
CREATE INDEX IF NOT EXISTS idx_bus_artifacts_lookup
    ON bus_artifacts(run_id, artifact_type, artifact_key, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_bus_artifacts_producer
    ON bus_artifacts(run_id, producer_skill, updated_at DESC);
