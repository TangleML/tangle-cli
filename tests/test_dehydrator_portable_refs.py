"""Digest/name dehydration emits portable, semantically verified references.

A published digest is trusted only when it resolves to the component actually
in hand, and explicit DIGEST/NAME always ship a local copy behind an ordered
resolve config, so the output stays usable when the library is unreachable.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from tangle_cli.models import ComponentInfo
from tangle_cli.pipeline_dehydrator import (
    DehydrateChoice,
    PipelineDehydrator,
    ResolveManifestUnavailableError,
)
from tangle_cli.pipeline_hydrator import PipelineHydrator
from tangle_cli.utils import compute_spec_digest, compute_text_digest, dump_yaml

LEAF = {"name": "L", "implementation": {"container": {"image": "i", "command": ["c"]}}}
OTHER = {"name": "L", "implementation": {"container": {"image": "other", "command": ["x"]}}}
NAMELESS = {"implementation": LEAF["implementation"]}
INNER = {"name": "Inner", "implementation": {"graph": {"tasks": {"leaf": {"componentRef": {"spec": LEAF}}}}}}
SPEC_DIGEST = compute_spec_digest(LEAF)
TEXT_DIGEST = compute_text_digest(dump_yaml(LEAF))


class Library:
    """Published digests resolve to ``specs``; ``error`` simulates an outage."""

    def __init__(self, specs=None, infos=(), error: Exception | None = None, successors=None) -> None:
        self.specs = specs or {}
        self.infos = list(infos)
        self.error = error
        self.successors = successors or {}

    def resolve_digest(self, digest: str) -> str:
        return self.successors.get(digest, digest)

    def get_component_spec(self, digest: str) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        if digest not in self.specs:
            raise KeyError(digest)
        return copy.deepcopy(self.specs[digest])

    def find_existing_components(self, *args: Any, **kwargs: Any) -> list[ComponentInfo]:
        if self.error is not None:
            raise self.error
        return list(self.infos)


def _pipeline(ref: dict[str, Any], task: str = "t") -> dict[str, Any]:
    return {"name": "O", "implementation": {"graph": {"tasks": {task: {"componentRef": ref}}}}}


def _dehydrate(tmp_path: Path, choice: str, document: dict[str, Any], client: Any) -> dict[str, Any]:
    return PipelineDehydrator({"": choice}, output_file=tmp_path / "out.yaml", client=client).dehydrate(
        document
    )


def _ref(result: dict[str, Any], task: str = "t") -> dict[str, Any]:
    return result["implementation"]["graph"]["tasks"][task]["componentRef"]


def _fragment(tmp_path: Path, result: dict[str, Any], task: str = "t") -> list[dict[str, Any]]:
    url = _ref(result, task)["url"]
    assert url.startswith("resolve://./out.components.yaml#")
    manifest = yaml.safe_load((tmp_path / "out.components.yaml").read_text(encoding="utf-8"))
    return manifest[url.split("#", 1)[1]]


def _leaf_digests(document: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for task in document["implementation"]["graph"]["tasks"].values():
        spec = task["componentRef"]["spec"]
        if "graph" in spec["implementation"]:
            found += _leaf_digests(spec)
        else:
            found.append(compute_spec_digest({k: v for k, v in spec.items() if not k.startswith("_")}))
    return found


# --------------------------------------------------------------------------- AUTO


@pytest.mark.parametrize(
    ("label", "ref", "published", "expected"),
    [
        ("carried-digest-matches", {"digest": TEXT_DIGEST, "spec": LEAF}, {TEXT_DIGEST: LEAF}, "digest"),
        ("carried-digest-is-stale", {"digest": TEXT_DIGEST, "spec": LEAF}, {TEXT_DIGEST: OTHER}, "file"),
        ("spec-digest-indexed", {"spec": LEAF}, {SPEC_DIGEST: LEAF}, "digest"),
        ("text-digest-indexed-only", {"spec": LEAF}, {TEXT_DIGEST: LEAF}, "file"),
    ],
)
def test_auto_trusts_a_digest_only_when_it_resolves_to_this_component(
    tmp_path: Path, label: str, ref: dict[str, Any], published: dict[str, Any], expected: str
) -> None:
    """Existence in the library is not enough: a stale or foreign digest that
    happens to be published must not replace the spec it no longer describes."""
    result = _dehydrate(tmp_path, DehydrateChoice.AUTO, _pipeline(ref), Library(published))

    emitted = _ref(result)
    assert ("digest" in emitted) == (expected == "digest"), label
    assert ("url" in emitted and emitted["url"].startswith("file://")) == (expected == "file"), label


# ------------------------------------------------------------------------- DIGEST


def test_a_verified_digest_is_primary_with_a_marked_local_fallback(tmp_path: Path) -> None:
    ref = {"digest": TEXT_DIGEST, "spec": LEAF}

    result = _dehydrate(tmp_path, DehydrateChoice.DIGEST, _pipeline(ref), Library({TEXT_DIGEST: LEAF}))

    fragment = _fragment(tmp_path, result)
    local = fragment[-1]["local"]
    assert fragment == [{"digest": TEXT_DIGEST, "fallback_on_error": True}, {"local": local}]
    assert yaml.safe_load((tmp_path / local).read_text(encoding="utf-8")) == LEAF


@pytest.mark.parametrize(
    ("label", "library"),
    [
        ("stale", Library({TEXT_DIGEST: OTHER})),
        ("unpublished", Library({})),
        ("offline", Library(error=ConnectionError("unreachable"))),
    ],
)
def test_an_unverifiable_digest_ships_the_local_copy_alone(
    tmp_path: Path, label: str, library: Library
) -> None:
    """DIGEST never emits a digest it could not verify, and never fails for it."""
    ref = {"digest": TEXT_DIGEST, "spec": LEAF}

    result = _dehydrate(tmp_path, DehydrateChoice.DIGEST, _pipeline(ref), library)

    fragment = _fragment(tmp_path, result)
    assert len(fragment) == 1 and set(fragment[0]) == {"local"}, label


def test_digest_dehydration_without_an_output_path_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With nowhere portable to go it raises, instead of writing into the cwd."""
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ResolveManifestUnavailableError):
        PipelineDehydrator({"": DehydrateChoice.DIGEST}, client=Library()).dehydrate(
            _pipeline({"spec": LEAF})
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["raises", "none"])
@pytest.mark.parametrize("choice", [DehydrateChoice.DIGEST, DehydrateChoice.NAME])
def test_no_usable_client_degrades_to_the_local_copy(tmp_path: Path, choice: str, failure: str) -> None:
    """Client creation may exit on missing credentials or yield nothing; the
    dehydration still succeeds, shipping only the local copy."""

    class NoClient(PipelineDehydrator):
        def _get_client(self):
            if failure == "raises":
                raise SystemExit("no credentials")
            return None

    result = NoClient({"": choice}, output_file=tmp_path / "out.yaml").dehydrate(
        _pipeline({"digest": SPEC_DIGEST, "spec": LEAF})
    )

    fragment = _fragment(tmp_path, result)
    assert len(fragment) == 1 and set(fragment[0]) == {"local"}


