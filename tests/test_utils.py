"""Tests for a representative slice of :mod:`tangle_cli.utils`.

The utils module is large; this file covers the helpers that are most
likely to break silently across version bumps or refactors:
version parsing/comparison, YAML round-trip, digest stability, and
env-var-driven configuration toggles.
"""

from __future__ import annotations

import pytest

from tangle_cli.utils import (
    UnsetVarError,
    _normalize_git_url,
    apply_defaults,
    check_versions,
    clamp,
    compare_versions,
    compute_spec_digest,
    compute_text_digest,
    dump_yaml,
    expand_vars,
    get_version_from_data,
    parse_yaml_string,
    set_component_yaml_path,
    tangle_verbose_enabled,
)


class TestClamp:
    def test_within_bounds(self):
        assert clamp(5, 0, 10) == 5

    def test_lower_bound(self):
        assert clamp(-1, 0, 10) == 0

    def test_upper_bound(self):
        assert clamp(11, 0, 10) == 10


class TestTangleVerboseEnabled:
    @pytest.mark.parametrize("value,expected", [
        ("1", True), ("true", True), ("True", True), ("yes", True),
        ("0", False), ("false", False), ("", False), ("anything-else", False),
    ])
    def test_env_var_truthiness(self, value, expected, monkeypatch):
        monkeypatch.setenv("TANGLE_VERBOSE", value)
        assert tangle_verbose_enabled() is expected

    def test_unset(self, monkeypatch):
        monkeypatch.delenv("TANGLE_VERBOSE", raising=False)
        assert tangle_verbose_enabled() is False


class TestExpandVars:
    def test_basic_substitution(self):
        assert expand_vars("hello ${name}", {"name": "world"}) == "hello world"

    def test_default_value(self):
        assert expand_vars("hello ${name:-friend}", {}) == "hello friend"

    def test_default_ignored_when_set(self):
        assert expand_vars("hello ${name:-friend}", {"name": "alice"}) == "hello alice"

    def test_unset_without_default_raises(self):
        with pytest.raises(UnsetVarError):
            expand_vars("hello ${name}", {})


class TestVersionHelpers:
    def test_get_version_from_data_in_annotations(self):
        data = {"metadata": {"annotations": {"version": "1.2.3"}}}
        assert get_version_from_data(data) == "1.2.3"

    def test_get_version_from_data_top_level_fallback(self):
        # Top-level ``version`` field also accepted.
        data = {"version": "0.1"}
        assert get_version_from_data(data) == "0.1"

    def test_get_version_from_data_missing(self):
        # Returns None when no version annotation is set.
        assert get_version_from_data({}) is None

    def test_compare_versions(self):
        assert compare_versions("1.2.0", "1.2.0") == 0
        assert compare_versions("1.2.0", "1.2.1") < 0
        assert compare_versions("2.0.0", "1.9.9") > 0
        # Short vs. long forms compare component-wise.
        assert compare_versions("1.2", "1.2.0") == 0

    def test_check_versions_equal_returns_false(self):
        # ``check_versions`` returns ``True`` when an update should proceed.
        # Equal versions => no update needed.
        assert check_versions("1.0", "1.0") is False

    def test_check_versions_different_returns_true(self):
        assert check_versions("1.0", "1.1") is True

    def test_check_versions_no_latest_proceeds(self):
        # No latest version published yet => first publish proceeds.
        assert check_versions("1.0", None) is True


class TestYamlRoundtrip:
    def test_parse_dump_preserves_keys(self):
        text = "a: 1\nb:\n  c: 2\n"
        data = parse_yaml_string(text)
        assert data == {"a": 1, "b": {"c": 2}}
        # dump_yaml should preserve insertion order for a plain dict.
        dumped = dump_yaml(data)
        round_tripped = parse_yaml_string(dumped)
        assert round_tripped == data

    def test_multiline_string_uses_literal_block(self):
        # The custom dumper renders multiline strings with the ``|`` block
        # scalar so they read nicely in component YAML files.
        data = {"description": "line one\nline two\n"}
        dumped = dump_yaml(data)
        assert "|" in dumped
        assert "line one" in dumped and "line two" in dumped


class TestDigest:
    def test_text_digest_stable_and_unique(self):
        d1 = compute_text_digest("hello")
        d2 = compute_text_digest("hello")
        d3 = compute_text_digest("hello!")
        assert d1 == d2
        assert d1 != d3
        # Reasonable shape — non-empty string, deterministic.
        assert isinstance(d1, str) and d1

    def test_spec_digest_independent_of_key_order(self):
        a = {"name": "c", "version": "1.0", "inputs": []}
        b = {"inputs": [], "version": "1.0", "name": "c"}
        assert compute_spec_digest(a) == compute_spec_digest(b)


class TestApplyDefaults:
    def test_entry_values_take_precedence(self):
        # ``apply_defaults`` returns a merged dict; entry values win on collision.
        result = apply_defaults({"a": 1}, {"a": 99, "b": 2, "c": 3})
        assert result == {"a": 1, "b": 2, "c": 3}

    def test_list_of_dicts(self):
        result = apply_defaults(
            [{"a": 1}, {"a": 2, "b": "keep"}],
            {"a": 99, "b": "default"},
        )
        assert result == [{"a": 1, "b": "default"}, {"a": 2, "b": "keep"}]


class TestSetComponentYamlPath:
    def test_splits_relative_path(self):
        ann: dict[str, str] = {}
        set_component_yaml_path("a/b/comp.yaml", ann)
        assert ann == {"git_relative_dir": "a/b", "component_yaml_path": "comp.yaml"}

    def test_bare_filename(self):
        ann: dict[str, str] = {}
        set_component_yaml_path("comp.yaml", ann)
        assert ann == {"component_yaml_path": "comp.yaml"}

    def test_no_overwrite_mode(self):
        ann = {"component_yaml_path": "old.yaml"}
        set_component_yaml_path("new.yaml", ann, overwrite=False)
        assert ann["component_yaml_path"] == "old.yaml"


class TestNormalizeGitUrl:
    @pytest.mark.parametrize("input_url,expected", [
        ("git@github.com:Org/repo.git", "https://github.com/Org/repo"),
        ("https://github.com/Org/repo.git", "https://github.com/Org/repo"),
        ("https://github.com/Org/repo", "https://github.com/Org/repo"),
        ("ssh://git@github.com/Org/repo.git", "https://github.com/Org/repo"),
    ])
    def test_normalization(self, input_url, expected):
        assert _normalize_git_url(input_url) == expected
