# Text Classification on AG News

A second self-contained, public-data worked example — same loop, a different model family. It
shows the situation → hypothesis → config-delta → signals → outcome rhythm applied to a
transformer fine-tuning task instead of a gradient-boosted ranker. Every command is the
published `tangle …` surface (`references/tangle-tools.md`).

## Dataset (public)

[**AG News**](https://huggingface.co/datasets/ag_news) — a widely used public topic-
classification benchmark: 120,000 training and 7,600 test news headlines/descriptions across
4 balanced classes (World, Sports, Business, Sci/Tech). It is small, balanced, and fast to
iterate on, which makes it a good loop-shakedown set. No credentials or private data.

The training task references the split as an input artifact by `uri` (scheme-agnostic — read
whatever comes back; see `OSS-CONVENTIONS.md` §5):

```
hf://datasets/<org>/ag-news@main/train.parquet
```

## What it optimizes

- **Primary**: macro-F1 on the test split (robust to any residual class imbalance).
- **Secondary**: accuracy, and per-class F1 (catch a model that's strong on three classes and
  weak on the fourth).

## Pipeline shape

```
prepare-data  →  finetune-classifier  →  eval-classifier
```

- `prepare-data` — load + tokenize the splits with a public base model's tokenizer, emit a
  tokenized-dataset artifact.
- `finetune-classifier` — fine-tune a small public encoder (e.g.
  [`distilbert-base-uncased`](https://huggingface.co/distilbert-base-uncased)) with a 4-way
  classification head.
- `eval-classifier` — score the test split and emit macro-F1, accuracy, and per-class F1 as a
  metrics artifact.

Only public models and datasets appear here. There is no registry/promotion/scheduling step —
those are extension-only in OSS (`OSS-CONVENTIONS.md` §10, D9); record the winning `run_id`
and move on.

---

## Round example

### Situation

The baseline fine-tune finished but underwhelms, and the run was expensive:

- macro-F1 = **0.918**, accuracy = **0.919**.
- Container logs (`tangle sdk pipeline-runs logs <execution_id>`) show validation loss
  bottoming out around epoch 2 of 5 and creeping up after — the last 2–3 epochs are wasted
  compute and mild overfitting.
- `max_seq_length=256`, but AG News items are short; most sequences are heavily padded.
- `learning_rate=5e-5` with a constant schedule.

### Hypothesis

Two orthogonal, low-risk levers:

1. **Stop wasting epochs**: cut `num_train_epochs` to 3 and add early stopping on validation
   macro-F1. Should preserve quality while cutting cost.
2. **Right-size the inputs and smooth the schedule**: drop `max_seq_length` to 128 (fits
   nearly all items, roughly halves step cost) and add linear warmup + decay. Faster steps let
   us afford a slightly larger effective batch.

Test them as two single-lever runs so each is attributable (`references/step-2-hypothesize.md`).

### Config deltas

Args go inline at submit — no `-f config.yaml` (`OSS-CONVENTIONS.md` §10, D12).

Run A — fewer epochs + early stopping:

```bash
tangle sdk pipeline-runs submit pipeline.yaml \
  --arg num_train_epochs=3 \
  --arg early_stopping_patience=1 \
  --arg metric_for_best_model=macro_f1 \
  --annotation session=2026-06-23-text-classification \
  --annotation round=1 \
  --annotation type=cost-trim \
  --annotation label=epochs3-earlystop
```

Run B — right-size sequence length + warmup schedule:

```bash
tangle sdk pipeline-runs submit pipeline.yaml \
  --arg max_seq_length=128 \
  --arg lr_scheduler_type=linear \
  --arg warmup_ratio=0.06 \
  --arg per_device_train_batch_size=64 \
  --annotation session=2026-06-23-text-classification \
  --annotation round=1 \
  --annotation type=throughput \
  --annotation label=seq128-warmup-bs64
```

For nested overrides use `--args-json '<JSON>'`. `submit` returns a `run_id` and does not wait;
record it, then watch the run.

### What to look for

Use the CLI for status, not a Python poll loop (`OSS-CONVENTIONS.md` §10, D14):

```bash
tangle sdk pipeline-runs status <run_id>
tangle sdk pipeline-runs wait <run_id> --max-wait 600 --poll-interval 10
```

Application progress (loss curves, early-stop trigger, steps/sec) is in container logs; a
killed-pod / OOM signal comes from your launcher's runtime, not the Tangle backend
(`OSS-CONVENTIONS.md` §7). On completion, read the metrics artifact and check:

1. **macro-F1 vs baseline** — within noise, better, or worse? With `reps > 1`, compare means.
2. **Cost vs quality (Run A)** — fewer epochs should hold F1 roughly flat while cutting
   wall-clock/compute. If F1 dropped meaningfully, patience=1 was too aggressive.
3. **Throughput vs quality (Run B)** — confirm `max_seq_length=128` didn't truncate enough
   content to hurt F1; steps/sec in the logs should rise.
4. **Per-class F1** — make sure the "Sci/Tech" class (the usual weak one) didn't regress while
   the macro average held.

### Outcome (illustrative)

| Run | Lever | macro-F1 | Rel. cost | Notes |
|-----|-------|----------|-----------|-------|
| baseline | — | 0.918 | 1.0× | overfits after ep2 |
| A | epochs=3 + early stop | 0.919 | ~0.55× | same quality, ~half the compute |
| B | seq=128 + warmup | 0.922 | ~0.6× | slight F1 gain, faster steps |

Both levers are wins and they compose — the next round combines `epochs=3 + early stop` with
`seq=128 + warmup` as a single new hypothesis. Record the best run as a learning keyed by its
`run_id`:

```bash
mkdir -p "$LEARNINGS_DIR/text-classification"
cp learning.json "$LEARNINGS_DIR/text-classification/learning-<best_run_id>.json"
```

`LEARNINGS_DIR` defaults to `$SCENARIO_DIR/learnings/` (env-overridable); the optional shared
tier is a HuggingFace-dataset repo (`OSS-CONVENTIONS.md` §6, `references/knowledge-corpus.md`).
Then decide per `references/step-7-decide.md`.
