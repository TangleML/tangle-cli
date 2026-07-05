# Step 3: Submit

**Never exceed budget.** Infra retries and analysis actions don't count against it.
Maximize concurrency — fill all `scenario.budget.max_parallel_runs` slots.

| Budget | Runs/Round | Strategy |
|--------|-----------|----------|
| Tight (< 10) | 2-3 | Sequential refinement |
| Moderate (10-20) | 3-5 | Sweep primary dimension, refine |
| Generous (20-50) | 5-8 | Parallel sweeps |

## Submission

Refer to `scenario.experiment_actions` and the scenario's `experiment-playbook.md`
skill for the specific config changes per experiment type.

Analysis actions don't consume budget — run locally, update session log.

## Pipeline Submission

**Follow the Submission Rules in `references/tangle-tools.md`** — hydrate is the
default, `submit` never waits, and run args go through `--arg` / `--args-json`.

Run arguments are passed on the command line, not via a `-f config.yaml` file
(there is no `-f` flag). Use repeated `--arg K=V` for individual values, or
`--args-json '<JSON>'` (or `--args-json @args.json`) for a structured payload.
Auto-loop bookkeeping rides along as generic `--annotation K=V` pairs:

```bash
# experiment args + auto-loop annotations, assembled on the submit line
tangle sdk pipeline-runs submit $SCENARIO_DIR/pipeline.yaml \
  --arg <experiment_arg>=<value> \
  --annotation session=YYYY-MM-DD-<scenario_name> \
  --annotation round=<N> \
  --annotation type=<experiment_type> \
  --annotation label=<short-description>
```

If you want the bookkeeping written down before you submit, keep a plain
`$SCENARIO_DIR/run_config.yaml` note for yourself with the intended args and
annotation values — but the values still reach the run via `--arg` /
`--annotation`, not via a submit `-f` flag.

### Pre-submit checks (run ALL of these BEFORE `pipeline-runs submit`)

These checks must run *before* submission — once the pipeline is submitted, any
credential value or wrong reference is already persisted in the Tangle backend.
**Do not** reorder these after the submit command.

```bash
# 1. Credential-body scan (uses grep -lE so values never echo to stdout;
#    see also references/secrets.md and the "Do not cat/grep -n" warning below).
#    Patterns are sized to match real tokens (>=20 chars of entropy) so doc
#    comments mentioning the word "Bearer" or "sk-" don't false-positive.
LEAK_FILES=$(grep -lEi '(sk-[A-Za-z0-9_-]{20,}|xoxb-[0-9]+-[0-9]+-[A-Za-z0-9]+|EJ\[1:[A-Za-z0-9+/=]{20,}|Bearer [A-Za-z0-9_.-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|-----BEGIN [A-Z ]+PRIVATE KEY-----)' \
  $SCENARIO_DIR/pipeline.yaml 2>/dev/null)
if [ -n "$LEAK_FILES" ]; then
  echo "ERROR: credential-shaped string detected in: $LEAK_FILES"
  echo "      Open these files in an editor (do NOT cat/grep them); route the value through dynamicData.secret."
  exit 1
fi

# 2. Placeholder scan — catches REPLACE_ME_* / TODO / <ASK_USER:*> identifiers
#    that the agent left for the human to fill in (per the autonomous-mode
#    convention in references/secrets.md). These must be resolved before submit
#    or the pipeline will fail at runtime when the component tries to look up
#    a non-existent secret.
PLACEHOLDERS=$(grep -lE '(REPLACE_ME_|<ASK_USER:|TODO_FILL_IN_)' \
  $SCENARIO_DIR/pipeline.yaml 2>/dev/null)
if [ -n "$PLACEHOLDERS" ]; then
  echo "ERROR: unresolved placeholders in: $PLACEHOLDERS"
  echo "      Ask the human for the correct identifier (secret name, etc.) before submitting."
  exit 1
fi

# 3. Tangle-secret existence check — every `dynamicData.secret.name` referenced
#    in the pipeline must exist under the authenticating identity. A typoed or
#    not-yet-created name passes step 1 (it's not a credential body) but fails
#    at runtime, which wastes budget and confuses the user.
# Run the helper in a Python environment with PyYAML available. `uv run` is shown
# for scenario repos that manage dependencies with uv; this is not a Tangle CLI invocation.
uv run python3 - <<'PY' "$SCENARIO_DIR/pipeline.yaml"
import sys, yaml, subprocess, json
path = sys.argv[1]
spec = yaml.safe_load(open(path))
# Collect every dynamicData.secret.name reference, anywhere in the doc.
refs = set()
def walk(o):
    if isinstance(o, dict):
        s = o.get("dynamicData", {}).get("secret", {}).get("name") if isinstance(o.get("dynamicData"), dict) else None
        if isinstance(s, str): refs.add(s)
        for v in o.values(): walk(v)
    elif isinstance(o, list):
        for v in o: walk(v)
walk(spec)
if not refs:
    sys.exit(0)
# stderr=DEVNULL drops auth warnings; --log-type none drops info logs.
out = subprocess.check_output(
    ["uv","run","tangle","sdk","secrets","list","--log-type","none"],
    text=True, stderr=subprocess.DEVNULL,
)
existing = {s["secret_name"] for s in json.loads(out).get("secrets", [])}
missing = sorted(refs - existing)
if missing:
    sys.stderr.write(
        "ERROR: pipeline references Tangle secrets that don't exist under the "
        "authenticating identity: " + ", ".join(missing) + "\n"
        "      Ask the human to create them via: tangle sdk secrets create <NAME> --from-env <NAME>\n"
        "      (see references/secrets.md § 'If the secret is missing, ask the human to create it').\n"
    )
    sys.exit(1)
PY
if [ $? -ne 0 ]; then exit 1; fi
```