@pytest.mark.parametrize("primary_fails", [False, True], ids=["primary-ok", "primary-fails"])
def test_a_nested_graph_is_fully_dehydrated_and_rehydrates_to_the_original(
    tmp_path: Path, primary_fails: bool
) -> None:
    document = _pipeline({"spec": INNER}, task="sub")
    out = tmp_path / "out.yaml"
    result = _dehydrate(tmp_path, DehydrateChoice.DIGEST, document, Library({SPEC_DIGEST: LEAF}))
    out.write_text(dump_yaml(result), encoding="utf-8")

    # Fully dehydrated: every ref at every level is a pure locator.
    written = [yaml.safe_load(out.read_text(encoding="utf-8"))]
    written += [yaml.safe_load(p.read_text(encoding="utf-8")) for p in (tmp_path / "components").glob("*.yaml")]
    for doc in written:
        for task in doc.get("implementation", {}).get("graph", {}).get("tasks", {}).values():
            assert set(task["componentRef"]) == {"url"}
    library = Library({SPEC_DIGEST: LEAF}, error=ConnectionError("down") if primary_fails else None)
    hydrated = PipelineHydrator(client=library).hydrate_file(out).data

    assert _leaf_digests(hydrated) == _leaf_digests(document)


# --------------------------------------------------------------------------- NAME


