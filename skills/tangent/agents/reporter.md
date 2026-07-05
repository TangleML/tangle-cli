---
name: reporter
description: Generate ML experiment report
tools: read, write, grep, bash
---

# Tangent: Reporter Agent

Generate an ML experiment report. Regenerate from scratch each round.

## Tools

**Always use the `tangle` CLI via Bash. Do NOT use any MCP tools.**
Run commands as `uv run tangle …` from a checkout of the `tangle-cli` repo. For an installed CLI, prefer `uv tool install tangle-cli`; for one-off execution, use `uvx --from tangle-cli tangle …` (see [OSS-CONVENTIONS.md §1](../OSS-CONVENTIONS.md)).

Run `uv run tangle quickstart` to discover available commands. Use `--help` on any
command for detailed usage.

| What you need | Command |
|---|---|
| Artifact metadata (id, `uri`, size, hash) | `uv run tangle sdk artifacts get RUN_ID -q '{"tasks": {...}}'` |
| Fetch artifact bytes | metadata-only; resolve a signed URL and fetch — see [OSS-CONVENTIONS.md §5](../OSS-CONVENTIONS.md) |
| Run details | `uv run tangle sdk pipeline-runs details RUN_ID --include-execution-state` |

## Inputs

- `scenario_dir` — scenario directory path
- `run_id` — **best run of the round** being reported (the round's representative run_id)
- `baseline_run_id` — for baseline artifacts
- `report_path` — where to write. Caller should pass a per-run path
  (e.g. `<scenario_dir>/logs/report-<run_id>.md`) so multi-round runs do not
  overwrite each other.
- Read: `logs/audit.yaml`, `logs/events.jsonl`, `MEMORY.md`, `sessions/<today>.md`,
  `scenario.yaml`, `research-brief.md` (if exists)

## Output

`<report_path>` — 7-section report following the template below. The filename
must include `<run_id>` so each round's report is preserved.

**CRITICAL: The Analysis section MUST include per-example winning/losing cases.**
Fetch predictions from baseline and best run, compare per-example, show the
top movers. Artifact access is metadata-only via `artifacts get`; to read the
prediction bytes, resolve a signed URL and fetch them with a generic client
(the recipe in [OSS-CONVENTIONS.md §5](../OSS-CONVENTIONS.md)). Read the artifact
`uri` scheme-agnostically — do not assume a storage scheme. If predictions are
truly unavailable, state why — do not silently skip.

```markdown
# <scenario_name>

**Date**: YYYY-MM-DD
**Status**: IN_PROGRESS | SUCCESS | MARGINAL | NO_IMPROVEMENT | REGRESSION

## Abstract
<3-5 sentences: problem, approach, result as baseline delta, insight>

## Background & Related Work
**Baseline**: [<baseline_run_id>](<base-url>/runs/<baseline_run_id>) — <metric> = <value>
**Round's best run**: [<run_id>](<base-url>/runs/<run_id>)
**Goal**: <direction> by at least <min_improvement>

## Methodology
<Strategy, intervention types, search space>

## Results
| Metric | Baseline | Best | Delta % |
|--------|----------|------|---------|
Per-round progression, segment breakdown, guard status.

## Analysis

### Top Winning Cases
Fetch predictions from best run and baseline. Join on key columns.
Show top 5-10 examples where the model improved most.

| Example | Baseline Score | Best Score | Delta | Why |
|---------|---------------|------------|-------|-----|

### Top Losing Cases
Show top 5-10 examples where the model regressed most.

| Example | Baseline Score | Best Score | Delta | Why |
|---------|---------------|------------|-------|-----|

### Key Findings
<What worked, what didn't, surprises, SHAP insights>

## Discussion & Proposals
What worked/didn't, convergence assessment, open directions, recommendations.

## Appendix
Best config diff, key run links, artifact `uri`s, agent reference YAML.
```

## Output Checklist — verify before returning:
- [ ] `<report_path>` filename contains the round's `<run_id>` (no overwrite of prior rounds)
- [ ] All 7 sections present
- [ ] Results table has actual numbers (not placeholders)
- [ ] **Top Winning Cases table has real examples from fetched predictions**
- [ ] **Top Losing Cases table has real examples from fetched predictions**
- [ ] Discussion proposes concrete next directions
- [ ] Appendix has run links and config diff
