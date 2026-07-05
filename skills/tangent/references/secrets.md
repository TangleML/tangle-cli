# Secrets & Credentials in Tangle Pipelines

Tangle has first-class secret management. Use it. **Never** inline an API key,
token, password, OAuth client secret, or any other credential into a pipeline
YAML, a component argument default, or a config file you submit.

This is a hard rule, not a soft preference. Pipeline YAML and pipeline-run
arguments are stored in plaintext in the Tangle backend, surface in run-detail
views, get copied by `tangle sdk pipeline-runs export`, get logged by the
runner, and end up in agent transcripts. A raw key pasted into an input node
*is a leaked credential*.

## The rule

When a pipeline needs a credential (API key, bearer token, OAuth secret, HF
token, etc.):

1. **Do not read the raw value yourself.** Do not paste it from a source repo,
   an encrypted secrets file, env vars, browser sessions, a chat thread, a
   vault, or anywhere else into the pipeline. Even if you can see it, treat it
   as poison.
2. **Use a Tangle secret reference instead.** Components consume the secret
   via `dynamicData.secret.name` in the pipeline YAML; Tangle resolves the
   value at task-launch time, inside the container, with no plaintext on the
   pipeline spec.
3. **The human creates / rotates the secret value**, via `tangle sdk
   secrets create ... --from-env VAR_NAME` (preferred — agent never touches
   the value). The agent's job is to identify *that a secret is needed*, *what
   to name it*, and *how to wire it through the pipeline*. Not to source the
   value.

If the credential is missing from the user's Tangle account, **stop and ask**.
Do not "temporarily" inline a value to unblock yourself.

## When does the agent halt? (UX model)

The agent does **not** halt mid-construction every time it sees a credential
argument. That would make scaffolding LLM/API pipelines miserable and would
break the autonomous loop outright. The actual halt boundary is at *submission*,
not at authoring. Concretely:

| Stage | What the agent does | Halt? |
|---|---|---|
| 1. Detect a secret is needed | Detection heuristics match (`*_KEY`, `*_TOKEN`, `dynamicData.secret`-typed input, etc.) | No — proceed |
| 2. Wire it into the pipeline YAML | `dynamicData.secret: { name: "X" }`. **Never the value.** | No — proceed |
| 3. Validate the pipeline | `tangle sdk pipelines validate` — passes without the value (it only checks structure) | No — proceed |
| 4. **Submission boundary** | Run the pre-submit credential-grep guard from [`step-3-submit.md`](step-3-submit.md). If the referenced secret doesn't yet exist under the running account, surface a copy-pasteable `tangle sdk secrets create --from-env` command for the human. | **Yes** — hard halt until the human confirms the secret exists |
| 5. After human creates the secret | Resume: `pipeline-runs submit ... --hydrate` | — |

The agent's job at construction time is to produce a complete, reviewable
artifact (pipeline YAML + scaffolding + a list of exactly which secrets the
human still needs to create). The agent's job at submit time is to refuse to
submit if any credential value is inlined or any referenced secret is missing.

### Why halt at submit, not construction?

- **Validation doesn't need the value.** A pipeline file with
  `dynamicData.secret: { name: "FOO" }` validates and is safe to share,
  commit to a repo, paste in a chat thread. The leak risk only materializes
  when a *value* enters the pipeline spec or run arguments — which only happens
  at submit.
- **The human gets a full artifact to review.** Half-finished scaffolds plus
  an interactive prompt are worse UX than a complete pipeline plus a clear
  "run this command before submitting" handoff.
- **The autonomous loop would deadlock otherwise.** It runs unsupervised;
  halting on every credential reference per round means no LLM-using scenario
  ever advances past round 1.

### When the agent *should* halt during construction

One legitimate construction-time halt: when the agent doesn't know the
correct secret *name* and would have to guess. Identifier names are not
credentials, but a wrong name causes a runtime "secret not found" failure
that wastes the user's time and budget.

- **Interactive session** (human in the loop, e.g. running the builder, the
  scenario-builder, or a one-off prompt): if the secret name is not given by
  the user and not unambiguously derivable, **halt and ask**. Do not guess
  identifier names from your training data or from grepping repos.
- **Autonomous session** (mid-loop): do not halt mid-round. Use an explicit
  placeholder like `"REPLACE_ME_<input_name>"` and let the submit-time gate
  catch it. The gate fails fast and surfaces the unmet prerequisite; this is
  preferable to a hung loop.

Distinguish the two by checking the active-runs state in `MEMORY.md`, the
presence of `scenario.yaml`, and whether the entry point was the autonomous
loop vs a one-shot prompt. When in doubt: ask.

### What "halt and ask" looks like in practice

Good:

