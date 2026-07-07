"""Pipeline whose body calls no ``ref(...)`` — an EMPTY graph.

The emitter must reject this up front: the dehydrated schema requires at
least one task (``minProperties: 1``). No ``Out[T]`` is declared so the
trace's return check passes and the empty-graph guard in ``emit`` is the
one that fires.
"""
from tangle_cli.python_pipeline import pipeline


@pipeline("Empty Pipeline")
def empty_pipeline(cfg):
    # No ref(...) calls → zero tasks → CompileError at emit time.
    return None
