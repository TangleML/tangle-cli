# Tangent Agent-Skills — Open-Source Port

This directory is an **open-source port of the Tangent agent-skill bundle**: a set of
instructions that let an autonomous coding agent run ML pipeline-tuning experiments end to
end against the [`tangle-cli`](https://github.com/TangleML/tangle-cli) CLI (binary
`tangle`, the `tangle sdk` / `tangle api` surfaces).

It is a faithful carbon copy of the original bundle with **every infrastructure-specific
reference removed and re-expressed against the public CLI**. The substitutions are not ad
hoc — they are governed by a single binding contract, [`OSS-CONVENTIONS.md`](./OSS-CONVENTIONS.md),
which every file in this tree inherits.

## What this is for

The bundle drives an 8-step autonomous loop (initialize → analyze → hypothesize → submit →
monitor → evaluate → synthesize → decide) plus a roster of specialized subagents. Point a
capable agent harness at `SKILL.md` as the entry index and it can: read a tuning scenario,
form a hypothesis, edit a pipeline config, submit a run, monitor it, pull artifacts and logs,
evaluate the result, and record a durable learning — looping until the scenario's goal is met.

## Layout

```
SKILL.md                     ← entry index: roster, the 8-step loop, invocation rules
OSS-CONVENTIONS.md           ← the binding port contract (CLI surface, auth, artifacts,
                               logs, learnings, annotations, resolved defaults). Read first.
PORT-README.md               ← this file
agents/                      ← 7 subagents the loop spawns
  builder.md  debugger.md  reporter.md  researcher.md
  reviewer.md  scenario-builder.md  auth-wizard.md
references/                  ← the loop steps + topic references
  step-0-initialize.md … step-7-decide.md
  tangle-tools.md  setup.md  secrets.md  data-sources.md
  event-log.md  knowledge-corpus.md  iterating-on-runs.md  uploading-artifacts.md
  example-scenarios/         ← worked scenarios on PUBLIC datasets
    INDEX.md  01-*.md  02-*.md
```

## How to invoke

Normal usage runs the **published `tangle-cli` package**: install persistently with `uv tool install tangle-cli`, then run `tangle …` / `tangle-cli …`, or use `uvx --from tangle-cli tangle …` for one-off commands. Local checkout invocation (`uv run tangle …`) is only for validating changes inside the `tangle-cli` repo. Elsewhere, `tangle` still appears as a bare command noun in prose and in the §2 CLI map (the naming-surface exception). See `OSS-CONVENTIONS.md` §1.

## What changed from the internal bundle

The internal original was wired to closed, internal-only infrastructure. Each of those couplings was
replaced with the project's open, backend-agnostic equivalent:

| Internal coupling (removed)        | Open-source replacement                                              |
| ---------------------------------- | -------------------------------------------------------------------- |
| Internal CLI wrapper + env shim    | `tangle …` / `tangle-cli …` from the published package, no environment shim |
| Cloud-object-storage artifact URIs | Scheme-agnostic artifact `uri` (e.g. `hf://…`); fetch via signed URL |
| Hosted log-search backend          | `tangle sdk pipeline-runs logs` + launcher-native system events      |
| Hard-coded internal API endpoint   | `--base-url` / `TANGLE_API_URL`                                      |
| Internal auth brokers / SSO        | `--token` / `--auth-header` / `-H` (+ `TANGLE_API_*` env vars)       |
| Cloud bucket "learnings" corpus    | Local `LEARNINGS_DIR`, optional shared HuggingFace-dataset tier      |
| Dashboard run-attribution tags     | Dropped (generic user annotations retained)                          |

A few source files were **dropped rather than ported**, by design:

- **`agents/uploader.md`** — its portable guidance was folded into
  `references/uploading-artifacts.md`; no surviving file spawns it.
- **`references/containerized-component-iteration.md`** — built on a container-build path that
  is a not-implemented stub in the OSS CLI.
- **The 15 numbered case studies under `example-scenarios/`** — these were internal/customer
  scenarios. They are replaced with self-contained synthetic scenarios on **public datasets**.

## Provenance & the security contract

This port treats internal references as a **release blocker**, not cosmetic cleanup: a leaked
internal hostname, project id, bucket URI, or credential path in open-source files is an attack
surface. `OSS-CONVENTIONS.md` §9 holds the full strip-list; every ported file was audited
against it. If you extend this bundle, run that audit on your additions before publishing.

## Intended destination

This tree is standalone and self-referential (all cross-references are relative). It is meant
to be dropped into the `tangle-cli` repo as an agent-skill bundle — for example at
`tangle-cli/skills/tangent/` (or wherever that repo loads agent skills from) — so the
harness can load `SKILL.md` as the index.
