# Learning-to-Rank on MSLR-WEB10K

A self-contained, public-data worked example of one autonomous tuning round. It mirrors the
loop in `references/step-0-initialize.md` … `references/step-7-decide.md`: read the situation,
form a hypothesis, express it as a small config delta, submit, watch the right signals, and
write down the outcome. Nothing here is specific to any one backend — every command is the
published `tangle …` surface from `references/tangle-tools.md`.

## Dataset (public)

[**MSLR-WEB10K**](https://www.microsoft.com/en-us/research/project/mslr/) — Microsoft's
Learning-to-Rank benchmark. ~10,000 queries, 5 cross-validation folds, 136 numeric features
per (query, document) pair, graded relevance labels 0–4. It is the canonical public L2R set
(the LETOR family). Released for research use; no credentials and no private data involved.

For a Tangle scenario the data lives wherever your backend's storage provider puts it. Under
the default OSS backend that is a HuggingFace dataset repo, so the training task reads an input
artifact whose `uri` looks like:

```
hf://datasets/<org>/mslr-web10k@main/Fold1/train.txt
```

The exact scheme is irrelevant to the loop — the pipeline references the artifact by `uri`,
and you read whatever scheme comes back (see `references/uploading-artifacts.md` and the
artifact recipe in `OSS-CONVENTIONS.md` §5). Do **not** assume a particular storage scheme.

## What it optimizes

- **Primary**: NDCG@10 on the held-out fold.
- **Secondary**: NDCG@1 (top-of-list quality) and MAP.
- **Per-segment** (optional): track NDCG@10 split by query frequency band if your eval task
  emits it — useful for catching a model that wins on average but regresses on rare queries.

## Pipeline shape

A minimal ranking pipeline has three leaf tasks:

```
generate-featureset  →  train-ranker  →  eval-ranker
```

- `generate-featureset` — load the fold's `train.txt` / `vali.txt` / `test.txt`, drop
  constant/duplicate columns, emit a featureset artifact.
- `train-ranker` — fit a gradient-boosted ranker (e.g. LightGBM with `objective=lambdarank`)
  using the LETOR query-group boundaries.
- `eval-ranker` — score the test fold and emit NDCG@1/@10 and MAP as a metrics artifact.

You do not need a registry, a scheduler, or a promotion gate to run this loop. Those are
extension-only concepts in OSS (see `OSS-CONVENTIONS.md` §10, D9) — treat "promote the winning
model" as out of scope here and just record the winning `run_id`.

---

## Round example

### Situation

A baseline run finished. Reading its metrics (`tangle sdk artifacts get <run_id> -q
'{"tasks":["eval-ranker"]}'`, then fetching the metrics blob via the signed-URL recipe in
`OSS-CONVENTIONS.md` §5):

- NDCG@10 = **0.452**, NDCG@1 = **0.471**.
- The train metric is far ahead of validation (train NDCG@10 ≈ 0.61) — classic overfitting.
- The trees are deep (`num_leaves=255`) and learning rate is high (`learning_rate=0.1`).
- Truncation for the lambdarank objective is left at its default (~50), while the metric we
  actually care about is NDCG@**10**.

### Hypothesis

The model overfits and wastes gradient on positions far below the cut. Two cheap, orthogonal
levers:

1. **Regularize**: lower `num_leaves` and `learning_rate`, raise `min_data_in_leaf`. This
   should narrow the train/validation gap.
2. **Align the objective to the metric**: set the lambdarank truncation to 10 so the loss
   concentrates on the positions NDCG@10 scores.

We test them as two separate single-lever runs so the round is attributable — see
`references/step-2-hypothesize.md` (one hypothesis per run).

### Config deltas

Run arguments are passed inline at submit time — there is **no** `-f config.yaml` file
(`OSS-CONVENTIONS.md` §10, D12). Start from the baseline pipeline, then override only the
args under test.

Run A — regularize:

```bash
tangle sdk pipeline-runs submit pipeline.yaml \
  --arg num_leaves=63 \
  --arg learning_rate=0.05 \
  --arg min_data_in_leaf=200 \
  --annotation session=2026-06-23-mslr-ranking \
  --annotation round=1 \
  --annotation type=regularize \
  --annotation label=leaves63-lr05-mdl200
```

Run B — align truncation to the metric (and a matching `n_estimators` bump to compensate for
the lower learning rate):

```bash
tangle sdk pipeline-runs submit pipeline.yaml \
  --arg lambdarank_truncation_level=10 \
  --arg learning_rate=0.05 \
  --arg n_estimators=2000 \
  --annotation session=2026-06-23-mslr-ranking \
  --annotation round=1 \
  --annotation type=objective-align \
  --annotation label=trunc10-lr05-n2000
```

For a structured/nested override use `--args-json '<JSON>'` (or `--args-json @args.json`)
instead of repeated `--arg`. `submit` returns a `run_id` immediately and does **not** wait
(`OSS-CONVENTIONS.md` §2). Record each `run_id` in your session log and MEMORY before moving on.

### What to look for

While the runs are in flight, watch status and graph state with the CLI rather than a Python
poll loop (`OSS-CONVENTIONS.md` §10, D14):

```bash
tangle sdk pipeline-runs status <run_id>
tangle sdk pipeline-runs graph-state <execution_id>
```

To block until done: `tangle sdk pipeline-runs wait <run_id> --max-wait 600
--poll-interval 10`. Container logs (training progress, early-stopping rounds) come from
`tangle sdk pipeline-runs logs <execution_id>`; scheduling/OOM events come from your
launcher's runtime, not from the Tangle backend (`OSS-CONVENTIONS.md` §7).

When `eval-ranker` completes, read its metrics artifact and check, in order:

1. **NDCG@10 vs baseline** — did it move beyond run-to-run noise? If your scenario sets
   `reps > 1`, compare the *mean* across reps, not a single number.
2. **Train/validation gap** — Run A should have closed it. If the gap is gone but NDCG@10
   didn't improve, you regularized past the sweet spot.
3. **NDCG@1 alongside NDCG@10** — Run B (truncation=10) should help top positions most; make
   sure it didn't trade away NDCG@1.
4. **Per-segment** (if emitted) — confirm the average win isn't hiding a rare-query regression.

### Outcome (illustrative)

| Run | Lever | NDCG@10 | NDCG@1 | Train gap |
|-----|-------|---------|--------|-----------|
| baseline | — | 0.452 | 0.471 | large |
| A | regularize | 0.460 | 0.474 | small |
| B | truncation=10 | 0.466 | 0.486 | medium |

Both levers helped; aligning the objective to the metric (Run B) helped more and especially
lifted NDCG@1. The natural next round combines them (regularize **and** truncation=10) as a
single new hypothesis, submitted the same way. Write the win down as a learning keyed by the
best `run_id`:

```bash
mkdir -p "$LEARNINGS_DIR/mslr-ranking"
cp learning.json "$LEARNINGS_DIR/mslr-ranking/learning-<best_run_id>.json"
```

`LEARNINGS_DIR` defaults to `$SCENARIO_DIR/learnings/` and is env-overridable; an optional
shared HuggingFace-dataset tier exists for team corpora (`OSS-CONVENTIONS.md` §6 and
`references/knowledge-corpus.md`). Then decide per `references/step-7-decide.md`: keep
iterating, or stop because the round saturated.
