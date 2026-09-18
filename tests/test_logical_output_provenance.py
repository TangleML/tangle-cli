"""Provenance must describe the component, not where its bytes were written.

A generated component carries annotations saying where it came from:
``component_yaml_path``, ``python_original_code_path`` and the ``git_*`` set.
Those are derived from the output path, so writing to an incidental location --
a configured ``output_folder`` outside the checkout, a private staging
directory -- silently changes what the component claims about itself, and
changes its bytes, and therefore its digest, between runs of identical source.

These drive the real generator end to end. A stub cannot show any of this,
because the behaviour under test IS the derivation.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tangle_cli.component_generator import regenerate_yaml

_SOURCE = '''\
def load_orders(limit: int = 10) -> dict:
    """Load Orders

    version: "1.0"
    """
    return {"limit": limit}
'''


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A real checkout with a commit and an origin."""
    root = tmp_path / "repo"
    (root / "pipelines").mkdir(parents=True)
    (root / "pipelines" / "orders.py").write_text(_SOURCE, encoding="utf-8")
    # Pinned, not inherited: the branch name is asserted below, and a host with
    # init.defaultBranch=main or commit.gpgsign=true would otherwise change or
    # block what this fixture produces.
    subprocess.run(["git", "init", "-q", "-b", "master", str(root)], check=True)
    for args in (
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "T"),
        ("config", "commit.gpgsign", "false"),
        ("config", "tag.gpgsign", "false"),
        ("remote", "add", "origin", "https://example.invalid/acme/orders.git"),
        ("add", "-A"),
        ("commit", "-qm", "initial"),
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    return root


def _annotations(path: Path) -> dict[str, str]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return dict(document.get("metadata", {}).get("annotations", {}) or {})


def _generate(repo: Path, output: Path, *, logical: Path | None = None) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    assert regenerate_yaml(
        python_file=repo / "pipelines" / "orders.py",
        output_path=output,
        function_name="load_orders",
        logical_output_path=logical,
    )
    return output


def test_an_output_outside_the_checkout_keeps_the_repository_of_origin(
    repo: Path, tmp_path: Path
) -> None:
    """The pre-existing bug: provenance was read from the common ancestor.

    Reachable today without any staging directory, by configuring an
    ``output_folder`` outside the checkout.
    """
    annotations = _annotations(_generate(repo, tmp_path / "outside" / "component.yaml"))

    # Before the fix this dict had NO git_* keys at all: git was read from the
    # common ancestor of source and output, which is outside the checkout.
    assert "acme/orders" in annotations.get("git_remote_url", ""), annotations
    assert annotations.get("git_local_sha"), "component has no repository of origin"
    assert annotations.get("git_local_branch") == "master"
    assert annotations.get("git_relative_dir") == "pipelines"


def test_the_logical_path_and_not_the_written_path_is_recorded(
    repo: Path, tmp_path: Path
) -> None:
    """A caller writing somewhere incidental can still tell the truth."""
    logical = repo / "pipelines" / "generated" / "orders.yaml"
    annotations = _annotations(
        _generate(repo, tmp_path / "staging" / "component.yaml", logical=logical)
    )

    assert annotations["component_yaml_path"] == "generated/orders.yaml"
    assert "staging" not in annotations["component_yaml_path"]
    assert annotations["python_original_code_path"] == "orders.py"
    assert "acme/orders" in annotations.get("git_remote_url", "")


def test_two_writes_to_different_places_produce_identical_bytes(
    repo: Path, tmp_path: Path
) -> None:
    """The digest must not depend on where the file happened to be written."""
    logical = repo / "pipelines" / "generated" / "orders.yaml"
    first = _generate(repo, tmp_path / "one" / "component.yaml", logical=logical)
    second = _generate(repo, tmp_path / "two" / "component.yaml", logical=logical)

    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")


def test_no_host_or_staging_path_reaches_the_component(repo: Path, tmp_path: Path) -> None:
    """Nothing about this machine belongs in a published component."""
    logical = repo / "pipelines" / "generated" / "orders.yaml"
    annotations = _annotations(
        _generate(repo, tmp_path / "staging" / "component.yaml", logical=logical)
    )

    rendered = " ".join(
        [
            annotations.get("component_yaml_path", ""),
            annotations.get("python_original_code_path", ""),
            annotations.get("git_relative_dir", ""),
        ]
    )
    for leak in (str(tmp_path), str(Path.home()), "staging"):
        assert leak not in rendered, f"provenance leaks {leak!r}: {rendered!r}"


