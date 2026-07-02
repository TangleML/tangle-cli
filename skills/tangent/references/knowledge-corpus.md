# Knowledge Corpus (Learnings)

Persistent record of what tangent has tried and learned, so future sessions and
other scenarios can reuse prior context.

This file is the canonical description of the learnings corpus. For the CLI
surface, auth, and the broader conventions it sits inside, see
[OSS-CONVENTIONS.md](../OSS-CONVENTIONS.md) (§6, Learnings corpus).

## Where the corpus lives

The corpus is a **local directory** by default, with an **optional shared tier**
backed by a HuggingFace dataset repo.

### Default — local corpus directory

The corpus directory is configurable via the `LEARNINGS_DIR` environment
variable. When unset it defaults to `$SCENARIO_DIR/learnings/`:

```
$LEARNINGS_DIR/                       # default: $SCENARIO_DIR/learnings/
└── <scenario_name>/
    ├── research-<run_id>.md          # research brief from Step 1 (one per round that runs research)
    └── learning-<run_id>.json        # final learning from Step 7 (one per completed round)
```

`<run_id>` is the Tangle pipeline-run ID returned by
`tangle sdk pipeline-runs submit`.

**Keying rules:**
- `research-<run_id>.md` — keyed by the **active run_id**:
  - Round 1: `active_run_id = baseline_run_id` (research happens before first submit)
  - Round 2+ re-research: `active_run_id = prior round's best_run_id` (the parent run
    that motivated the new research)
- `learning-<run_id>.json` — keyed by the round's **best_run_id** (the
  best-performing run of the round; in single-run rounds this is the only run).

### Shared tier — HuggingFace dataset repo (optional)

For a team-shared corpus (so learnings outlive a single checkout and are
visible across the team), push records to a HuggingFace dataset repo. This is
the OSS-native equivalent of a shared bucket and matches the backend's own
storage provider:

```
hf://datasets/<org>/<corpus>@main/<scenario>/<run_id>.json
```

Push with `huggingface_hub`:

```python
from huggingface_hub import HfApi

HfApi().upload_file(
    path_or_fileobj="learning.json",
    path_in_repo="<scenario>/learning-<run_id>.json",
    repo_id="<org>/<corpus>",
    repo_type="dataset",
)
```

The local corpus directory is the default; the HF dataset is an optional shared
tier layered on top. Use the same scenario/run_id keying scheme for both.

## When to record

- **Research brief** — Step 1 (Analyze), immediately after the researcher writes
  `$SCENARIO_DIR/research-brief.md`. Record once per round that runs research.
- **Final learning** — Step 7 (Decide), after the round's outcome is known. Always
  record, even on a regression — negative results are signal too.

## Record commands

```bash
# Step 1 — research brief
mkdir -p "$LEARNINGS_DIR/<scenario>"
cp "$SCENARIO_DIR/research-brief.md" \
   "$LEARNINGS_DIR/<scenario>/research-<run_id>.md"

# Step 7 — final learning
mkdir -p "$LEARNINGS_DIR/<scenario>"
cp "$SCENARIO_DIR/logs/learning-<run_id>.json" \
   "$LEARNINGS_DIR/<scenario>/learning-<run_id>.json"
```

If recording fails (e.g. the corpus directory is unwritable), log a
`learning_record_failed` event and keep going — the local copy under
`$SCENARIO_DIR/logs/` is the source of truth. A future session can retry the
record.

When using the shared HF dataset tier, the same resilience applies: if the
upload to the dataset repo fails, log `learning_record_failed`, keep the local
record, and retry later.

## learning.json shape

```json
{
  "scenario": "<scenario_name>",
  "run_id": "<run_id>",
  "session": "YYYY-MM-DD-<scenario_name>",
  "round": <N>,
  "baseline_run_id": "<baseline_run_id>",
  "hypothesis": "<one sentence, from Step 2>",
  "experiment_type": "<feature_selection|param_tuning|data_action|...>",
  "config_diff": { ... },               // diff vs baseline config
  "primary_metric": { "name": "...", "baseline": ..., "result": ..., "delta": ... },
  "all_metrics": { ... },               // full metric dict
  "outcome": "<SUCCESS|MARGINAL|NO_IMPROVEMENT|REGRESSION|FAILED>",
  "lesson": "<one-line takeaway, from Step 6 / MEMORY.md>",
  "next_direction": "<from Step 7 — what to try next>"
}
```

## Reading prior learnings

Step 0 may pull recent learnings for the same scenario into context:

```bash
ls -t "$LEARNINGS_DIR/<scenario>/" | head -20
```

Read individual files directly from the corpus directory. Don't bulk-load the
whole prefix — for a long-running scenario it grows without bound.

When the shared HF dataset tier is in use, list and fetch per-scenario records
with `huggingface_hub` (`HfApi().list_repo_files(...)` + `hf_hub_download`)
rather than cloning the entire dataset.