**Do not `cat`, `grep -n`, or otherwise echo the matching files to stdout to
"see what was matched"** — that re-leaks the value into your terminal,
agent transcript, and (if you re-run from shell history) shell history.
Open the suspect file in an editor instead and use the editor's search.

These three checks (credential-body + placeholders + secret-name existence) are
the complete pre-submit gate — every one must exit cleanly before the
`pipeline-runs submit` command below runs. If any of them prints `ERROR:`,
stop, surface the failure to the human, and do not submit.

### Submit (only after every pre-submit check above exited cleanly)

```bash
tangle sdk pipeline-runs submit $SCENARIO_DIR/pipeline.yaml \
  --arg <experiment_arg>=<value> \
  --annotation session=YYYY-MM-DD-<scenario_name> \
  --annotation round=<N> \
  --annotation type=<experiment_type> \
  --annotation label=<short-description>
```

Hydration is the default, so component versions are resolved as part of submit.
`submit` returns as soon as the run is created — it does **not** wait. To block
on completion, follow up with `tangle sdk pipeline-runs wait RUN_ID`.

### If you modified component source code:
1. Rebuild from your Python source, pointing at the image you built and pushed
   yourself: `tangle sdk components generate from-python source.py --image <registry/img:tag> --output component.yaml`
2. Update ref in pipeline YAML: swap `digest: ...` with `url: file://<path-to-component.yaml>`
3. Submit with the command above

See `agents/builder.md` for the full workflow.

## Post-Submission

`tangle sdk pipeline-runs submit` returns a **`run_id`** for each submitted
pipeline. Treat `run_id` as a first-class session concept:

1. Log to `sessions/YYYY-MM-DD.md` — record the `run_id` in a `## Run Log`
   section with timestamp, round, label, config diff. Order is chronological.
2. **Write to MEMORY.md "Active Runs" immediately** — `run_id`, run link
   (`<base-url>/runs/<run_id>`, or inspect via
   `tangle sdk pipeline-runs details RUN_ID`), label, config summary,
   timestamp. Survives session interruptions.
3. The `run_id` is what keys learnings records in Step 7
   (`learning-<run_id>.json` under `$LEARNINGS_DIR/<scenario>/`), so make sure
   it's recorded verbatim.

## Gate — do NOT proceed to Step 4 until all pass:
- [ ] **All three pre-submit checks (credential-body, placeholder, secret-name existence) exited cleanly BEFORE `pipeline-runs submit` ran**
- [ ] If source code was modified: component rebuilt and pipeline ref updated
- [ ] All runs submitted successfully (run IDs received)
- [ ] Each run annotated with session, round, type, label
- [ ] MEMORY.md "Active Runs" updated with every `run_id`
- [ ] Session log `Run Log` section updated with every `run_id`
- [ ] Budget check: total runs submitted ≤ remaining budget
- [ ] `step_transition` and `run_submit` events logged
- [ ] **Reload + review**: re-read this step file and `references/tangle-tools.md`; agent confirms it remembers them