def test_omitting_the_logical_path_preserves_existing_behaviour(
    repo: Path, tmp_path: Path
) -> None:
    """The seam is opt-in: every existing caller must be unaffected.

    Generating in place is the ordinary case, and it must still describe
    itself exactly as before.
    """
    in_place = repo / "pipelines" / "orders.yaml"
    annotations = _annotations(_generate(repo, in_place))

    assert annotations["component_yaml_path"] == "orders.yaml"
    assert annotations["python_original_code_path"] == "orders.py"
    assert "acme/orders" in annotations.get("git_remote_url", "")
    assert annotations.get("git_relative_dir") == "pipelines"


def test_a_logical_path_equal_to_the_written_path_changes_nothing(
    repo: Path, tmp_path: Path
) -> None:
    """Passing the seam explicitly must be indistinguishable from omitting it."""
    implicit = _generate(repo, repo / "pipelines" / "a.yaml").read_text(encoding="utf-8")
    target = repo / "pipelines" / "a.yaml"
    explicit = _generate(repo, target, logical=target).read_text(encoding="utf-8")

    assert implicit == explicit


def test_an_override_written_against_the_old_signature_still_works(
    repo: Path, tmp_path: Path
) -> None:
    """A subclass predating the seam must keep working when it is unused.

    Both hops are overridable, so forwarding the new keyword unconditionally
    would break existing overrides that never asked for it.
    """
    from tangle_cli.component_generator import ComponentGenerator

    seen: dict[str, Any] = {}

    class _OldGenerator(ComponentGenerator):
        # Exactly the pre-change signature: no logical_output_path.
        def run_generation(
            self,
            *,
            python_file: Path,
            final_output: Path,
            image: str,
            func_name: str | None,
            deps_file: Path | None,
            custom_name: str | None,
            strip_code: bool,
            strip_source_path: bool,
            mode: str = "inline",
            resolve_root: Path | None = None,
            emit_generation_annotations: bool = True,
            unwrapped_inputs: dict[str, Any] | None = None,
        ) -> bool:
            seen["called"] = True
            return True

    assert _OldGenerator().regenerate_yaml(
        python_file=repo / "pipelines" / "orders.py",
        output_path=repo / "pipelines" / "orders.yaml",
        function_name="load_orders",
    )
    assert seen["called"]


def test_a_symlinked_output_keeps_its_lexical_name_in_legacy_mode(tmp_path: Path) -> None:
    """``td_legacy`` records the basename it was given, not the link target."""
    from tangle_cli.component_from_func import generate_component_yaml

    outside = tmp_path / "nogit"
    outside.mkdir()
    (outside / "orders.py").write_text(_SOURCE, encoding="utf-8")
    target = outside / "physical-target.yaml"
    alias = outside / "logical-alias.yaml"
    target.write_text("", encoding="utf-8")
    alias.symlink_to(target)

    assert generate_component_yaml(
        file_path=outside / "orders.py",
        output_path=alias,
        container_image="python:3.12",
        function_name="load_orders",
        path_annotation_mode="td_legacy",
    )
    assert _annotations(target)["component_yaml_path"] == "logical-alias.yaml"


def test_a_symlinked_source_outside_the_checkout_keeps_git_provenance(
    repo: Path, tmp_path: Path
) -> None:
    """Git is discovered from the source's REAL directory, not the link's."""
    link = tmp_path / "orders-link.py"
    link.symlink_to(repo / "pipelines" / "orders.py")

    output = tmp_path / "staging" / "component.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    assert regenerate_yaml(
        python_file=link,
        output_path=output,
        function_name="load_orders",
        logical_output_path=repo / "pipelines" / "generated" / "orders.yaml",
    )
    annotations = _annotations(output)
    assert "acme/orders" in annotations.get("git_remote_url", ""), annotations
    assert annotations.get("git_local_sha")
