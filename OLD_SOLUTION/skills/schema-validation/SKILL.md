# Schema Validation

Use this skill to validate the minimal SQLite schema for the parcel analysis pipeline.

Initialize a clean split workspace:

```powershell
python skills/schema-validation/scripts/init_split_workspace.py --replace
```

Run:

```powershell
python skills/schema-validation/scripts/validate_schema.py --db-path data/analysis_workspace.sqlite --canon-db-path data/canon_workspace.sqlite --require-linked-parcels --require-geometry-features --require-visual-features
```

Add `--require-price-observations` only when the current run is expected to have RCN price observations.

Migrate old table names to prefixed names:

```powershell
python skills/schema-validation/scripts/migrate_table_prefixes.py --db-path data/analysis_workspace.sqlite --clear-helpers
```

Clear session helper tables after presentation artifacts are written:

```powershell
python skills/schema-validation/scripts/cleanup_helper_tables.py --db-path data/analysis_workspace.sqlite
```

## Rules

- This is a validation skill, not an analysis skill.
- Read only the final pass/fail output.
- Do not invent a larger validation framework unless explicitly requested.
- Never delete rows from `canon_*` tables.
- Only `helper_*` tables are cleared after a session.
- `data/canon_workspace.sqlite` is the Git-synced canonical DB.
- `data/analysis_workspace.sqlite` is local rebuildable workspace and must stay out of Git.
