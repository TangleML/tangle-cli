# Event Log

Append-only structured event log. One JSON object per line.
Path: `<scenario_dir>/logs/events.jsonl`

## Step Name Mapping

| Step | Name |
|------|------|
| 0 | `initialize` |
| 1 | `analyze` |
| 2 | `hypothesize` |
| 3 | `submit` |
| 4 | `monitor` |
| 5 | `evaluate` |
| 6 | `synthesize` |
| 7 | `decide` |

## Event Types

**step_transition**
```json
{"type": "step_transition", "ts": "2025-01-15T10:30:00Z", "step": 1, "step_name": "analyze", "status": "complete", "round": 1}
```

**tool_call** — log the call, but do NOT save the output (it bloats the log and is
rarely re-read). Only record tool name, args, and duration.
```json
{"type": "tool_call", "ts": "2025-01-15T10:31:00Z", "tool": "get_run_details", "args": {"run_id": "abc123"}, "duration_ms": 1200}
```

**hypothesis**
```json
{"type": "hypothesis", "ts": "2025-01-15T10:32:00Z", "round": 1, "experiment_type": "parameter_tuning", "description": "Sweep learning rate 0.03-0.15", "expected_outcome": "+0.5% target metric", "num_runs": 4, "budget_impact": 4}
```

**run_submit**
```json
{"type": "run_submit", "ts": "2025-01-15T10:33:00Z", "round": 1, "run_id": "abc123", "experiment_type": "parameter_tuning", "label": "<short-description>", "config_changes": {"learning_rate": 0.05}, "annotations": {"session": "2025-01-15-<scenario_name>", "round": "1"}, "pipeline_args": {}}
```

**run_complete**
```json
{"type": "run_complete", "ts": "2025-01-15T11:15:00Z", "round": 1, "run_id": "abc123", "target_metric": 0.4523, "guard_metrics": {"metric_a": 0.412}, "all_metrics": {}, "vs_baseline": 0.0023, "vs_best": 0.0023}
```

**run_failed**
```json
{"type": "run_failed", "ts": "2025-01-15T11:10:00Z", "round": 1, "run_id": "def456", "failure_type": "INFRA", "failed_task": "<TaskName>", "error_summary": "Pod evicted", "resolution": "retry", "retry_run_id": "ghi789", "snapshot_path": "logs/failures/def456.md"}
```

**analysis**
```json
{"type": "analysis", "ts": "2025-01-15T11:20:00Z", "round": 1, "run_id": "abc123", "analysis_type": "error", "findings": ["Segment X regressed 2%", "Segment Y improved 1.5%"], "proposed_direction": "Try feature pruning for weak segment features"}
```

**finding_promoted**
```json
{"type": "finding_promoted", "ts": "2025-01-15T11:25:00Z", "round": 1, "finding": "LR=0.05 optimal for this model size", "source_run_id": "abc123"}
```

**round_end**
```json
{"type": "round_end", "ts": "2025-01-15T11:30:00Z", "round": 1, "best_run_id": "abc123", "best_metric": 0.4523, "improvement_vs_baseline": 0.0023, "improvement_vs_prev_best": 0.0023, "budget_remaining": 11, "num_runs_this_round": 4}
```

**memory_compaction**
```json
{"type": "memory_compaction", "ts": "2025-01-15T11:31:00Z", "lessons_before": 12, "lessons_after": 8, "tokens_before": 3200, "tokens_after": 2400, "archived_to": "sessions/2025-01-15.md"}
```

**research_complete**
```json
{"type": "research_complete", "ts": "2025-01-15T10:15:00Z", "tracks_completed": ["shipping", "code", "baseline", "issues", "literature", "data"], "tracks_skipped": ["slack"], "brief_path": "research-brief.md", "directions_count": 7, "duration_seconds": 180}
```

**report_generated**
```json
{"type": "report_generated", "ts": "2025-01-15T12:00:00Z", "report_path": "case_studies/2025-01-15-<slug>.md", "outcome": "SUCCESS", "baseline_metric": 0.45, "best_metric": 0.458, "improvement_pct": 1.78, "total_runs": 12, "total_rounds": 4}
```

**learning_record_failed** — emitted when a write to the learnings corpus fails
(disk, network, quota, or permissions on the shared tier). The local file is the
source of truth; a future session can retry. `artifact` is `"research_brief"` or
`"learning"`; `key_run_id` is the run_id used to key the corpus path (active_run_id
for research, best_run_id for learning). `corpus_uri` is the destination that
failed — under the default local tier this is a `LEARNINGS_DIR` path, under the
optional shared tier an `hf://datasets/...` URI.
```json
{"type": "learning_record_failed", "ts": "2025-01-15T12:05:00Z", "round": 1, "artifact": "learning", "key_run_id": "abc123", "local_path": "logs/learning-abc123.json", "corpus_uri": "hf://datasets/<org>/<corpus>@main/<scenario>/learning-abc123.json", "error_summary": "403 Forbidden: caller is not allowed to write to this dataset repo"}
```

## jq Examples

```bash
# All events for round 2
jq 'select(.round == 2)' logs/events.jsonl

# All failures
jq 'select(.type == "run_failed")' logs/events.jsonl

# Target metric progression
jq 'select(.type == "run_complete") | {run_id, target_metric, vs_baseline}' logs/events.jsonl

# Promoted findings
jq 'select(.type == "finding_promoted") | .finding' logs/events.jsonl

# Budget tracking per round
jq 'select(.type == "round_end") | {round, budget_remaining, improvement_vs_baseline}' logs/events.jsonl
```