def _real_client(rows: list[dict[str, Any]], specs: dict[str, dict[str, Any]]) -> Any:
    """The REAL client, stubbed only at its HTTP seams.

    Owner discovery goes through ``ComponentInspector.inspect_by_digest`` on
    this client, so the production request/row shape is what is exercised.
    """
    from tangle_cli.client import TangleApiClient
    from tangle_cli.models import ComponentSpec

    client = TangleApiClient(base_url="https://review.invalid", include_env_credentials=False)
    client._published_component_rows = lambda **kw: [
        copy.deepcopy(r) for r in rows if kw.get("digest") in (None, r["digest"])
    ]
    client.get_component_spec = lambda digest: ComponentSpec.from_dict(copy.deepcopy(specs[digest]))
    return client


def _row(owner: Any, digest: str = SPEC_DIGEST, name: str = "L") -> dict[str, Any]:
    return {"name": name, "digest": digest, "published_by": owner}


def test_a_name_reference_is_pinned_to_its_inspected_owner(tmp_path: Path) -> None:
    """NAME pins the AUTHOR, not the version: hydration resolves that owner's
    latest candidate, and the local copy is an availability fallback only."""
    client = _real_client([_row("user-123")], {SPEC_DIGEST: LEAF})

    result = _dehydrate(tmp_path, DehydrateChoice.NAME, _pipeline({"spec": LEAF}), client)

    fragment = _fragment(tmp_path, result)
    assert fragment[0] == {"name": "L", "publisher": "user-123", "fallback_on_error": True}
    assert set(fragment[1]) == {"local"}


@pytest.mark.parametrize(
    ("label", "rows", "specs", "spec"),
    [
        ("unpublished", [], {SPEC_DIGEST: LEAF}, LEAF),
        ("symbolic-me", [_row("me")], {SPEC_DIGEST: LEAF}, LEAF),
        ("empty-owner", [_row("")], {SPEC_DIGEST: LEAF}, LEAF),
        # The carried digest is published, but as different content.
        ("different-content", [_row("user-1")], {SPEC_DIGEST: OTHER}, LEAF),
        # Content matches, so only the no-name rule can stop the lookup.
        ("nameless", [_row("user-1", compute_spec_digest(NAMELESS))], {compute_spec_digest(NAMELESS): NAMELESS}, NAMELESS),
    ],
)
def test_name_is_emitted_only_for_an_inspected_matching_publication(
    tmp_path: Path, label: str, rows: list[dict[str, Any]], specs: dict[str, Any], spec: dict[str, Any]
) -> None:
    """Without a trustworthy inspected owner a name lookup would be global,
    so a same-named component by any other author could resolve instead."""
    client = _real_client(rows, specs)

    result = _dehydrate(tmp_path, DehydrateChoice.NAME, _pipeline({"spec": spec}), client)

    fragment = _fragment(tmp_path, result)
    assert len(fragment) == 1 and set(fragment[0]) == {"local"}, label


# ------------------------------------------------- fragment identity and reuse


