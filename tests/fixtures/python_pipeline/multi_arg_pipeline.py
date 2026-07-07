"""Multi-argument pipeline fixture exercising every runnable ArgumentValue shape.

The first task receives several STRING constants (one of them a
``json.dumps`` payload, proving structured data is stringified by the
author, never by tangle-cli), a ``wait_for`` edge bound to an ``In``
param, and a NON-edge-key argument (``run_mode``) ALSO bound to an ``In``
param — proving the emitter dispatches purely on the value's type, never
on the argument key.

The downstream task consumes the upstream task's BARE output via
``depends_on`` and a NAMED output (``produce_data.rows_written``) via a
plain argument key — exercising both ``taskOutput`` shapes and proving
the ``depends_on`` key is preserved verbatim.
"""
import json

from tangle_cli.python_pipeline import In, Out, pipeline, ref


@pipeline("Multi Arg Pipeline")
def multi_arg_pipeline(
    parent_wait_token: In[str],
    run_mode: In[str],
    cfg,
) -> Out[str]:
    produce_data = ref(url="file://./noop.yaml")(
        a_string="hello world",
        a_multiline="line one\nline two\n",
        a_json_payload=json.dumps({"mode": "dry_run", "batch_size": 100}),
        wait_for=parent_wait_token,
        run_mode=run_mode,
    )
    consume_data = ref(url="file://./noop.yaml")(
        depends_on=produce_data,
        rows=produce_data.rows_written,
    )
    return consume_data
