# Candidate Ranking

Use this skill to rank parcel candidates already linked in SQLite.

The ranking is generic. The model chooses runtime thresholds and weights from the user goal, then passes them as CLI parameters. Do not create ad hoc ranking loops.

Run:

```powershell
python skills/candidate-ranking/scripts/rank_candidates.py --run-id "<run_id>" --db-path data/analysis_workspace.sqlite --min-area-m2 1500 --max-area-m2 12000 --max-elongation 8 --require-visual --limit 20 --output results/analysis_<analysis_id>/ranked_candidates_<run_id>.json
```

Optional weights:

```powershell
--target-area-m2 5000 --area-weight 2 --compactness-weight 1 --elongation-weight 0.8 --green-weight 1.4 --dark-weight 0.6 --bright-weight 0.4
```

## Rules

- Source of truth is SQLite.
- Do not read raw parcel rows or generated large JSON.
- Do not hardcode business goals in the script name or code.
- Use runtime thresholds and weights for the current research goal.
- The output must include `funnel_counts` so the agent can report the inverted pyramid.
- The final answer reads only the compact summary and artifact path.
