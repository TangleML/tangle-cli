"""A nested graph's input defaults reach its tasks when the caller omits them.

Tangle applies an input's default for a container component and for a run's
root graph, never for a graph nested in another. These tests compile and
hydrate pipelines, then check what each leaf container would receive under
those rules.
"""
from __future__ import annotations

import textwrap
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from tangle_cli.pipeline_compiler import compile_pipeline
from tangle_cli.pipeline_hydrator import PipelineHydrator
from tangle_cli.python_pipeline.errors import CompileError

UNSET = object()

JUDGE_YAML = """\
name: Judge
inputs:
  - {name: plan, type: String}
  - {name: verbose, type: Boolean}
  - {name: limit, type: Integer}
  - {name: note, type: String, optional: true}
implementation:
  container:
    image: python:3.12
    command: [sh, -c, "true"]
"""


def leaf_inputs(document: Mapping[str, Any]) -> dict[str, dict[str, tuple[Any, bool]]]:
    """``{"Task/.../Leaf": {input: (value, required)}}`` as a run of ``document`` would resolve them.

    Root inputs fall back to their defaults (a root input without one stands for
    its run argument), a nested graph's inputs take only the caller's argument,
    and a container input falls back to its own default. ``UNSET`` means no value.
    """
    leaves: dict[str, dict[str, tuple[Any, bool]]] = {}

    def value_of(argument: Any, scope: Mapping[str, Any]) -> Any:
        if isinstance(argument, Mapping) and "graphInput" in argument:
            return scope.get(argument["graphInput"]["inputName"], UNSET)
        if isinstance(argument, Mapping) and "taskOutput" in argument:
            return ("output of", argument["taskOutput"]["taskId"], argument["taskOutput"]["outputName"])
        return argument

    def walk(spec: Mapping[str, Any], scope: Mapping[str, Any], prefix: str) -> None:
        for task_id, task in spec["implementation"]["graph"]["tasks"].items():
            component = task["componentRef"]["spec"]
            arguments = {
                name: value_of(argument, scope) for name, argument in (task.get("arguments") or {}).items()
            }
            path = f"{prefix}{task_id}"
            if "graph" in component["implementation"]:
                walk(component, {k: v for k, v in arguments.items() if v is not UNSET}, path + "/")
                continue
            resolved = {}
            for entry in component.get("inputs") or []:
                default = entry.get("default")
                value = arguments.get(entry["name"], UNSET)
                if value is UNSET and default is not None:
                    value = default
                required = not entry.get("optional") and default is None
                resolved[entry["name"]] = (value, required)
            leaves[path] = resolved

    root_scope = {
        entry["name"]: entry["default"] if entry.get("default") is not None else ("run argument", entry["name"])
        for entry in document.get("inputs") or []
    }
    walk(document, root_scope, "")
    return leaves


def values(leaf: Mapping[str, tuple[Any, bool]]) -> dict[str, Any]:
    return {name: value for name, (value, _) in leaf.items()}


def missing_required(document: Mapping[str, Any]) -> list[str]:
    return [
        f"{path}.{name}"
        for path, leaf in leaf_inputs(document).items()
        for name, (value, required) in leaf.items()
        if required and value is UNSET
    ]


def _write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(textwrap.dedent(text))
    return path


def compile_and_hydrate(script: Path) -> dict[str, Any]:
    compiled = script.parent / "compiled.yaml"
    compile_pipeline(script, compiled)
    return PipelineHydrator(client=MagicMock(), error_policy="raise").hydrate_file(compiled).data


def _stage_child(tmp_path: Path) -> None:
    """Graph ``Child`` feeds every input it has to the Judge container."""
    (tmp_path / "judge.yaml").write_text(JUDGE_YAML)
    _write(
        tmp_path,
        "ngid_child.py",
        """\
        from tangle_cli.python_pipeline import In, pipeline, ref


        @pipeline(name="Child")
        def child(plan: In[str], verbose: In[bool] = False, limit: In[int] = 10, note: In[str] = None):
            ref(url="file://./judge.yaml").named("Judge")(plan=plan, verbose=verbose, limit=limit, note=note)
        """,
    )


def _stage_child_call(tmp_path: Path, call: str) -> Path:
    """Root ``Parent`` calls graph ``Child`` with ``call`` as its arguments."""
    _stage_child(tmp_path)
    return _write(
        tmp_path,
        "ngid_parent.py",
        f"""\
        from ngid_child import child

        from tangle_cli.python_pipeline import In, pipeline, subpipeline


        @pipeline(name="Parent")
        def parent(plan: In[str], loud: In[bool] = True):
            subpipeline(child).named("Run Child")({call})
        """,
    )


def test_an_omitted_input_takes_the_nested_graphs_default(tmp_path):
    document = compile_and_hydrate(_stage_child_call(tmp_path, "plan=plan"))

    judge = values(leaf_inputs(document)["Run Child/Judge"])
    assert judge["verbose"] == "False"
    assert judge["limit"] == "10"
    assert judge["plan"] == ("run argument", "plan")
    assert missing_required(document) == []


