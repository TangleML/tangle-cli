"""Identifier helpers — snake_case to Title Case With Spaces.

Used to derive Tangle's task IDs from Python variable names. The PoC
authors write ``build_quality_tables = build_dbt(...)`` and the
framework infers the task ID ``"Build Quality Tables"``.
"""
from __future__ import annotations


def snake_to_title_case(name: str) -> str:
    """Convert a ``snake_case`` identifier to ``Title Case With Spaces``.

    Examples:
        >>> snake_to_title_case("build_quality_tables")
        'Build Quality Tables'
        >>> snake_to_title_case("foo")
        'Foo'

    Notes:
        - Multiple consecutive underscores collapse: empty segments are
          dropped (so ``a__b`` → ``A B``, not ``A  B``).
        - All-caps tokens are unchanged by ``str.capitalize`` semantics
          (which lowercases trailing letters), so ``GPU`` becomes ``Gpu``.
          Users with acronyms should call ``.named("...")`` explicitly.
    """
    parts = [p for p in name.split("_") if p]
    return " ".join(p.capitalize() for p in parts)