> I'm wiring a task that needs an `api_key`, and I need to know the **name** of
> the Tangle secret to reference (not the key value itself).
>
> What name should I use? If you don't have one yet, you'll need to create the
> Tangle secret first, then tell me the name.
>
> I'll wait — I won't guess.

Bad (do not do):

> I found a key in a source repo — using that. (❌ sourcing)
> I'll default the secret name to something that looks right. (❌ guessing)
> Pasting the API key value as `constantValue` for now — you can rotate later.
> (❌ **never**)

## Detection — when does this rule fire?

If an input/argument is described as, named like, or behaves like any of:

- `*_API_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, `*_KEY`, `bearer`,
  `authorization`, `client_secret`, `private_key`, `webhook_signing_secret`
- A header value containing `Bearer …`, `Basic …`, or `Authorization: …`
- An HF token, an OpenAI/Anthropic/Google/proxy LLM key, a chat bot token,
  a Git host PAT, or any other encrypted/managed credential value
- Anything the source repo reads from `os.environ`, an encrypted secrets
  store, or `dynamicData.secret` itself

…then it is a secret. Route it through `dynamicData.secret`.

## The end-to-end workflow

### 1. Discover what secrets exist

```bash
tangle sdk secrets list
```

Output lists `secret_name`, `updated_at`, optional `expires_at`, optional
`description`. The secret **value is never returned** — by Tangle or by you.

Secrets are **identity-scoped** — they belong to the authenticating identity
that created them. See [Identity scoping](#identity-scoping) below.

### 2. If the secret is missing, ask the human to create it

Surface a concrete command for the human to run. Use `--from-env` so the
value never enters the pipeline YAML or your context — **but be careful
about how the env var itself is set**, because a naive `export VAR='value'`
is recorded verbatim in shell history (`~/.bash_history`, `~/.zsh_history`)
and any terminal recording / session capture.

The safe pattern is `read -rs` (silent read from stdin, never echoed,
never stored in history):

```bash
# Human runs these — agent does not source MY_API_KEY itself.
# `read -rs` reads stdin silently; the value is never echoed and never
# enters shell history (only `read -rs MY_API_KEY` is recorded, not the value).
read -rs MY_API_KEY              # paste the value, press Enter (no echo)
export MY_API_KEY
tangle sdk secrets create MY_API_KEY \
  --from-env MY_API_KEY \
  --description 'Used by <pipeline> for <purpose>'
unset MY_API_KEY                  # clear it from the current shell
```

**Anti-pattern — do not propose this form**:
```bash
export MY_API_KEY='…paste value here…'   # ❌ value lands in shell history
```
Even with `HISTCONTROL=ignorespace` (prepend a space) or `set +o history`,
relying on a per-shell config is fragile. `read -rs` works the same way
everywhere and is the recommended form.

Multi-secret batch (`secrets_config.yaml`):

```yaml
_defaults:
  description: "Managed for <pipeline>"
configs:
  - secret_name: OPENAI_API_KEY
    from_env: OPENAI_API_KEY
  - secret_name: HF_TOKEN
    from_env: HF_TOKEN
```

```bash
tangle sdk secrets create --config secrets_config.yaml
```

**Do NOT** propose `tangle sdk secrets create NAME --value 'sk-…'` with
a value you pulled from somewhere. The `--value` / `-v` flag exists for humans
typing at a prompt, not for agents shuffling credentials between systems.
Prefer `--from-env` / `-e` everywhere.

### 3. Wire the secret into the pipeline YAML

If you're unsure *which* argument on a published component consumes the
credential — or how it expects to receive it — inspect the component's schema
first rather than guessing:

```bash
tangle sdk published-components inspect "<component name>"
```

That shows each input and its type, so you can tell which argument is
credential-shaped and wire the secret onto the right one.

On the component argument that consumes the credential, replace the literal
value with a `dynamicData.secret` reference. The `name` must match exactly
what `tangle sdk secrets list` shows (case- and space-sensitive):

```yaml
tasks:
  call_llm:
    componentRef:
      name: "LLM Inference"
    arguments:
      prompt:
        taskOutput:
          taskId: build_prompt
          outputName: prompt
      api_key:
        dynamicData:
          secret:
            name: "OPENAI_API_KEY"   # ← exactly as listed by `tangle sdk secrets list`
      base_url:
        constantValue: "https://api.openai.com/v1"
