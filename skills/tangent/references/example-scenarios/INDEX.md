# Example Scenarios — Public-Data Worked Examples

> **Purpose**: A small set of self-contained, runnable scenarios on **public datasets**,
> meant as inspiration for an autonomous tuning loop. Each one mirrors the loop in
> `references/step-0-initialize.md` … `references/step-7-decide.md`: read the situation, form
> a single hypothesis, express it as a small config delta, submit, watch the right signals,
> and write down the outcome.

Every command in these scenarios uses the published `tangle …` surface documented in
`references/tangle-tools.md`. They name only public datasets and public models, store
artifacts scheme-agnostically (read the `uri`; see `OSS-CONVENTIONS.md` §5), and treat
registry / promotion / scheduling / `--run-as` as **extension-only** (`OSS-CONVENTIONS.md`
§10, D9). None of them require component discovery / published-component search — that feature
is off by default in OSS, so any search step is optional and tolerant of empty results
(`OSS-CONVENTIONS.md` §10, D11).

---

## Scenarios

| # | Scenario | File | Public Dataset | What It Optimizes | Model Family |
|---|----------|------|----------------|-------------------|--------------|
| 1 | [Learning-to-Rank on MSLR-WEB10K](01-mslr-ranking.md) | `01-mslr-ranking.md` | MSLR-WEB10K (LETOR) | NDCG@10 (also NDCG@1, MAP) | Gradient-boosted ranker (LambdaRank) |
| 2 | [Text Classification on AG News](02-text-classification.md) | `02-text-classification.md` | AG News | macro-F1 (also accuracy, per-class F1) | Small public encoder fine-tune |

---

## Cross-Cutting Techniques

| Technique | Where Used |
|-----------|-----------|
| **Single-lever, attributable rounds** | Both — one hypothesis per run (`references/step-2-hypothesize.md`) |
| **Objective↔metric alignment** | MSLR ranking (lambdarank truncation = NDCG cut) |
| **Regularization vs overfitting** | MSLR ranking (leaves / learning rate / min-data-in-leaf) |
| **Cost-vs-quality trimming** | Text classification (epochs + early stopping) |
| **Throughput right-sizing** | Text classification (sequence length + warmup schedule) |
| **Per-segment / per-class checks** | Both — guard against an average win that hides a slice regression |

## Loop Patterns (shared by every scenario)

| Pattern | Details |
|---------|---------|
| **Submit** | Args inline via `--arg K=V` / `--args-json` — no `-f config.yaml` (`OSS-CONVENTIONS.md` §10, D12); hydrate is the default; `submit` never waits |
| **Wait / status** | `pipeline-runs status` and `pipeline-runs graph-state` over a Python poll loop (`OSS-CONVENTIONS.md` §10, D14); block with `pipeline-runs wait` |
| **Logs** | Container logs via `pipeline-runs logs EXECUTION_ID`; scheduling/OOM events from the launcher's runtime, not the backend (`OSS-CONVENTIONS.md` §7) |
| **Artifacts** | Metadata via `artifacts get`; bytes via the signed-URL recipe; URIs are scheme-agnostic, e.g. `hf://…` (`OSS-CONVENTIONS.md` §5) |
| **Annotations** | Generic `--annotation session=… --annotation round=… --annotation type=… --annotation label=…` (`OSS-CONVENTIONS.md` §8) |
| **Learnings** | Record keyed by `run_id` under `$LEARNINGS_DIR/<scenario>/` (default `$SCENARIO_DIR/learnings/`); optional shared HuggingFace-dataset tier (`OSS-CONVENTIONS.md` §6, `references/knowledge-corpus.md`) |
