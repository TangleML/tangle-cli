"""``@Publish`` marker: decoration contract, ref propagation, sidecar emission.

``@Publish`` is a pure marker. Compiling a marked pipeline records the
declaration in the resolver sidecar beside ``local_from_python`` and publishes
NOTHING; a downstream tool reads the marker and owns the policy. These tests
cover the authoring refusals, the propagation properties that make the marker
trustworthy (fluent composition, imported/cached refs, repeated compiles), and
the emission rules (dedup agreement, subgraph children, hydration unaffected).
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from tangle_cli.pipeline_compiler import compile_pipeline
from tangle_cli.python_pipeline import Publish, ref, registered, task
from tangle_cli.python_pipeline.errors import CompileError

FIXTURES = Path(__file__).parent / "fixtures" / "python_pipeline"


def _assert_marked(entry: dict, name: str, version: str) -> None:
    """Assert the emitted shape for a declared component.

    The declaration is emitted as the resolver's OWN
    ``name``/``version``/``publisher`` fields, so ordinary hydration looks the
    published component up; ``local_from_python`` remains as the candidate used
    when none is found. ``publish`` is a separate literal ``True`` so
    publication is never inferred from those fields alone.

    ``publisher`` is the SYMBOLIC ``me``, never a resolved account id: the
    compiler is offline and must not contact the API to learn who is running it.
    """
    assert entry["name"] == name
    assert entry["version"] == version
    assert entry["publisher"] == "me"
    assert entry["publish"] is True
    assert "local_from_python" in entry


def _task_ref(**kwargs):
    """A minimal ``@task`` ref to apply ``@Publish`` to."""

    def load_orders(source: str = "orders") -> str:
        """Load orders.

        Metadata:
            Name: Load Orders
        """
        return source

    return task(image="registry.example/loader:1", **kwargs)(load_orders)


# ---------------------------------------------------------------------------
# Authoring contract: both values required, and the target must be a @task.


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"component_name": "", "version": "1.0"}, id="empty-name"),
        pytest.param({"component_name": "   ", "version": "1.0"}, id="blank-name"),
        pytest.param({"component_name": "orders", "version": ""}, id="empty-version"),
        pytest.param({"component_name": None, "version": "1.0"}, id="none-name"),
        pytest.param({"component_name": "orders", "version": 1.0}, id="non-string-version"),
        pytest.param({"component_name": "ord\ners", "version": "1.0"}, id="newline-in-name"),
        pytest.param({"component_name": "orders", "version": "1.0\x7f"}, id="delete-in-version"),
    ],
)
def test_publish_refuses_unusable_values(kwargs):
    """Both values are required, non-blank, and control-character free: they
    are echoed into diagnostics and written into a YAML document."""
    with pytest.raises(CompileError, match="@Publish"):
        Publish(**kwargs)


def test_publish_requires_both_values():
    with pytest.raises(TypeError):
        Publish(component_name="orders")  # type: ignore[call-arg]


def test_publish_strips_surrounding_whitespace():
    declared = Publish(component_name="  orders-loader  ", version=" 1.0 ")
    assert (declared.component_name, declared.version) == ("orders-loader", "1.0")


def test_publish_refuses_a_plain_function():
    """Applied BELOW @task (wrong order) the decorator sees a function."""
    with pytest.raises(CompileError, match="can only publish a @task component"):

        @Publish(component_name="orders-loader", version="1.0")
        def load_orders() -> str:
            return "orders"


def test_publish_refuses_a_plain_ref():
    """``ref()`` names an existing component; there is no local source to
    generate and publish from."""
    with pytest.raises(CompileError, match="can only publish a @task component"):
        Publish(component_name="orders-loader", version="1.0")(
            ref("file://component.yaml")
        )


def test_publish_refuses_a_registered_ref():
    """``@registered`` points at an ALREADY published component."""

    @registered(fragment="run-query", gen_config="gs://bucket/gen_config.yaml")
    def run_query(sql: str = "SELECT 1") -> str:
        """Run a query."""
        return sql

    with pytest.raises(CompileError, match="can only publish a @task component"):
        Publish(component_name="run-query", version="1.0")(run_query)


def test_publish_refuses_a_second_declaration():
    published = Publish(component_name="orders-loader", version="1.0")(_task_ref())
    with pytest.raises(CompileError, match="already declared"):
        Publish(component_name="other", version="2.0")(published)


# ---------------------------------------------------------------------------
# Propagation: the marker must survive everything that produces a NEW ref.


def test_the_marker_is_a_real_field_carried_by_fluent_composition():
    """``_replace`` re-copies only a dunder allowlist, so a dynamically
    stamped attribute would be dropped here. Real dataclass fields are copied
    by ``dataclasses.replace`` itself."""
    published = Publish(component_name="orders-loader", version="1.0")(_task_ref())

    derived = published.named("first").bind(source="a")

    assert derived._task_publish_name == "orders-loader"
    assert derived._task_publish_version == "1.0"


def test_the_marker_does_not_leak_onto_an_undeclared_task():
    plain = _task_ref()
    Publish(component_name="orders-loader", version="1.0")(plain)

    assert plain._task_publish_name is None
    assert plain._task_publish_version is None


# ---------------------------------------------------------------------------
# Sidecar emission.


def _keys_named(data: Any, key: str) -> bool:
    """Whether ``key`` appears as a mapping key anywhere in ``data``."""
    if isinstance(data, dict):
        return key in data or any(_keys_named(value, key) for value in data.values())
    if isinstance(data, list):
        return any(_keys_named(item, key) for item in data)
    return False


def _sidecar(pipeline_path: Path, out: Path):
    result = compile_pipeline(pipeline_path, out)
    return yaml.safe_load(result.components_path.read_text())


def _project_with(tmp_path: Path, body: str) -> Path:
    """Write a one-module pipeline project and return its pipeline path."""
    src = tmp_path / "project" / "src"
    src.mkdir(parents=True)
    pipeline_path = src / "pipeline.py"
    pipeline_path.write_text(body, encoding="utf-8")
    return pipeline_path


_PUBLISHING_PIPELINE = (
    "from tangle_cli.python_pipeline import Out, Publish, pipeline, task\n\n"
    "@Publish(component_name='orders-loader', version='1.0')\n"
    "@task(image='registry.example/loader:1')\n"
    "def load_orders(source: str = 'orders') -> str:\n"
    '    """Load orders.\n\n'
    "    Metadata:\n"
    "        Name: Load Orders\n"
    '    """\n'
    "    return source\n\n"
    "@pipeline('Publishing Pipeline')\n"
    "def publishing_pipeline() -> Out[str]:\n"
    "    loaded = load_orders(source='orders')\n"
    "    return loaded\n"
)