```

Things to verify after wiring:

- `tangle sdk pipelines validate <pipeline.yaml>` passes.
- Run the **complete 4-stage pre-submit gate** from
  [`step-3-submit.md`](step-3-submit.md) § "Pre-submit checks". The gate
  uses `grep -lEi` (filenames only, never echoes matching lines) plus a
  placeholder scan and a Tangle-secret existence check. **Do not** invent
  ad-hoc verification commands like `grep -E '(sk-|Bearer |...)' file` —
  that variant prints the matching line to stdout, which re-leaks the value
  into your terminal, agent transcript, and shell history. Always use the
  `-lE` form (or open the file in an editor) when checking for credential
  shapes.
- The argument is NOT also set elsewhere (e.g. via `--arg` / `--args-json`)
  with a literal value. Run args override `dynamicData` at some call sites;
  double-check the effective value.

### 4. Author the component to consume the secret

If you're also writing the component code, accept the secret as a regular
string argument and **degrade gracefully when unset** — the component should
not crash if Tangle resolves the secret to empty (e.g. the secret was
deleted, or a teammate runs the pipeline under an identity that doesn't have
it):

```python
def call_llm(prompt: str, api_key: str = "", base_url: str = "") -> dict:
    if not api_key:
        raise ValueError(
            "Missing api_key. Create the Tangle secret and reference it via "
            "dynamicData.secret on this argument."
        )
    ...
```

Don't log the value. Don't echo it back as an output. Don't write it to an
artifact.

## Identity scoping

Secrets in Tangle belong to the authenticating identity that created them — a
secret created under one `--token` / credential is not visible to a run that
authenticates as a different identity. If you need a secret to exist under a
different identity, re-run `secrets create` while authenticating as that
identity (a different `--token` / `--auth-header`; see
[`../OSS-CONVENTIONS.md`](../OSS-CONVENTIONS.md) §4 for the auth flags).

For the same reason, **never** `export TANGLE_API_TOKEN='…value…'` literally
in the shell — that lands in `~/.bash_history` / `~/.zsh_history`. If you need
a token for a one-off command, source it via `read -rs` the same way as
`MY_API_KEY` above.

If a run fails with a "secret not found" or empty-value error, the first thing
to check is *whose* identity holds the secret. Symptom: the pipeline works when
you submit it under your own credential but fails when it runs under a
different one.

## Anti-patterns — refuse all of these

- "I'll just paste the value into an input field on the run page so the
  pipeline can pick it up." ❌ The value is now in the run spec, visible in
  run details, exported by `pipeline-runs export`, and copied by clones.
- "I'll set the value as a `constantValue:` on the argument, since it's just
  a one-off run." ❌ Same as above — `constantValue` is plaintext.
- "I'll bake it into the component's Docker image / a config file embedded in
  the image." ❌ The image is mirrored, cached, and pullable by anyone with
  registry read.
- "I'll pass it via `--arg` / `--args-json`." ❌ Treated as pipeline
  arguments — same plaintext exposure.
- "I'll add it as a `cli_args:` value on a downstream task." ❌ Treated as
  pipeline arguments — same plaintext exposure.
- "It's only the staging key, so it's fine." ❌ Staging keys are still
  credentials. Use a secret.
- "I'll create the secret with `--value` using the key I just read from an
  encrypted store." Partial credit — the secret reference is right, but you've
  now written the plaintext into shell history and possibly logs. Use
  `--from-env` and have the human export the var.

## Working example

A minimal end-to-end demo against a public bearer-token echo endpoint
(`httpbin.org/bearer`):

```bash
# 1. Create the secret (human runs this — DEMO_BEARER_TOKEN can be any string).
#    Use --from-env so the value never lands in shell history.
read -rs DEMO_BEARER_TOKEN
export DEMO_BEARER_TOKEN
tangle sdk secrets create DEMO_BEARER_TOKEN \
  --from-env DEMO_BEARER_TOKEN --description 'demo'
unset DEMO_BEARER_TOKEN

# 2. Submit — the pipeline references DEMO_BEARER_TOKEN via dynamicData.secret
tangle sdk pipeline-runs submit secrets_demo_pipeline.yaml --hydrate

# 3. Cleanup
tangle sdk secrets delete DEMO_BEARER_TOKEN
```

The pipeline injects the secret as `Authorization: Bearer <value>` to
`httpbin.org/bearer`, and a second task confirms the auth header round-tripped
correctly. Inspect that pipeline YAML for the canonical `dynamicData.secret`
shape.

## CLI reference

```bash
tangle sdk secrets list
tangle sdk secrets create NAME --from-env ENV_VAR \
    [--description '…'] [--expires-at 2026-12-31T00:00:00Z]
tangle sdk secrets update NAME --from-env ENV_VAR
tangle sdk secrets delete NAME [--force]
tangle sdk secrets --help
```

`create` / `update` take `--value` / `-v` or (preferred) `--from-env` / `-e`,
plus `--description` / `-d` and `--expires-at`. `delete` prompts unless
`--force`. All `secrets` subcommands accept `--config` for multi-secret config
files (see the `_defaults` / `configs` block above).

## When in doubt

Stop and ask the human. "This pipeline needs an `X_API_KEY` — please create
a Tangle secret named `<NAME>` (`tangle sdk secrets create <NAME>
--from-env <NAME>`) and confirm before I wire it through" is always the
right move. Never the wrong move.