@pytest.mark.parametrize(
    ("label", "second", "library"),
    [
        ("two-verified-locators", {"spec": LEAF}, Library({TEXT_DIGEST: LEAF, SPEC_DIGEST: LEAF})),
        ("verified-and-unpublished", {"digest": "f" * 64, "spec": LEAF}, Library({TEXT_DIGEST: LEAF})),
    ],
)
def test_equal_specs_with_different_primaries_get_distinct_fragments(
    tmp_path: Path, label: str, second: dict[str, Any], library: Library
) -> None:
    """The fragment addresses the whole entry list, not just the content."""
    document = {
        "name": "O",
        "implementation": {
            "graph": {
                "tasks": {
                    "a": {"componentRef": {"digest": TEXT_DIGEST, "spec": LEAF}},
                    "b": {"componentRef": second},
                }
            }
        },
    }

    result = _dehydrate(tmp_path, DehydrateChoice.DIGEST, document, library)

    a, b = _fragment(tmp_path, result, "a"), _fragment(tmp_path, result, "b")
    assert a != b, label
    assert a[-1] == b[-1], "one content-addressed local copy serves both"


@pytest.mark.parametrize("entry", ["dehydrate_file", "retargeted_attributes"])
@pytest.mark.parametrize("choice", [DehydrateChoice.DIGEST, DehydrateChoice.AUTO])
def test_a_reused_dehydrator_gives_every_output_its_own_bundle(
    tmp_path: Path, choice: str, entry: str
) -> None:
    """Caches from an earlier output must not point a later one at the old
    bundle -- that output would ship no local copy of its own.

    Every output shares one component (a stale cache would be hit) and has one
    of its own (a leaked fragment could not hide behind a shared key).
    """
    first = tmp_path / "out0" / "out.yaml"
    dehydrator = PipelineDehydrator({"": choice}, output_file=first, client=Library({}))
    for index in range(2):
        own = {"name": f"Own{index}", "implementation": {"container": {"image": f"own{index}"}}}
        document = {
            "name": "O",
            "implementation": {
                "graph": {"tasks": {"shared": {"componentRef": {"spec": LEAF}}, "own": {"componentRef": {"spec": own}}}}
            },
        }
        out_dir = tmp_path / f"out{index}"
        out_dir.mkdir(exist_ok=True)
        out = out_dir / "out.yaml"
        if entry == "dehydrate_file":
            (out_dir / "in.yaml").write_text(dump_yaml(document), encoding="utf-8")
            dehydrator.dehydrate_file(out_dir / "in.yaml", out)
        else:
            dehydrator.output_file = out
            dehydrator.components_dir = out_dir / "components"
            out.write_text(dump_yaml(dehydrator.dehydrate(document)), encoding="utf-8")

        text = out.read_text(encoding="utf-8")
        assert "../" not in text, "a ref escaped into another output's bundle"
        assert len(list((out_dir / "components").glob("*.yaml"))) == 2, "a local copy is missing"
        if choice == DehydrateChoice.DIGEST:
            manifest = yaml.safe_load((out_dir / "out.components.yaml").read_text(encoding="utf-8"))
            assert len(manifest) == 2, "fragments leaked from the earlier output"


# ------------------------------------------------ deprecation successors


@pytest.mark.parametrize("choice", [DehydrateChoice.DIGEST, DehydrateChoice.AUTO])
def test_export_verifies_the_carried_digest_itself_not_its_successor(
    tmp_path: Path, choice: str
) -> None:
    """Accepted behavior, pinned so it is not "fixed" back: the digest whose
    OWN spec matches is emitted, even if it already has a different successor."""
    successor = "5" * 64
    library = Library({SPEC_DIGEST: LEAF, successor: OTHER}, successors={SPEC_DIGEST: successor})

    result = _dehydrate(tmp_path, choice, _pipeline({"digest": SPEC_DIGEST, "spec": LEAF}), library)

    emitted = _ref(result) if choice == DehydrateChoice.AUTO else _fragment(tmp_path, result)[0]
    assert emitted["digest"] == SPEC_DIGEST