def test_a_declared_task_emits_a_publish_marker_beside_local_from_python(tmp_path):
    pipeline_path = _project_with(tmp_path, _PUBLISHING_PIPELINE)

    sidecar = _sidecar(pipeline_path, pipeline_path.parent / "compiled.yaml")

    entry = sidecar["load-orders"]
    _assert_marked(entry, "orders-loader", "1.0")
    # The local resolver is untouched and stays the fallback candidate.
    assert entry["local_from_python"]["function"] == "load_orders"


def test_an_undeclared_task_emits_no_publish_key(tmp_path):
    """Absence is expressed by omission, never by a null or empty mapping."""
    pipeline_path = _project_with(
        tmp_path, _PUBLISHING_PIPELINE.replace(
            "@Publish(component_name='orders-loader', version='1.0')\n", ""
        )
    )

    sidecar = _sidecar(pipeline_path, pipeline_path.parent / "compiled.yaml")

    assert "publish" not in sidecar["load-orders"]


def test_compiling_a_declared_pipeline_publishes_nothing(tmp_path, monkeypatch):
    """Compile records intent and performs no registry call. The marker is
    inert: publication is a separate, downstream decision."""
    import tangle_cli.component_publisher as component_publisher

    def _fail(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("compile must not publish")

    monkeypatch.setattr(
        component_publisher.ComponentPublisher, "publish_components", _fail, raising=False
    )
    pipeline_path = _project_with(tmp_path, _PUBLISHING_PIPELINE)

    sidecar = _sidecar(pipeline_path, pipeline_path.parent / "compiled.yaml")

    assert sidecar["load-orders"]["name"] == "orders-loader"


def test_two_call_sites_of_one_declared_task_share_one_entry_and_one_marker(tmp_path):
    """Dedup is unchanged by publication: one generated component, one entry,
    one marker."""
    pipeline_path = _project_with(
        tmp_path,
        "from tangle_cli.python_pipeline import Out, Publish, pipeline, task\n\n"
        "@Publish(component_name='orders-loader', version='1.0')\n"
        "@task(image='registry.example/loader:1')\n"
        "def load_orders(source: str = 'orders') -> str:\n"
        '    """Load orders.\n\n'
        "    Metadata:\n"
        "        Name: Load Orders\n"
        '    """\n'
        "    return source\n\n"
        "@pipeline('Twice Pipeline')\n"
        "def twice_pipeline() -> Out[str]:\n"
        "    load_orders.named('first')(source='a')\n"
        "    return load_orders.named('second')(source='b')\n",
    )

    sidecar = _sidecar(pipeline_path, pipeline_path.parent / "compiled.yaml")

    assert list(sidecar) == ["load-orders"]
    _assert_marked(sidecar["load-orders"], "orders-loader", "1.0")


def test_two_declarations_folded_into_one_component_are_refused(tmp_path):
    """Publication is deliberately NOT part of the dedup identity, so two
    refs that generate the same component collapse into one entry that can
    carry only one marker. Reachable by applying @Publish functionally to one
    of two otherwise identical refs — so this refusal is not merely defensive.
    """
    pipeline_path = _project_with(
        tmp_path,
        "from tangle_cli.python_pipeline import Out, Publish, pipeline, task\n\n"
        "def load_orders(source: str = 'orders') -> str:\n"
        '    """Load orders.\n\n'
        "    Metadata:\n"
        "        Name: Load Orders\n"
        '    """\n'
        "    return source\n\n"
        "base = task(image='registry.example/loader:1')(load_orders)\n"
        "declared = Publish(component_name='orders-loader', version='1.0')(base)\n\n"
        "@pipeline('Disagreeing Pipeline')\n"
        "def disagreeing_pipeline() -> Out[str]:\n"
        "    base.named('plain')(source='a')\n"
        "    return declared.named('declared')(source='b')\n",
    )

    with pytest.raises(CompileError, match="@Publish disagreement"):
        compile_pipeline(pipeline_path, pipeline_path.parent / "compiled.yaml")


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param("", ", mode='inline'", id="lean-then-explicit"),
        pytest.param(", mode='inline'", "", id="explicit-then-lean"),
    ],
)
def test_the_marker_survives_the_canonical_spelling_choice(tmp_path, first, second):
    """``@task()`` and ``@task(mode='inline')`` are one component spelled two
    ways, and the compiler keeps the leanest spelling regardless of call
    order. Whichever emitted form wins must still carry the declaration --
    in the order where the SECOND call site supplies the canonical form, the
    representative is replaced after the marker was first recorded.
    """
    src = tmp_path / "project" / "src"
    src.mkdir(parents=True)
    pipeline_path = src / "pipeline.py"
    pipeline_path.write_text(
        "from tangle_cli.python_pipeline import Out, Publish, pipeline, task\n\n"
        "def load_orders(source: str = 'orders') -> str:\n"
        '    """Load orders.\n\n    Metadata:\n        Name: Load Orders\n    """\n'
        "    return source\n\n"
        "publish = Publish(component_name='orders-loader', version='1.0')\n"
        f"first = publish(task(image='registry.example/loader:1'{first})(load_orders))\n"
        f"second = publish(task(image='registry.example/loader:1'{second})(load_orders))\n\n"
        "@pipeline('Spelling Pipeline')\n"
        "def spelling_pipeline() -> Out[str]:\n"
        "    a = first.named('a')(source='a')\n"
        "    b = second.named('b')(source='b')\n"
        "    return b\n",
        encoding="utf-8",
    )

    sidecar = _sidecar(pipeline_path, src / "compiled.yaml")

    assert list(sidecar) == ["load-orders"]
    _assert_marked(sidecar["load-orders"], "orders-loader", "1.0")


