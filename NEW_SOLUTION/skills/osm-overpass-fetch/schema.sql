CREATE TABLE IF NOT EXISTS skill_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS skill_cache (
    cache_key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS skill_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    detail_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
