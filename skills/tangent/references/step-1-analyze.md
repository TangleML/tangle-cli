# Step 1: Analyze

Choose the highest-ROI experiment direction based on evidence.

## Round 1: Launch Researcher

**Skip research if** `research-brief.md` already exists in `$SCENARIO_DIR` (from a prior
session or the scenario builder). Read the existing brief and proceed to "Every Round."

Otherwise, **launch the researcher as a subagent using the Agent tool.** Do not just
read the file — you must actually invoke it as a foreground agent and wait for completion.
Skip only if user says "skip research" or `scenario.research.enabled` is explicitly `false`.

Read `agents/researcher.md` and pass its full content as the agent prompt, with
this task context appended:
```
---
Task context:
Research the <scenario_name> pipeline.
Scenario: <scenario.research section>
Baseline run_id: <baseline_run_id>
Parent run_id: <prior round's best_run_id, or "n/a — round 1">
Code paths: <code_paths>
Image roots: <image_roots>
Write brief to: <SCENARIO_DIR>/research-brief.md
Write priors to: <SCENARIO_DIR>/research-priors.txt
```

The `parent_run_id` is the round's *active run_id* for record keying — see the
researcher brief template. Round 1: no parent → key by `baseline_run_id`.

**If the researcher agent fails or times out**, do NOT block the experiment loop. Log
the failure, then fall back to the experiment types in `scenario.experiment_actions` and
MEMORY.md priors. Research is high-value but not blocking.

Then read `research-brief.md`. Your experiment choice MUST follow the top-ranked
research direction. Only fall back to generic parameter tuning if research
recommends it or all directions have been tried.

## Round 2+

Read Step 5 analysis findings from the previous round (session log / MEMORY.md).
The Direction Proposal tells you what to try next based on the data.

## Every Round

1. Read MEMORY.md — best config, budget, lessons
2. Read `scenario.yaml` for available experiment types
3. Use free analysis actions before expensive experiments

## Choosing Experiment Type

Choose the TYPE, not just the parameter:

| Experiment Type | Typical ROI | When to Use |
|----------------|-------------|-------------|
| Analysis (free) | Information | Always first |
| Feature selection | High | Dead features or feature dominance |
| Data actions | High | Noisy labels or train/eval misalignment |
| Parameter tuning | Medium-High | LR, capacity, regularization — first 2 rounds |
| Pipeline modifications | Medium | Pipeline topology is the bottleneck |

## Record research brief to the learnings corpus

After the researcher writes `research-brief.md`, record it in the local learnings
corpus. Key by the **active run_id** — for Round 1 that's `baseline_run_id`; for round 2+
re-research that's the prior round's `best_run_id` (the parent run that motivated
the new research pass).

```bash
# Round 1: active_run_id == baseline_run_id
# Round 2+: active_run_id == prior round's best_run_id
mkdir -p "$LEARNINGS_DIR/<scenario>"
cp \
  "$SCENARIO_DIR/research-brief.md" \
  "$LEARNINGS_DIR/<scenario>/research-<active_run_id>.md"
```

`LEARNINGS_DIR` defaults to `$SCENARIO_DIR/learnings/` and is env-overridable. If the
record fails, log a `learning_record_failed` event and keep going — local is the source
of truth. See `references/knowledge-corpus.md` for the corpus-directory layout and the
optional shared-tier (HuggingFace dataset repo) recipe.

## Gate — do NOT proceed to Step 2 until all pass:
- [ ] Round 1: **`research-brief.md` exists at `$SCENARIO_DIR/research-brief.md`** (verify with Read — if missing, researcher did not run)
- [ ] Research brief recorded at `$LEARNINGS_DIR/<scenario>/research-<active_run_id>.md` (Round 1: keyed by `baseline_run_id`; Round 2+: keyed by prior round's `best_run_id`) — or `learning_record_failed` event logged
- [ ] Round 2+: Step 5 Direction Proposal from previous round has been read
- [ ] MEMORY.md reviewed for budget and lessons
- [ ] Experiment direction chosen with **Phase A evidence** (not anchored on Phase B FYI context)
- [ ] `step_transition` event logged
- [ ] **Reload + review**: re-read this step file and `agents/researcher.md`; agent confirms it remembers them
