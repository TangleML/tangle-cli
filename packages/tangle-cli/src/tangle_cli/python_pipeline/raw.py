"""``raw()`` — mark a string argument as a legitimate RUNTIME placeholder.

The compiler's no-template-delimiter output guard
rejects any ``{{``, ``{%`` or ``{#`` anywhere in the compiled dehydrated
pipeline, because a surviving delimiter normally means an upstream
template was not rendered at compile time. That guard is intentionally
operation-agnostic and cannot tell a genuine compile-time leak from an
intentional runtime sentinel.

Some Tangle ops legitimately ship a literal ``{{name}}`` placeholder that
is substituted at RUN time, NOT at compile/hydrate time, and NOT by Jinja.
The canonical case is a ``run-query`` task whose ``input_1`` is the
runtime-timestamped output table of an upstream ``unique_output=True``
task: the fully-qualified table name is unknown at compile time, so the
SQL must carry a literal ``{{input_1}}`` that the run-query op substitutes
at runtime via a plain ``query.replace("{{" + key + "}}", value)`` — there
is no Jinja involved. Hydrate passes the sentinel through untouched.

Wrapping such a value in :func:`raw` tells the compiler "this delimiter is
intentional": the inner string is emitted verbatim (exactly as a plain
``str`` constant is) AND the argument's location is recorded in an
exempt-paths allow-list so the output guard skips it. Every OTHER
delimiter in the compiled output still fails the guard, so real
compile-time leaks are still caught.

Usage::

    from tangle_cli.python_pipeline import raw, ref

    add_cache_key = ref(url="resolve://./components.yaml#run-query")(
        # ``input_1`` is wired to the runtime-timestamped output of an
        # upstream unique_output task; the op str.replaces ``{{input_1}}``
        # at run time, so the SQL must ship the literal sentinel.
        input_1=mine_options.output_table,
        sql_query=raw("SELECT * FROM `{{input_1}}`"),
    )

``raw`` only accepts a ``str``; the wrapped value is still subject to the
ordinary runnable-argument contract (it is emitted as a raw string
constant). It is NOT an escape hatch for compile-time Jinja: use it ONLY
for placeholders an op resolves at runtime.
"""
from __future__ import annotations


class Raw:
    """A string argument value whose template delimiters are intentional.

    Carries a single ``str`` whose ``{{...}}`` is a legitimate RUNTIME
    placeholder (see the module docstring). At emit time the inner
    string is rendered verbatim — identical to a plain ``str`` constant —
    and the argument's JSON path is added to the output guard's
    exempt-paths allow-list so that one location is skipped while every
    other delimiter in the output still fails the guard.

    Construct via the :func:`raw` helper rather than directly.
    """

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError(
                f"raw() requires a str, got {type(value).__name__!r}. raw() "
                "marks a string runtime placeholder (e.g. a run-query "
                "'{{input_1}}' sentinel) as intentional; convert non-string "
                "values to a string in your pipeline code first."
            )
        # raw() exempts a value from the output guard's no-template-delimiter
        # check, but ONLY for {{...}} runtime sentinels — strings an op
        # str.replaces at RUN time. {%...%} (statements) and {#...#} (comments)
        # are Jinja constructs rendered at COMPILE time, never substituted at
        # run time, so a surviving one is a leaked compile-time template, not a
        # runtime placeholder — exactly what raw() must not hide. Reject them
        # at construction so the exemption can never rescue a real leak.
        token = "{%" if "{%" in value else ("{#" if "{#" in value else None)
        if token is not None:
            raise ValueError(
                "raw() values may carry only {{...}} runtime-substitution "
                "sentinels (e.g. a run-query '{{input_1}}' placeholder an op "
                f"str.replaces at run time). The value passed to raw() contains a Jinja {token} "
                "token, which is never a valid runtime placeholder: {%...%} "
                "(statements) and {#...#} (comments) are rendered by Jinja at "
                "COMPILE time, not substituted at run time, so a surviving one "
                "is a leaked compile-time template — exactly what raw() must "
                "not hide. Render the Jinja in your pipeline code before "
                "passing the result to raw(), or drop raw() if the value has "
                "no runtime sentinel."
            )
        self.value = value

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return f"Raw({self.value!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Raw) and other.value == self.value

    def __hash__(self) -> int:
        return hash((Raw, self.value))


def raw(value: str) -> Raw:
    """Mark ``value`` as a legitimate RUNTIME template placeholder.

    Returns a :class:`Raw` wrapper. Pass it as a task argument value when
    the string legitimately ships a ``{{...}}`` placeholder that an op
    substitutes at RUN time (not compile/hydrate time, and not via Jinja)
    — e.g. a ``run-query`` ``sql_query`` carrying ``{{input_1}}`` for a
    runtime-timestamped upstream table. ``{%...%}`` and ``{#...#}`` Jinja
    tokens are rejected: they are rendered at compile time, so a surviving
    one is a leaked compile-time template, never a runtime placeholder.

    The wrapped string is emitted verbatim (exactly like a plain ``str``
    constant) and its location is exempted from the compiler's
    no-template-delimiter output guard, while every other delimiter in the
    compiled pipeline still fails that guard.

    Raises:
        TypeError: when ``value`` is not a ``str``.
        ValueError: when ``value`` contains a ``{%...%}`` or ``{#...#}`` Jinja token.
    """
    return Raw(value)