def test_two_different_components_each_carry_their_own_marker(tmp_path):
    """Multiple distinct publications per compile are representable upstream:
    the one-per-ship rule is a downstream policy, not a compiler rule."""
    pipeline_path = _project_with(
        tmp_path,
        "from tangle_cli.python_pipeline import Out, Publish, pipeline, task\n\n"
        "@Publish(component_name='orders-loader', version='1.0')\n"
        "@task(image='registry.example/loader:1')\n"
        "def load_orders(source: str = 'orders') -> str:\n"
        '    """Load orders.\n\n    Metadata:\n        Name: Load Orders\n    """\n'
        "    return source\n\n"
        "@Publish(component_name='orders-shipper', version='2.0')\n"
        "@task(image='registry.example/shipper:1')\n"
        "def ship_orders(source: str = 'orders') -> str:\n"
        '    """Ship orders.\n\n    Metadata:\n        Name: Ship Orders\n    """\n'
        "    return source\n\n"
        "@pipeline('Two Publications Pipeline')\n"
        "def two_publications_pipeline() -> Out[str]:\n"
        "    loaded = load_orders(source='orders')\n"
        "    shipped = ship_orders(source=loaded)\n"
        "    return shipped\n",
    )

    sidecar = _sidecar(pipeline_path, pipeline_path.parent / "compiled.yaml")

    assert sidecar["load-orders"]["name"] == "orders-loader"
    assert sidecar["ship-orders"]["name"] == "orders-shipper"
    assert sidecar["ship-orders"]["version"] == "2.0"


