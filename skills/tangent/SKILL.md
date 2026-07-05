---
name: tangent
description: ML experiment toolkit and autonomous agent. Say "tangent" for help, "tangent <subagent>" for a specific tool, or "tangent auto" for the full autonomous experiment loop. Requires the published Tangle CLI (`uv tool install tangle-cli` or `uvx --from tangle-cli tangle ...`) and a reachable Tangle backend.
allowed-tools: [Bash, Read, Write, Glob, Grep, Agent, dispatch]
---

# Tangent

Thin index. Every file linked here is the source of truth for its topic — read it
when you need it, don't paraphrase from this file.

For the CLI surface, invocation rule, auth, artifacts, learnings corpus, logs, and
annotations, the single source of truth is
[`OSS-CONVENTIONS.md`](OSS-CONVENTIONS.md). When any skill file (this one included)
needs a command, a flag, or an env var, it cites that document rather than
re-deriving it.

## First, every session

Skills live **in-repo** (checked into the `tangle-cli` repo) — there is no fetch or
bundle-refresh step. Relative cross-references in this skill (`agents/*.md`,
`references/*.md`) resolve directly on disk. Read
[`references/setup.md`](references/setup.md) to set up the CLI and auth.

Normal usage runs the published CLI: install persistently with `uv tool install tangle-cli` and use `tangle …`, or run one-off commands with `uvx --from tangle-cli tangle …`. If intentionally validating a local `tangle-cli` checkout, prefix examples with `uv run`. See [`OSS-CONVENTIONS.md`](OSS-CONVENTIONS.md) §1.

## Commands

- **`tangent`** — print the help block below.
- **`tangent <subagent>`** — read `agents/<subagent>.md` and follow it.
- **`tangent auto`** — run the autonomous loop. See [Auto Mode](#auto-mode).

```
Subagents:                         Agent file:
  tangent debugger                   agents/debugger.md
  tangent researcher                 agents/researcher.md
  tangent reporter                   agents/reporter.md
  tangent reviewer                   agents/reviewer.md
  tangent builder                    agents/builder.md
  tangent auth                       agents/auth-wizard.md
  tangent new-scenario               agents/scenario-builder.md

Automation:
  tangent auto       — Run full autonomous 8-step experiment loop
```

## References (read on demand)

| Topic | File |
|---|---|
| Setup / auth | [`references/setup.md`](references/setup.md) |
| Tangle CLI | [`references/tangle-tools.md`](references/tangle-tools.md) |
| Event log schema | [`references/event-log.md`](references/event-log.md) |
| Iterating on a run | [`references/iterating-on-runs.md`](references/iterating-on-runs.md) |
| Uploading artifacts → pipelines | [`references/uploading-artifacts.md`](references/uploading-artifacts.md) |
| Reusing data sources | [`references/data-sources.md`](references/data-sources.md) |
| Secrets & credentials (API keys, tokens) | [`references/secrets.md`](references/secrets.md) |
| Example scenarios | [`references/example-scenarios/INDEX.md`](references/example-scenarios/INDEX.md) |
| Knowledge corpus (learnings) | [`references/knowledge-corpus.md`](references/knowledge-corpus.md) |

## Tools

Always use the `tangle` CLI via Bash. Do **not** use any `tangle` MCP tools.
Run `tangle quickstart` to discover commands. See
[`references/tangle-tools.md`](references/tangle-tools.md).

Cancel a run: `tangle sdk pipeline-runs cancel RUN_ID`

Background execution: `dispatch`. Subagents: `agents/*.md`.

## Scenarios

A scenario is any repo with a Tangle pipeline. Contents: `scenario.yaml`, `MEMORY.md`,
`skills/`, `sessions/`, `logs/`, `pipeline.yaml`. No scenario? `tangent new-scenario`.
For inspiration: [`references/example-scenarios/INDEX.md`](references/example-scenarios/INDEX.md).

---

## Auto Mode

Autonomous MLE agent that iterates on an ML pipeline. Tunes parameters, selects
features, changes data, analyzes results, modifies pipelines.

### Memory & run_id

- **Long-term**: `MEMORY.md` — best config, lessons, session index. < 3000 tokens.
- **Short-term**: `sessions/YYYY-MM-DD.md` — daily log, append-only.
- **Active runs**: written to `MEMORY.md` on Step 3 submission, cleared on Step 5 completion.
- **`run_id`** is a first-class session concept. Every Tangle pipeline-run has one;
  the session log tracks them in order, and learnings recorded to the corpus are
  keyed by `<scenario>/<run_id>`. Always record the `run_id` returned by
  `tangle sdk pipeline-runs submit`. See the learnings corpus in
  [`references/knowledge-corpus.md`](references/knowledge-corpus.md) and
  [`OSS-CONVENTIONS.md`](OSS-CONVENTIONS.md) §6.

### Procedure

Read each step file only when you reach that step — do not pre-read all step files.
Execute the step, verify the gate checklist, do not proceed until every gate passes.
**Each step gate ends with a "reload skills + context" checkbox — actually re-read
the step file and active agent files at that point, don't rely on stale memory.**

| Step | Reference |
|------|-----------|
| 0 Initialize | [`references/step-0-initialize.md`](references/step-0-initialize.md) |
| — Load builder skills | [`agents/builder.md`](agents/builder.md) (after init, before the loop) |
| 1 Analyze | [`references/step-1-analyze.md`](references/step-1-analyze.md) |
| 2 Hypothesize | [`references/step-2-hypothesize.md`](references/step-2-hypothesize.md) |
| 3 Submit | [`references/step-3-submit.md`](references/step-3-submit.md) |
| 4 Monitor | [`references/step-4-monitor.md`](references/step-4-monitor.md) |
| 5 Evaluate | [`references/step-5-evaluate.md`](references/step-5-evaluate.md) |
| 6 Synthesize | [`references/step-6-synthesize.md`](references/step-6-synthesize.md) |
| 7 Decide | [`references/step-7-decide.md`](references/step-7-decide.md) |

Loop: Step 7 → Step 1 if not converged. Read `references/tangle-tools.md` and
`references/event-log.md` at Step 0 only.

### Extracting metrics

```python
value = metrics
for key in path.split("."):
    value = value[int(key) if key.isdigit() else key]
```