@pytest.mark.parametrize("choice", [DehydrateChoice.DIGEST, DehydrateChoice.AUTO])
def test_hydration_follows_a_later_successor_by_design(tmp_path: Path, choice: str) -> None:
    """Deprecated successors may change content; that is accepted product
    behavior for both choices. The local copy covers availability, not drift."""
    out = tmp_path / "out.yaml"
    result = _dehydrate(tmp_path, choice, _pipeline({"spec": LEAF}), Library({SPEC_DIGEST: LEAF}))
    out.write_text(dump_yaml(result), encoding="utf-8")
    successor = "7" * 64
    later = Library({SPEC_DIGEST: LEAF, successor: OTHER}, successors={SPEC_DIGEST: successor})

    hydrated = PipelineHydrator(client=later).hydrate_file(out).data

    assert _leaf_digests(hydrated) == [compute_spec_digest(OTHER)]


# ---------------------------------------------------- hydrator: marked fallback


def _write_config(tmp_path: Path, config: Any) -> Path:
    (tmp_path / "c.yaml").write_text(dump_yaml(LEAF), encoding="utf-8")
    (tmp_path / "r.yaml").write_text(dump_yaml({"frag": config}), encoding="utf-8")
    pipeline = tmp_path / "p.yaml"
    pipeline.write_text(dump_yaml(_pipeline({"url": "resolve://./r.yaml#frag"})), encoding="utf-8")
    return pipeline


@pytest.mark.parametrize(
    ("label", "primary", "error"),
    [
        ("digest-404", {"digest": SPEC_DIGEST}, None),
        ("digest-network", {"digest": SPEC_DIGEST}, ConnectionError("down")),
        ("name-network", {"name": "L", "publisher": "user-1"}, ConnectionError("down")),
    ],
)
def test_a_marked_primary_that_raises_falls_through_to_the_local_copy(
    tmp_path: Path, label: str, primary: dict[str, Any], error: Exception | None
) -> None:
    pipeline = _write_config(tmp_path, [{**primary, "fallback_on_error": True}, {"local": "./c.yaml"}])

    hydrated = PipelineHydrator(client=Library({}, error=error)).hydrate_file(pipeline).data

    assert _leaf_digests(hydrated) == [SPEC_DIGEST], label


@pytest.mark.parametrize(
    ("label", "config"),
    [
        ("unmarked-multi-entry", [{"digest": SPEC_DIGEST}, {"local": "./c.yaml"}]),
        (
            "marked-last-entry",
            [{"digest": SPEC_DIGEST, "fallback_on_error": True}, {"digest": SPEC_DIGEST, "fallback_on_error": True}],
        ),
        ("marked-single-entry", {"digest": SPEC_DIGEST, "fallback_on_error": True}),
    ],
)
def test_errors_still_propagate_outside_the_generated_fallback_contract(
    tmp_path: Path, label: str, config: Any
) -> None:
    """Hand-authored, single and last entries keep today's loud failure."""
    pipeline = _write_config(tmp_path, config)

    with pytest.raises(ConnectionError):
        PipelineHydrator(client=Library(error=ConnectionError("down"))).hydrate_file(pipeline)


@pytest.mark.parametrize("choice", [DehydrateChoice.DIGEST, DehydrateChoice.FILE, DehydrateChoice.AUTO])
def test_extract_subgraphs_false_dehydrates_only_the_leaves_in_place(tmp_path: Path, choice: str) -> None:
    """A caller that represents nested graphs itself keeps every boundary
    inline; only the leaf components inside it are dehydrated, in place, with
    no subgraph file written."""
    document = _pipeline({"spec": INNER}, task="sub")

    result = PipelineDehydrator(
        {"": choice}, output_file=tmp_path / "out.yaml", client=Library({}), extract_subgraphs=False
    ).dehydrate(document)

    boundary = result["implementation"]["graph"]["tasks"]["sub"]["componentRef"]
    assert boundary["spec"]["name"] == "Inner", "the graph boundary stays inline"
    leaf = boundary["spec"]["implementation"]["graph"]["tasks"]["leaf"]["componentRef"]
    assert "spec" not in leaf and set(leaf) == {"url"}, "the leaf inside it is dehydrated"
    assert not (tmp_path / "subgraphs").exists()
    assert not [p for p in tmp_path.rglob("*.yaml") if "inner" in p.name.lower()]