# ---------------------------------------------------------------------------
# The case that defeated a capture-based design: the declaration lives in a
# module that is ALREADY imported, so its decorators do not run again.


def _shared_task_project(tmp_path: Path, module: str) -> Path:
    """A project whose pipeline imports its declared @task from ``module``."""
    src = tmp_path / "project" / "src"
    src.mkdir(parents=True)
    (src / f"{module}.py").write_text(
        "from tangle_cli.python_pipeline import Publish, task\n\n"
        "@Publish(component_name='orders-loader', version='1.0')\n"
        "@task(image='registry.example/loader:1')\n"
        "def load_orders(source: str = 'orders') -> str:\n"
        '    """Load orders.\n\n'
        "    Metadata:\n"
        "        Name: Load Orders\n"
        '    """\n'
        "    return source\n",
        encoding="utf-8",
    )
    pipeline_path = src / "pipeline.py"
    pipeline_path.write_text(
        "from tangle_cli.python_pipeline import Out, pipeline\n"
        f"from {module} import load_orders\n\n"
        "@pipeline('Imported Pipeline')\n"
        "def imported_pipeline() -> Out[str]:\n"
        "    loaded = load_orders(source='orders')\n"
        "    return loaded\n",
        encoding="utf-8",
    )
    return pipeline_path


def test_a_declaration_in_an_imported_module_is_emitted(tmp_path):
    """The marker travels on the ref, so it does not matter which module the
    declaration was written in."""
    module = f"pubshared_{abs(hash(str(tmp_path))):x}"
    pipeline_path = _shared_task_project(tmp_path, module)

    sidecar = _sidecar(pipeline_path, pipeline_path.parent / "compiled.yaml")

    assert sidecar["load-orders"]["name"] == "orders-loader"


def test_a_preloaded_declaring_module_changes_nothing(tmp_path):
    """The decisive property, and the exact case that defeats a capture-based
    design: when the declaring module is ALREADY imported its ``@Publish``
    does not execute again, so a mechanism that observed decoration would see
    nothing. The marker rides on the ref, so a cold compile and a compile with
    the module preloaded produce the same sidecar.
    """
    module = f"pubcached_{abs(hash(str(tmp_path))):x}"
    pipeline_path = _shared_task_project(tmp_path, module)
    source_dir = pipeline_path.parent

    cold = _sidecar(pipeline_path, source_dir / "cold.yaml")

    sys.path.insert(0, str(source_dir))
    try:
        preloaded = importlib.import_module(module)
        # Verify the preload resolved to the module just written, rather than
        # some same-named module left behind by another test.
        assert Path(preloaded.__file__ or "") == source_dir / f"{module}.py"
        assert preloaded.load_orders._task_publish_name == "orders-loader"

        warm = _sidecar(pipeline_path, source_dir / "warm.yaml")
    finally:
        sys.path.remove(str(source_dir))
        sys.modules.pop(module, None)

    _assert_marked(cold["load-orders"], "orders-loader", "1.0")
    assert warm == cold


# ---------------------------------------------------------------------------
# Subgraph children and hydration.


def test_a_declaration_inside_a_subpipeline_child_is_emitted(tmp_path):
    """A child graph gets its own ``<child>.components.yaml``; the marker must
    ride into the child's sidecar, not the root's."""
    src = tmp_path / "project" / "src"
    src.mkdir(parents=True)
    pipeline_path = src / "pipeline.py"
    pipeline_path.write_text(
        "from tangle_cli.python_pipeline import Out, Publish, pipeline, subpipeline, task\n\n"
        "@Publish(component_name='orders-loader', version='1.0')\n"
        "@task(image='registry.example/loader:1')\n"
        "def load_orders(source: str = 'orders') -> str:\n"
        '    """Load orders.\n\n    Metadata:\n        Name: Load Orders\n    """\n'
        "    return source\n\n"
        "@pipeline('Child Pipeline')\n"
        "def child_pipeline() -> Out[str]:\n"
        "    loaded = load_orders(source='orders')\n"
        "    return loaded\n\n"
        "@pipeline('Parent Pipeline')\n"
        "def parent_pipeline() -> Out[str]:\n"
        "    child = subpipeline(child_pipeline)()\n"
        "    return child\n",
        encoding="utf-8",
    )
    out = src / "compiled.yaml"

    result = compile_pipeline(pipeline_path, out, pipeline_name="Parent Pipeline")

    assert len(result.subgraph_paths) == 1
    child_graph = result.subgraph_paths[0]
    child_sidecar_path = child_graph.with_name(f"{child_graph.stem}.components.yaml")
    child_sidecar = yaml.safe_load(child_sidecar_path.read_text())
    _assert_marked(child_sidecar["load-orders"], "orders-loader", "1.0")


