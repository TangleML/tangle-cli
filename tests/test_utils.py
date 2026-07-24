"""Tests for a representative slice of :mod:`tangle_cli.utils`.

The utils module is large; this file covers the helpers that are most
likely to break silently across version bumps or refactors:
version parsing/comparison, YAML round-trip, digest stability, and
env-var-driven configuration toggles.
"""

from __future__ import annotations

import pytest

from tangle_cli.utils import (
    _REDACTED_GIT_URL,
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

    @pytest.mark.parametrize("input_url,expected", [
        # user:password userinfo is stripped from http(s) URLs
        (
            "https://user:s3cr3t@github.com/Org/repo.git",
            "https://github.com/Org/repo",
        ),
        # token-style single-field userinfo (personal access token)
        (
            "https://ghp_ABC123token@github.com/Org/repo.git",
            "https://github.com/Org/repo",
        ),
        # username-only userinfo
        (
            "https://alice@example.com/Org/repo.git",
            "https://example.com/Org/repo",
        ),
        # password-only / empty username
        (
            "https://:onlypassword@example.com/Org/repo",
            "https://example.com/Org/repo",
        ),
        # percent-encoded userinfo (e.g. an email-style username and @ in secret)
        (
            "https://user%40corp.com:p%40ss%2Fword@gitlab.com/Org/repo.git",
            "https://gitlab.com/Org/repo",
        ),
        # plain http is preserved as http (scheme not silently upgraded)
        (
            "http://user:pw@internal.example/Org/repo.git",
            "http://internal.example/Org/repo",
        ),
        # host + port is preserved while credentials are removed
        (
            "https://user:pw@example.com:8443/Org/repo.git",
            "https://example.com:8443/Org/repo",
        ),
        # GitLab CI token URL (a very common real-world leak vector)
        (
            "https://gitlab-ci-token:glcbt-xxxxxxxx@gitlab.com/Org/repo.git",
            "https://gitlab.com/Org/repo",
        ),
        # ssh:// with userinfo -> https, credentials dropped
        (
            "ssh://git@github.com/Org/repo.git",
            "https://github.com/Org/repo",
        ),
        # ssh:// with an explicit port keeps the port
        (
            "ssh://git@github.com:2222/Org/repo.git",
            "https://github.com:2222/Org/repo",
        ),
        # scp-style with a username other than git
        (
            "deploy@example.com:Org/repo.git",
            "https://example.com/Org/repo",
        ),
        # IPv6 literal host with credentials and port
        (
            "https://user:pw@[2001:db8::1]:8443/Org/repo.git",
            "https://[2001:db8::1]:8443/Org/repo",
        ),
        # fragment is preserved
        (
            "https://user:pw@github.com/Org/repo.git#readme",
            "https://github.com/Org/repo#readme",
        ),
    ])
    def test_credentials_are_stripped(self, input_url, expected):
        assert _normalize_git_url(input_url) == expected

    @pytest.mark.parametrize("secret,input_url", [
        ("s3cr3t", "https://user:s3cr3t@github.com/Org/repo.git"),
        ("ghp_ABC123token", "https://ghp_ABC123token@github.com/Org/repo.git"),
        ("glcbt-xxxxxxxx", "https://gitlab-ci-token:glcbt-xxxxxxxx@gitlab.com/Org/repo.git"),
        ("p%40ss%2Fword", "https://u:p%40ss%2Fword@gitlab.com/Org/repo.git"),
        ("onlypassword", "https://:onlypassword@example.com/Org/repo"),
    ])
    def test_no_secret_material_survives(self, secret, input_url):
        result = _normalize_git_url(input_url)
        assert secret not in result
        assert "@" not in result

    @pytest.mark.parametrize("input_url,expected", [
        # sensitive query parameters are redacted
        (
            "https://github.com/Org/repo?access_token=abc123",
            "https://github.com/Org/repo",
        ),
        (
            "https://example.com/Org/repo.git?private_token=tok&ref=main",
            "https://example.com/Org/repo?ref=main",
        ),
        (
            "https://example.com/Org/repo?password=hunter2&x=1",
            "https://example.com/Org/repo?x=1",
        ),
    ])
    def test_sensitive_query_params_redacted(self, input_url, expected):
        assert _normalize_git_url(input_url) == expected

    @pytest.mark.parametrize("input_url", [
        "https://github.com/Org/repo",
        "https://github.com/Org/repo?ref=main&path=a/b",
        "/local/path/to/repo",
        "./relative/repo",
        "file:///home/user/repo",
        "git@github.com:Org/repo.git",
    ])
    def test_credential_free_urls_are_preserved(self, input_url):
        # Non-sensitive query strings and local paths must not be corrupted.
        result = _normalize_git_url(input_url)
        assert _normalize_git_url(result) == result  # idempotent

    def test_local_paths_not_corrupted(self):
        assert _normalize_git_url("/abs/path/repo") == "/abs/path/repo"
        assert _normalize_git_url("./rel/repo") == "./rel/repo"
        assert _normalize_git_url("file:///home/user/repo.git") == "file:///home/user/repo"

    def test_windows_path_not_treated_as_scp(self):
        assert _normalize_git_url(r"C:\Users\me\repo") == r"C:\Users\me\repo"

    def test_empty_and_whitespace(self):
        assert _normalize_git_url("") == ""
        assert _normalize_git_url("  https://user:pw@github.com/Org/repo.git  ") == (
            "https://github.com/Org/repo"
        )

    def test_idempotent(self):
        once = _normalize_git_url("https://user:token@github.com/Org/repo.git")
        assert _normalize_git_url(once) == once

    @pytest.mark.parametrize("input_url,expected", [
        # oauth_token is not an exact known key but is credential-shaped
        (
            "https://github.com/Org/repo?oauth_token=SECRETVAL",
            "https://github.com/Org/repo",
        ),
        # AWS SigV4 presigned-URL params (mixed case) are dropped fail-closed
        (
            "https://host/Org/repo?X-Amz-Signature=SECRETSIG&X-Amz-Credential=AKIA/x&ref=main",
            "https://host/Org/repo?ref=main",
        ),
        (
            "https://host/Org/repo?X-Amz-Security-Token=SECRETTOK&path=a/b",
            "https://host/Org/repo?path=a%2Fb",
        ),
    ])
    def test_unknown_credential_query_keys_dropped_fail_closed(self, input_url, expected):
        result = _normalize_git_url(input_url)
        assert result == expected
        for secret in ("SECRETVAL", "SECRETSIG", "AKIA", "SECRETTOK"):
            assert secret not in result

    def test_missing_host_with_userinfo_fails_closed(self):
        # scheme present, userinfo present, but no parseable host: must not leak
        result = _normalize_git_url("https://user:secret@/Org/repo.git")
        assert result == _REDACTED_GIT_URL
        assert "secret" not in result
        assert "@" not in result

    def test_scheme_relative_userinfo_is_stripped(self):
        # ``//user:secret@host/path`` previously fell through with creds intact
        result = _normalize_git_url("//user:secret@host/Org/repo.git")
        assert result == "//host/Org/repo"
        assert "secret" not in result
        assert "@" not in result

    def test_invalid_textual_port_does_not_raise(self):
        # ``.port`` raises ValueError when read; we drop the bad port, keep host
        result = _normalize_git_url("https://user:secret@host:notaport/Org/repo.git")
        assert result == "https://host/Org/repo"
        assert "secret" not in result
        assert "@" not in result

    def test_malformed_ipv6_fails_closed(self):
        # urlsplit itself raises on an unterminated IPv6 authority
        result = _normalize_git_url("https://user:secret@[::1/Org/repo.git")
        assert result == _REDACTED_GIT_URL
        assert "secret" not in result

    def test_hostless_file_url_preserved(self):
        # a legitimately hostless scheme carries no userinfo and must survive
        assert _normalize_git_url("file:///home/user/repo.git") == "file:///home/user/repo"
