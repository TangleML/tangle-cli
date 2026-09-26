"""``ref()`` emits every locator it is given, in canonical key order.

The dehydrated schema allows ``url``, ``name`` and ``digest`` together, and
the hydrator resolves each one independently and keeps the highest component
version (``_resolve_best_ref`` / ``_pick_best_candidate``). ``ref()`` used to
reject ``url`` with ``name``, so five corpus pipelines under
``relevance/experiments/shop_app`` and ``relevance-tools`` could not be
expressed in Python at all.

A name beside a url is an upgrade path, not a filter: it can win.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from tangle_cli.pipeline_compiler import compile_pipeline
from tangle_cli.pipeline_hydrator import PipelineHydrator
from tangle_cli.python_pipeline import ref
from tangle_cli.python_pipeline.errors import CompileError

_DIGEST = "a" * 64

def _component(name: str, version: str) -> dict:
    return {
        "name": name,
        "metadata": {"annotations": {"version": version}},
        "implementation": {"container": {"image": "python:3.12"}},
    }


_HEADER = '''
from tangle_cli.python_pipeline import Out, pipeline, ref

COMPONENT = ref(__REF_ARGS__)

'''

_BODY = '''
@pipeline("Locators")
def locators() -> Out[str]:
    run_it = COMPONENT.named("Run It")()
    return run_it
'''


def _compile(tmp_path: Path, ref_args: str, case: str, *, component: str = "c.yaml") -> dict:
    """Compile a one-task pipeline and return its ``componentRef``.

    ``component`` is created relative to the output directory because the
    compiler refuses a relative ``file://`` ref whose target is missing.
    """
    case_dir = tmp_path / case
    case_dir.mkdir(parents=True, exist_ok=True)
    target = (case_dir / component).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(_component("My Comp", "1.0.0")), encoding="utf-8")
    script = case_dir / "pipeline.py"
    script.write_text(
        textwrap.dedent(_HEADER).replace("__REF_ARGS__", ref_args)
        + textwrap.dedent(_BODY),
        encoding="utf-8",
    )
    out = case_dir / "compiled.yaml"
    compile_pipeline(script, out)
    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    return doc["implementation"]["graph"]["tasks"]["Run It"]["componentRef"]


# ============================================================================
# Emitted shape
# ============================================================================


@pytest.mark.parametrize(
    "ref_args, expected, case",
    [
        ('url="file://./c.yaml"', {"url": "file://./c.yaml"}, "url"),
        ('name="My Comp"', {"name": "My Comp"}, "name"),
        (f'digest="{_DIGEST}"', {"digest": _DIGEST}, "digest"),
        (
            f'url="file://./c.yaml", digest="{_DIGEST}"',
            {"url": "file://./c.yaml", "digest": _DIGEST},
            "url_digest",
        ),
        (
            f'name="My Comp", digest="{_DIGEST}"',
            {"name": "My Comp", "digest": _DIGEST},
            "name_digest",
        ),
        (
            'url="file://./c.yaml", name="My Comp"',
            {"url": "file://./c.yaml", "name": "My Comp"},
            "url_name",
        ),
        (
            f'url="file://./c.yaml", name="My Comp", digest="{_DIGEST}"',
            {"url": "file://./c.yaml", "name": "My Comp", "digest": _DIGEST},
            "url_name_digest",
        ),
    ],
)
def test_every_locator_combination_emits_its_keys(tmp_path, ref_args, expected, case):
    assert _compile(tmp_path, ref_args, case) == expected


def test_locator_keys_are_emitted_in_canonical_order(tmp_path):
    """Key order is the emitter's, not the caller's, so two pipelines writing
    the same locators produce the same bytes."""
    forward = _compile(
        tmp_path,
        f'url="file://./c.yaml", name="My Comp", digest="{_DIGEST}"',
        "order_forward",
    )
    reversed_kwargs = _compile(
        tmp_path,
        f'digest="{_DIGEST}", name="My Comp", url="file://./c.yaml"',
        "order_reversed",
    )

    assert list(forward) == ["url", "name", "digest"]
    assert list(reversed_kwargs) == ["url", "name", "digest"]


def test_the_corpus_shape_round_trips(tmp_path):
    """The shape five corpus pipelines use — a relative component url beside
    the component's published name — which could not be authored in Python
    before."""
    corpus = {
        "name": "Build Vantage Features",
        "url": "file://../../components/build-vantage-features.yaml",
    }

    emitted = _compile(
        tmp_path,
        'url="file://../../components/build-vantage-features.yaml",'
        ' name="Build Vantage Features"',
        "corpus/tangle/pipeline",
        component="../../components/build-vantage-features.yaml",
    )

    assert emitted == corpus
    assert set(emitted) == set(corpus)


# ============================================================================
# Chaining is unaffected
# ============================================================================


def test_chaining_preserves_every_locator(tmp_path):
    """``.named`` / ``.bind`` / ``.with_annotations`` rebuild the ref, so the
    locators have to survive the copy."""
    case_dir = tmp_path / "chained"
    case_dir.mkdir()
    (case_dir / "c.yaml").write_text(
        yaml.safe_dump(_component("My Comp", "1.0.0")), encoding="utf-8"
    )
    script = case_dir / "pipeline.py"
    script.write_text(
        textwrap.dedent(
            f'''
            from tangle_cli.python_pipeline import Out, pipeline, ref

            COMPONENT = (
                ref(url="file://./c.yaml", name="My Comp", digest="{_DIGEST}")
                .bind(fixed="1")
                .with_annotations({{"team": "search"}})
            )


            @pipeline("Chained")
            def chained() -> Out[str]:
                run_it = COMPONENT.named("Run It")(other="2")
                return run_it
            '''
        ),
        encoding="utf-8",
    )
    out = case_dir / "compiled.yaml"
    compile_pipeline(script, out)

    task = yaml.safe_load(out.read_text(encoding="utf-8"))["implementation"]["graph"][
        "tasks"
    ]["Run It"]
    assert task["componentRef"] == {
        "url": "file://./c.yaml",
        "name": "My Comp",
        "digest": _DIGEST,
    }
    assert task["arguments"] == {"fixed": "1", "other": "2"}
    assert task["annotations"] == {"team": "search"}


# ============================================================================
# Hydration
# ============================================================================


def test_the_hydrator_resolves_both_locators_and_keeps_the_newer(tmp_path):
    """This is the behaviour the corpus relies on, and the reason the name is
    worth emitting beside the url: whichever locator resolves to the higher
    version wins."""
    hydrator = PipelineHydrator(client=MagicMock())
    hydrator._resolve_registered_component = MagicMock(  # type: ignore[method-assign]
        side_effect=lambda ref_type, ref_value, path, base_dir: {
            "url": ("digest-from-url", _component("From URL", "1.0.0")),
            "name": ("digest-from-name", _component("From Name", "2.0.0")),
        }[ref_type]
    )

    task = hydrator._resolve_task(
        "Run It",
        {"componentRef": {"url": "file://./c.yaml", "name": "My Comp"}},
        "p.tasks.Run It",
    )

    assert task["componentRef"]["spec"]["name"] == "From Name"
    assert task["componentRef"]["digest"] == "digest-from-name"


def test_the_hydrator_keeps_the_url_when_it_is_the_newer(tmp_path):
    """The same mechanism in the other direction, so the test above is not
    just pinning the tie-break order."""
    hydrator = PipelineHydrator(client=MagicMock())
    hydrator._resolve_registered_component = MagicMock(  # type: ignore[method-assign]
        side_effect=lambda ref_type, ref_value, path, base_dir: {
            "url": ("digest-from-url", _component("From URL", "3.0.0")),
            "name": ("digest-from-name", _component("From Name", "2.0.0")),
        }[ref_type]
    )

    task = hydrator._resolve_task(
        "Run It",
        {"componentRef": {"url": "file://./c.yaml", "name": "My Comp"}},
        "p.tasks.Run It",
    )

    assert task["componentRef"]["spec"]["name"] == "From URL"


def test_a_compiled_url_name_ref_hydrates(tmp_path):
    """End to end: compile the Python, then hydrate what it wrote."""
    component = tmp_path / "c.yaml"
    component.write_text(yaml.safe_dump(_component("My Comp", "1.0.0")), encoding="utf-8")
    case_dir = tmp_path / "e2e"
    case_dir.mkdir()
    script = case_dir / "pipeline.py"
    script.write_text(
        textwrap.dedent(
            '''
            from tangle_cli.python_pipeline import Out, pipeline, ref

            COMPONENT = ref(url="file://../c.yaml", name="My Comp")


            @pipeline("Hydrated")
            def hydrated() -> Out[str]:
                run_it = COMPONENT.named("Run It")()
                return run_it
            '''
        ),
        encoding="utf-8",
    )
    out = case_dir / "compiled.yaml"
    compile_pipeline(script, out)

    hydrator = PipelineHydrator(client=MagicMock())
    result = hydrator.hydrate_file(out)
    task = result.data["implementation"]["graph"]["tasks"]["Run It"]

    assert task["componentRef"]["spec"]["name"] == "My Comp"


# ============================================================================
# Still rejected
# ============================================================================


def test_a_ref_with_no_locator_is_rejected():
    with pytest.raises(CompileError) as excinfo:
        ref()

    assert "requires a locator" in str(excinfo.value)


def test_a_tag_ref_is_still_rejected():
    """The hydrator has no tag fetcher, and ``tag`` is not even a property of
    the dehydrated schema."""
    with pytest.raises(CompileError) as excinfo:
        ref(url="file://./c.yaml", name="My Comp", tag="v1")

    assert "tag" in str(excinfo.value)


def test_rejection_messages_do_not_echo_values():
    with pytest.raises(CompileError) as excinfo:
        ref(tag="super-secret-tag")

    assert "super-secret-tag" not in str(excinfo.value)
