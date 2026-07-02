# Step 2: Hypothesize

## Round 1: Present the Research Plan

The researcher already produced ranked directions with exact implementation steps.
**Present them directly — do not re-interpret or expand.** The research plan IS
the experiment plan. Your job is to present it clearly for user approval, not to
redesign it.

```
## Experiment Plan: <scenario_name>

**Target metric**: <name> (baseline: <value>, goal: <direction> by <min_improvement>)
**Budget**: <max_total_runs> runs, <max_rounds> rounds, <max_parallel_runs> parallel
**Guard metrics**: <list with thresholds>

### Research directions (from research-brief.md, in order)
<paste the Recommended Experiment Directions section from research-brief.md>

### Round 1 experiment (= research direction #1)
<paste the #1 direction's Implementation steps verbatim>
```

Do NOT proceed without explicit user approval.

## Round 2+: Evidence-Driven Hypothesis

State clearly:
- **What** you're changing and **why** (cite Step 5 Direction Proposal or MEMORY.md)
- **Expected outcome** and how you'll measure it
- **How many runs** — justify why not fewer

**Design minimal experiments.** If evidence points to a clear fix, test it
directly with 1-2 runs. Don't sweep when you already know the answer.

## Gate — do NOT proceed to Step 3 until all pass:
- [ ] Round 1: research directions presented verbatim from research-brief.md
- [ ] Round 1: user explicitly approved
- [ ] Round 2+: evidence cited, run count justified
- [ ] Budget impact calculated (won't exceed remaining budget)
- [ ] `step_transition` and `hypothesis` events logged
- [ ] **Reload + review**: re-read this step file and the current `research-brief.md`; agent confirms it remembers them