def test_a_value_the_caller_passes_wins_over_the_default(tmp_path):
    document = compile_and_hydrate(_stage_child_call(tmp_path, 'plan=plan, verbose=loud, limit="3"'))

    judge = values(leaf_inputs(document)["Run Child/Judge"])
    assert judge["limit"] == "3"
    assert judge["verbose"] == "True"  # the root input ``loud``, through its own default


def test_an_optional_input_without_a_default_stays_unset(tmp_path):
    document = compile_and_hydrate(_stage_child_call(tmp_path, "plan=plan"))

    assert values(leaf_inputs(document)["Run Child/Judge"])["note"] is UNSET
    assert missing_required(document) == []


def test_a_required_input_without_a_default_is_still_refused(tmp_path):
    with pytest.raises(CompileError, match="without required input 'plan'"):
        compile_and_hydrate(_stage_child_call(tmp_path, "verbose=loud"))


def test_each_call_of_a_shared_child_keeps_its_own_arguments(tmp_path):
    """One child sidecar serves both calls; only the call that omits ``limit`` takes the default."""
    _stage_child(tmp_path)
    script = _write(
        tmp_path,
        "ngid_twice.py",
        """\
        from ngid_child import child

        from tangle_cli.python_pipeline import In, pipeline, subpipeline


        @pipeline(name="Twice")
        def twice(plan: In[str]):
            subpipeline(child).named("Defaulted")(plan=plan)
            subpipeline(child).named("Explicit")(plan=plan, limit="7")
        """,
    )
    leaves = leaf_inputs(compile_and_hydrate(script))

    assert values(leaves["Defaulted/Judge"])["limit"] == "10"
    assert values(leaves["Explicit/Judge"])["limit"] == "7"
    assert values(leaves["Explicit/Judge"])["verbose"] == "False"


def test_each_nesting_level_applies_its_own_defaults(tmp_path):
    (tmp_path / "judge.yaml").write_text(JUDGE_YAML)
    _write(
        tmp_path,
        "ngid_leaf.py",
        """\
        from tangle_cli.python_pipeline import In, pipeline, ref


        @pipeline(name="Leaf")
        def leaf(plan: In[str], verbose: In[bool] = False, limit: In[int] = 10):
            ref(url="file://./judge.yaml").named("Judge")(plan=plan, verbose=verbose, limit=limit)
        """,
    )
    _write(
        tmp_path,
        "ngid_middle.py",
        """\
        from ngid_leaf import leaf

        from tangle_cli.python_pipeline import In, pipeline, subpipeline


        @pipeline(name="Middle")
        def middle(plan: In[str], verbose: In[bool] = True):
            subpipeline(leaf).named("Run Leaf")(plan=plan, verbose=verbose)
        """,
    )
    script = _write(
        tmp_path,
        "ngid_top.py",
        """\
        from ngid_middle import middle

        from tangle_cli.python_pipeline import In, pipeline, subpipeline


        @pipeline(name="Top")
        def top(plan: In[str]):
            subpipeline(middle).named("Run Middle")(plan=plan)
        """,
    )
    document = compile_and_hydrate(script)

    judge = values(leaf_inputs(document)["Run Middle/Run Leaf/Judge"])
    assert judge["verbose"] == "True"  # Middle's default, not Leaf's False
    assert judge["limit"] == "10"  # Leaf's default
    assert missing_required(document) == []


def test_a_graph_input_default_satisfies_a_required_consumer(tmp_path):
    """A child declares ``verbose`` with ``graph_input(..., default=...)`` and feeds it to a
    container whose ``verbose`` is required; the parent calls the child without it."""
    _write(
        tmp_path,
        "generate.yaml",
        """\
        name: Generate
        inputs:
          - {name: shard_id, type: String}
          - {name: verbose, type: Boolean}
        implementation:
          container:
            image: python:3.12
            command: [sh, -c, "true"]
        """,
    )
    _write(
        tmp_path,
        "ngid_relevance.py",
        """\
        from tangle_cli.python_pipeline import graph_input, pipeline, ref


        @pipeline(name="Relevance")
        def relevance():
            run_id = graph_input("run_id", "String")
            verbose = graph_input("verbose", "Boolean", default="False")
            ref(url="file://./generate.yaml").named("Generate")(shard_id=run_id, verbose=verbose)
        """,
    )
    script = _write(
        tmp_path,
        "ngid_pulse.py",
        """\
        from ngid_relevance import relevance

        from tangle_cli.python_pipeline import In, pipeline, subpipeline


        @pipeline(name="Pulse")
        def pulse(run_id: In[str]):
            subpipeline(relevance).named("Relevance")(run_id=run_id)
        """,
    )
    document = compile_and_hydrate(script)

    generate = values(leaf_inputs(document)["Relevance/Generate"])
    assert generate["verbose"] == "False"
    assert generate["shard_id"] == ("run argument", "run_id")
    assert missing_required(document) == []