def test_the_marker_is_stripped_from_the_baked_operation_program():
    """``@Publish`` is authoring-only. The baked program drops the authoring
    import, so a surviving ``@Publish`` line would raise ``NameError`` at
    container startup for every marked task.
    """
    from tangle_cli.component_from_func import _strip_authoring_constructs

    baked = _strip_authoring_constructs(
        "from tangle_cli.python_pipeline import Publish, task\n\n"
        '@Publish(component_name="orders-loader", version="1.0")\n'
        '@task(image="registry.example/loader:1")\n'
        'def load_orders(source: str = "orders") -> str:\n'
        "    return source\n"
    )

    assert "Publish" not in baked
    assert "tangle_cli.python_pipeline" not in baked
    # The decisive check: it must RUN with no authoring names in scope.
    namespace: dict[str, Any] = {}
    exec(compile(baked, "<baked>", "exec"), namespace)
    assert namespace["load_orders"]("orders") == "orders"


def test_the_generated_component_command_carries_no_authoring_construct(tmp_path):
    """End-to-end counterpart: the command actually baked into the resolved
    component must be free of the decorator and its import."""
    from unittest.mock import MagicMock

    from tangle_cli.pipeline_hydrator import PipelineHydrator

    pipeline_path = _project_with(tmp_path, _PUBLISHING_PIPELINE)
    out = pipeline_path.parent / "compiled.yaml"
    compile_pipeline(pipeline_path, out)

    hydrated = PipelineHydrator(client=MagicMock()).hydrate_file(out)

    tasks = hydrated.data["implementation"]["graph"]["tasks"]
    spec = next(iter(tasks.values()))["componentRef"]["spec"]
    program = "\n".join(
        part for part in spec["implementation"]["container"]["command"] if isinstance(part, str)
    )
    assert "@Publish" not in program
    assert "from tangle_cli.python_pipeline import" not in program
    assert "def load_orders" in program


def test_a_marked_component_still_hydrates_from_local_source(tmp_path):
    """Hydration reads ``local_from_python`` and ignores the marker, so a
    declared component resolves exactly as an undeclared one does."""
    from unittest.mock import MagicMock

    from tangle_cli.pipeline_hydrator import PipelineHydrator

    declared_path = _project_with(tmp_path, _PUBLISHING_PIPELINE)
    declared_out = declared_path.parent / "compiled.yaml"
    compile_pipeline(declared_path, declared_out)

    hydrated = PipelineHydrator(client=MagicMock()).hydrate_file(declared_out)

    tasks = hydrated.data["implementation"]["graph"]["tasks"]
    task_spec = next(iter(tasks.values()))
    # The component resolved from local source, exactly as an unmarked one.
    assert "spec" in task_spec["componentRef"]
    assert task_spec["componentRef"]["spec"]["name"] == "Load Orders"
    # The marker did not travel into the hydrated graph. Checked STRUCTURALLY:
    # the rendered YAML embeds the pipeline source, whose own identifiers
    # contain the substring "publish".
    assert not _keys_named(hydrated.data, "publish")


def test_compilation_emits_a_symbolic_publisher_without_contacting_the_api(tmp_path):
    """The emitted owner must be symbolic, not a resolved account.

    Resolving an id at compile time would make compilation require
    authentication and network access, and would bake one author's account into
    an artifact that another author may legitimately rebuild.
    """
    pipeline_path = _project_with(tmp_path, _PUBLISHING_PIPELINE)

    sidecar = _sidecar(pipeline_path, pipeline_path.parent / "compiled.yaml")

    entry = sidecar["load-orders"]
    assert entry["publisher"] == "me"


def test_the_symbolic_publisher_is_matched_exactly_and_case_sensitively() -> None:
    """A literal account id that merely looks like the sentinel stays literal."""
    from tangle_cli.authenticated_identity import is_symbolic_me

    assert is_symbolic_me("me")
    for other in ("ME", "Me", " me", "me ", "me@example.com", "", None, True):
        assert not is_symbolic_me(other)
