"""Manual-test example: per-variant @task sidecar dedup for ONE shared function.

The SAME module-qualified function -- ``dedup_image_variants.shared_dbt.run_dbt``
-- is re-decorated three times: twice with distinct task-level images, and once
more with an image identical to the first. Before the dedup fix the sidecar was
keyed by function NAME, so all three collapsed into the first entry and every
task silently resolved to the first call site's image.

Compile from the repository root with::

    uv run tangle sdk pipelines compile \
      examples/python_pipeline/dedup_image_variants/pipeline.py \
      --pipeline dedup_image_variants_pipeline \
      --output /tmp/tangle-dedup-demo/pipeline.yaml

Then inspect the two generated artifacts::

    cat /tmp/tangle-dedup-demo/pipeline.components.yaml
    cat /tmp/tangle-dedup-demo/pipeline.yaml

Expected assertions
-------------------
1. ``pipeline.components.yaml`` has EXACTLY TWO entries, both hashed:
   ``run-dbt--<10 hex chars>``. Neither keeps the bare ``run-dbt`` name --
   once a base collides, every variant is suffixed, so there is no arbitrary
   "first variant wins".
2. The two entries differ ONLY in ``local_from_python.image``
   (``python:3.12-slim`` vs ``python:3.12``); both carry
   ``function: run_dbt`` and the same ``file: ./shared_dbt.py``.
3. Each graph task's ``componentRef.url`` is
   ``resolve://./pipeline.components.yaml#run-dbt--<hash>`` pointing at the
   fragment whose image matches that task's decorator:
   ``daily_orders`` and ``hourly_sessions`` -> the SLIM entry (they share one
   identity and therefore one fragment -- repeated identical calls still
   dedup), ``backfill_orders`` -> the FAT entry.
4. No raw image, registry, tag, digest, or source-path text appears in any
   fragment name: the suffix is a SHA-256 prefix over the canonical identity,
   so ``python``, ``slim``, ``3.12``, ``shared_dbt`` and ``.py`` must NOT be
   substrings of any sidecar key.
5. Fragment names are stable: recompiling to a different ``--output``
   directory, or moving this example tree elsewhere, produces the SAME two
   fragment names (identity is anchored at the pipeline source directory, not
   the output directory and not an absolute machine path).

Observed output on this revision (the digests are derived only from
project-relative values, so they reproduce on any checkout)::

    run-dbt--b196ad73a4   image: python:3.12-slim
    run-dbt--6024a73044   image: python:3.12

    daily_orders     -> resolve://./pipeline.components.yaml#run-dbt--b196ad73a4
    hourly_sessions  -> resolve://./pipeline.components.yaml#run-dbt--b196ad73a4
    backfill_orders  -> resolve://./pipeline.components.yaml#run-dbt--6024a73044

Note on ``file:``: sidecar paths are relative to the OUTPUT directory, so
compiling into ``/tmp`` (outside the source tree) writes a long ``../../..``
path to ``shared_dbt.py``. That is expected and is exactly the point of
assertion 5 -- the emitted path tracks the output directory while the fragment
NAMES do not. Compile next to the script if you want short paths.
"""

from tangle_cli.python_pipeline import Out, pipeline, task

from shared_dbt import run_dbt

# Three decorations of ONE function. ``dbt_slim`` and ``dbt_slim_again`` are
# byte-identical configurations, so they share a single generated component;
# ``dbt_fat`` differs in image, so it is a genuinely different component.
dbt_slim = task(image="python:3.12-slim")(run_dbt)
dbt_slim_again = task(image="python:3.12-slim")(run_dbt)
dbt_fat = task(image="python:3.12")(run_dbt)


@pipeline("Dedup image variants demo")
def dedup_image_variants_pipeline() -> Out[str]:
    # Two call sites, one image -> ONE sidecar entry, two task refs to it.
    daily = dbt_slim.named("daily_orders")(model="orders_daily")
    hourly = dbt_slim_again.named("hourly_sessions")(model="sessions_hourly")

    # Same function, different image -> its OWN sidecar entry and task ref.
    backfill = dbt_fat.named("backfill_orders")(model=daily, target="backfill")

    print(hourly)
    return backfill
