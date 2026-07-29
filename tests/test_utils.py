"""Tests for a representative slice of :mod:`tangle_cli.utils`.

The utils module is large; this file covers the helpers that are most
likely to break silently across version bumps or refactors:
version parsing/comparison, YAML round-trip, digest stability, and
env-var-driven configuration toggles.
"""

from __future__ import annotations

import time

import pytest

from tangle_cli.utils import (
    _REDACTED_GIT_URL,
    UnsetVarError,
    _collapse_percent_octets,
    _decoded_delimiter_unsafe,
    _embedded_assignment_names,
    _is_sensitive_query_key,
    _normalize_git_url,
    _screen_reveals,
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


# Credential parameter names that must never survive normalization, in any
# position.  Covers provider-specific spellings, identifier suffixes
# (``token_id``, ``AWSAccessKeyId``), and version suffixes (``authToken2``).
MUST_REDACT_KEYS = (
    "access_token", "refresh_token", "id_token", "sessionToken", "accessToken",
    "client_secret", "user_credential", "x-api-key", "apiKey", "auth_token",
    "private_key", "authorization", "authorization-bearer", "bearer", "cookie",
    "set-cookie", "oauth", "oauth_token", "password", "passwd", "pwd",
    "passphrase", "secret", "session", "sig", "signature", "token", "key",
    "AWSAccessKeyId", "GoogleAccessId", "token_id", "tokenId", "apiKeyId",
    "authToken2", "x-api-key-v2", "session_id", "X-Amz-Signature",
    "X-Amz-Credential", "X-Amz-Security-Token", "presigned_url", "signed-url",
)

# Diagnostic parameter names that must survive intact.  These are the names a
# terminal-word classifier is most at risk of over-redacting.
MUST_KEEP_KEYS = (
    "max_tokens", "total_tokens", "token-count", "tokens_used", "num-keys",
    "function_signature", "SignatureVersion", "authorization_url",
    "X-Amz-Date", "X-Amz-Expires", "X-Amz-Algorithm", "X-Amz-SignedHeaders",
    "account_id", "request_id", "trace_id", "user_id", "sessionStart",
    "keychain-path", "signed-off-by", "author", "authors", "monkey", "keyboard",
    "ref", "path", "branch", "tag", "sha", "sha1", "commit", "rev", "depth",
    "subdirectory", "filename", "format", "page", "per_page", "recursive",
)


def _encoded_at_depth(text: str, depth: int) -> str:
    from urllib.parse import quote

    for _ in range(depth):
        text = quote(text, safe="")
    return text


def _fully_collapsed(text: str) -> str:
    while True:
        decoded = _collapse_percent_octets(text)
        if decoded == text:
            return text
        text = decoded


def _staggered(text: str, depth: int) -> str:
    """Encode *text* so each collapse pass reveals exactly one layer.

    Re-encoding every character except the leading ``%`` keeps that ``%``
    separated from its hex digits, so a pass cannot see a complete octet tower
    and decodes only one level (``@`` -> ``%40`` -> ``%%34%30`` -> ...).
    """
    if depth <= 0:
        return text
    encoded = "".join(f"%{ord(char):02X}" for char in text)
    for _ in range(depth - 1):
        encoded = "%" + "".join(f"%{ord(char):02X}" for char in encoded[1:])
    return encoded


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

    # ------------------------------------------------------------------
    # Credential query-key canonicalization
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("key", [
        # separator and casing variants of the same credential name
        "x-api-key", "X-Api-Key", "x_api_key", "api-key", "API-KEY", "api_key",
        "apikey", "apiKey", "ApiKey", "APIKey",
        # header-style credentials
        "authorization", "Authorization", "authorization-bearer", "bearer",
        "cookie", "Cookie", "set-cookie", "Set-Cookie",
        # provider-specific and terminal credential forms
        "cloud-auth", "cloud_auth", "CLOUD-AUTH", "cloudAuth",
        "x-auth-token", "access-token", "accessToken", "refresh-token",
        "private-token", "personal-access-token", "oauth_token",
        "X-Amz-Signature", "X-Amz-Credential", "X-Amz-Security-Token",
        "deploy-key", "ssh-key", "session", "gh-session",
        # bare credential names
        "token", "key", "auth", "sig", "signature", "secret", "password",
        "passwd", "pwd", "passphrase",
    ])
    def test_credential_key_variants_are_sensitive(self, key):
        assert _is_sensitive_query_key(key) is True
        result = _normalize_git_url(f"https://host/Org/repo.git?{key}=SECRETVAL")
        assert result == "https://host/Org/repo"
        assert "SECRETVAL" not in result

    @pytest.mark.parametrize("key", [
        # benign git metadata whose names merely contain a credential word
        "ref", "path", "branch", "tag", "sha", "commit", "rev", "depth",
        "author", "authors", "author-date", "signed-off-by", "design",
        "monkey", "keyboard", "keychain-path", "token-count", "tokens_used",
        "max_tokens", "num-keys", "total_tokens", "sessionStart",
        "subdirectory", "filename", "format", "page", "per_page", "recursive",
        # AWS SigV4 params that are not themselves secret
        "X-Amz-Date", "X-Amz-Expires", "X-Amz-Algorithm", "X-Amz-SignedHeaders",
    ])
    def test_benign_keys_are_not_sensitive(self, key):
        assert _is_sensitive_query_key(key) is False
        result = _normalize_git_url(f"https://host/Org/repo.git?{key}=keepme")
        assert "keepme" in result

    def test_mixed_query_keeps_metadata_and_drops_credentials(self):
        result = _normalize_git_url(
            "https://host/Org/repo.git?ref=main&x-api-key=SECRETVAL&author=me&cookie=SESSIONVAL"
        )
        assert result == "https://host/Org/repo?ref=main&author=me"
        assert "SECRETVAL" not in result
        assert "SESSIONVAL" not in result

    # ------------------------------------------------------------------
    # Empty / malformed authority: credentials must never survive
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("input_url", [
        # empty authority pushes the userinfo into the path
        "https:///user:SECRETVAL@Org/repo.git",
        "https:////user:SECRETVAL@Org/repo.git",
        "http:///user:SECRETVAL@Org/repo",
        "ssh:///user:SECRETVAL@Org/repo.git",
        "git:///user:SECRETVAL@Org/repo.git",
        "file:///user:SECRETVAL@Org/repo.git",
        # token-only userinfo, no password
        "https:///SECRETVAL@Org/repo.git",
        # too few slashes after the scheme
        "https:/user:SECRETVAL@Org/repo.git",
        "https:user:SECRETVAL@Org/repo.git",
        "ssh:/user:SECRETVAL@Org/repo.git",
        # userinfo with an empty host
        "https://user:SECRETVAL@/Org/repo.git",
        "https://SECRETVAL@/Org/repo.git",
        "https://@/Org/repo.git",
        # malformed IPv6 authority
        "https://user:SECRETVAL@[::1/Org/repo.git",
        "ssh://user:SECRETVAL@[fe80::1/Org/repo.git",
    ])
    def test_unparseable_authority_fails_closed(self, input_url):
        result = _normalize_git_url(input_url)
        assert result == _REDACTED_GIT_URL
        assert "SECRETVAL" not in result
        assert "@" not in result

    @pytest.mark.parametrize("input_url,expected", [
        # a real SCP remote must still be rewritten, credentials dropped
        ("git@github.com:Org/repo.git", "https://github.com/Org/repo"),
        ("user:SECRETVAL@github.com:Org/repo.git", "https://github.com/Org/repo"),
        ("github.com:Org/repo.git", "https://github.com/Org/repo"),
        # a scheme must never be mistaken for the hostname
        ("https://user:SECRETVAL@github.com/Org/repo.git", "https://github.com/Org/repo"),
        ("ssh://user:SECRETVAL@github.com/Org/repo.git", "https://github.com/Org/repo"),
    ])
    def test_scheme_is_never_treated_as_host(self, input_url, expected):
        result = _normalize_git_url(input_url)
        assert result == expected
        assert "SECRETVAL" not in result
        assert "https://https" not in result

    def test_hostless_url_without_credentials_is_preserved(self):
        # an empty authority alone is not a leak; keep the path intelligible
        assert _normalize_git_url("https:///Org/repo.git") == "https:///Org/repo"
        assert _normalize_git_url("file:///srv/git/repo.git") == "file:///srv/git/repo"
        # ``@`` outside the first path segment is part of the path, not userinfo
        assert _normalize_git_url("file:///home/me@corp/repo.git") == "file:///home/me@corp/repo"

    def test_hostless_url_query_is_redacted(self):
        result = _normalize_git_url("file:///srv/repo.git?ref=main&token=SECRETVAL")
        assert "SECRETVAL" not in result
        assert "ref=main" in result

    @pytest.mark.parametrize("input_url", [
        "",
        "   ",
        "not a url",
        "://",
        "https://",
        "@",
        ":",
        "///",
        "https://host:notaport/Org/repo.git",
        "https://host:99999/Org/repo.git",
        "https://host:/Org/repo.git",
        r"C:\Users\me\repo",
        r"C:\Users\me@corp\repo",
        "/abs/path/repo.git",
        "./rel/repo",
    ])
    def test_adversarial_inputs_never_raise(self, input_url):
        # No parser exception may escape into callers for any remote shape.
        result = _normalize_git_url(input_url)
        assert isinstance(result, str)

    @pytest.mark.parametrize("input_url", [
        "https://user:SECRETVAL@github.com/Org/repo.git",
        "https:///user:SECRETVAL@Org/repo.git",
        "https://github.com/Org/repo.git?x-api-key=SECRETVAL",
        "https://user:SECRETVAL@[::1/Org/repo.git",
        "https://user:SECRETVAL@github.com:8443/Org/repo.git?ref=main#frag",
        "git@github.com:Org/repo.git",
        "//user:SECRETVAL@github.com/Org/repo.git",
        "https://github.com/Org/repo?ref=main&path=a/b",
        "file:///home/user/repo.git",
        r"C:\Users\me\repo",
    ])
    def test_idempotent_across_shapes(self, input_url):
        once = _normalize_git_url(input_url)
        assert _normalize_git_url(once) == once
        assert "SECRETVAL" not in once

    def test_browse_link_is_buildable_after_normalization(self):
        # The normalized URL must remain a usable base for ``/blob/{ref}/{path}``.
        normalized = _normalize_git_url(
            "https://user:SECRETVAL@github.com/Org/repo.git?access_token=SECRETVAL"
        )
        link = f"{normalized}/blob/main/src/component.yaml"
        assert link == "https://github.com/Org/repo/blob/main/src/component.yaml"
        assert "SECRETVAL" not in link

    def test_browse_link_from_redacted_url_carries_no_credentials(self):
        # When the remote is unparseable the placeholder propagates instead of a
        # half-built link that still contains the secret.
        normalized = _normalize_git_url("https:///user:SECRETVAL@Org/repo.git")
        link = f"{normalized}/blob/main/src/component.yaml"
        assert normalized == _REDACTED_GIT_URL
        assert "SECRETVAL" not in link

    # ------------------------------------------------------------------
    # git transport-helper prefixes (``<transport>::<address>``)
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("input_url,expected", [
        # the inner transport is sanitized, not returned verbatim
        (
            "git::https://user:SECRETVAL@host/Org/repo.git",
            "https://host/Org/repo",
        ),
        # a credential-free helper address still normalizes cleanly
        (
            "git::https://host/Org/repo.git",
            "https://host/Org/repo",
        ),
        # non-git helpers behave identically; the prefix is not part of a link
        (
            "hg::https://user:SECRETVAL@host/Org/repo",
            "https://host/Org/repo",
        ),
        (
            "bzr::https://host/Org/repo",
            "https://host/Org/repo",
        ),
        # ssh:// inner transport keeps the existing ssh -> https rewrite
        (
            "git::ssh://git@github.com:2222/Org/repo.git",
            "https://github.com:2222/Org/repo",
        ),
        # nesting is unwrapped rather than mistaken for an address
        (
            "git::git::https://user:SECRETVAL@host/Org/repo.git",
            "https://host/Org/repo",
        ),
        # query and fragment inside a helper address are sanitized too
        (
            "git::https://u:SECRETVAL@host/Org/repo.git?access_token=SECRETVAL#readme",
            "https://host/Org/repo#readme",
        ),
    ])
    def test_transport_helper_prefix_is_sanitized(self, input_url, expected):
        result = _normalize_git_url(input_url)
        assert result == expected
        assert "SECRETVAL" not in result
        assert "@" not in result

    @pytest.mark.parametrize("input_url", [
        # ``ext::`` takes a command, not a URL: it cannot be sanitized structurally
        "ext::ssh -p 2222 user:SECRETVAL@host %S /Org/repo.git",
        "ext::git-remote-helper user:SECRETVAL@host",
        # an SCP-style helper address would need the scheme guessed
        "git::user:SECRETVAL@github.com:Org/repo.git",
        "git::git@github.com:Org/repo.git",
        # a helper address whose own authority is unparseable
        "git::https:///user:SECRETVAL@Org/repo.git",
        "git::https://user:SECRETVAL@[::1/Org/repo.git",
        # an empty address carries no host to normalize toward
        "git::",
        "git::   ",
        # nesting beyond the unwrap bound must not be returned raw
        "git::git::git::git::git::https://user:SECRETVAL@host/Org/repo.git",
    ])
    def test_unsanitizable_transport_helper_fails_closed(self, input_url):
        result = _normalize_git_url(input_url)
        assert result == _REDACTED_GIT_URL
        assert "SECRETVAL" not in result
        assert "@" not in result

    # ------------------------------------------------------------------
    # Structural URI scheme recognition (no enumerated scheme list)
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("input_url", [
        # an unenumerated scheme with a mangled authority must not be rewritten
        # as ``https://<scheme>/…``, which would re-embed the userinfo
        "foo:/user:SECRETVAL@host/Org/repo",
        "git+https:/user:SECRETVAL@host/Org/repo",
        "hg:/user:SECRETVAL@host/Org/repo",
        "svn+ssh:/user:SECRETVAL@host/Org/repo",
        "x-custom.transport:/user:SECRETVAL@host/Org/repo",
        # scheme-like prefix with a non-slash hier-part: the scheme would be read
        # as the host and the credentials would land in the path
        "foo:user:SECRETVAL@host/Org/repo",
        "hg:user:SECRETVAL@host/Org/repo",
        "https:user:SECRETVAL@Org/repo.git",
        "a:SECRETVAL@Org/repo",
    ])
    def test_scheme_like_input_never_reaches_scp_rewrite(self, input_url):
        result = _normalize_git_url(input_url)
        assert result == _REDACTED_GIT_URL
        assert "SECRETVAL" not in result
        assert "@" not in result

    @pytest.mark.parametrize("input_url,expected", [
        # unenumerated schemes with a real authority are sanitized, not redacted
        ("foo://user:SECRETVAL@host/Org/repo", "foo://host/Org/repo"),
        ("git+https://user:SECRETVAL@host/Org/repo.git", "git+https://host/Org/repo"),
        ("hg://user:SECRETVAL@host/Org/repo", "hg://host/Org/repo"),
        # genuine SCP remains correct, including a bare host and an IP host
        ("git@github.com:Org/repo.git", "https://github.com/Org/repo"),
        ("github.com:Org/repo.git", "https://github.com/Org/repo"),
        ("localhost:Org/repo.git", "https://localhost/Org/repo"),
        ("192.168.1.10:Org/repo.git", "https://192.168.1.10/Org/repo"),
        ("deploy@example.com:Org/repo.git", "https://example.com/Org/repo"),
        # a hostless unenumerated scheme is metadata, not a leak
        ("hg:/Org/repo", "hg:/Org/repo"),
    ])
    def test_structural_scheme_keeps_scp_and_unknown_schemes_correct(
        self, input_url, expected
    ):
        result = _normalize_git_url(input_url)
        assert result == expected
        assert "SECRETVAL" not in result

    @pytest.mark.parametrize("input_url,expected", [
        (r"C:\Users\me\repo", r"C:\Users\me\repo"),
        (r"C:\Users\me\repo.git", r"C:\Users\me\repo"),
        (r"D:/src/repo.git", r"D:/src/repo"),
        ("/abs/path/repo.git", "/abs/path/repo"),
        ("./rel/repo", "./rel/repo"),
        ("../up/repo.git", "../up/repo"),
        ("repo.git", "repo"),
    ])
    def test_local_and_windows_paths_survive_structural_detection(
        self, input_url, expected
    ):
        assert _normalize_git_url(input_url) == expected

    # ------------------------------------------------------------------
    # Fragment sanitization
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("input_url,expected", [
        # benign document anchors are useful and are preserved byte-for-byte
        ("https://github.com/Org/repo.git#readme", "https://github.com/Org/repo#readme"),
        ("https://github.com/Org/repo#L42", "https://github.com/Org/repo#L42"),
        (
            "https://github.com/Org/repo#L10-L20",
            "https://github.com/Org/repo#L10-L20",
        ),
        (
            "https://github.com/Org/repo#issuecomment-123",
            "https://github.com/Org/repo#issuecomment-123",
        ),
        # a query-like fragment with only benign keys is preserved unchanged
        (
            "https://github.com/Org/repo#section=intro&page=2",
            "https://github.com/Org/repo#section=intro&page=2",
        ),
        ("file:///srv/repo.git#readme", "file:///srv/repo#readme"),
    ])
    def test_benign_fragments_are_preserved(self, input_url, expected):
        assert _normalize_git_url(input_url) == expected

    @pytest.mark.parametrize("input_url,expected", [
        # OAuth implicit-flow style credentials live in the fragment
        (
            "https://github.com/Org/repo#access_token=SECRETVAL",
            "https://github.com/Org/repo",
        ),
        (
            "https://github.com/Org/repo#oauth=SECRETVAL",
            "https://github.com/Org/repo",
        ),
        (
            "https://github.com/Org/repo#signature=SECRETVAL",
            "https://github.com/Org/repo",
        ),
        (
            "https://github.com/Org/repo#credential=SECRETVAL",
            "https://github.com/Org/repo",
        ),
        (
            "https://github.com/Org/repo#X-Amz-Security-Token=SECRETVAL",
            "https://github.com/Org/repo",
        ),
        # benign fragment parameters survive alongside a redacted credential
        (
            "https://github.com/Org/repo#ref=main&access_token=SECRETVAL",
            "https://github.com/Org/repo#ref=main",
        ),
        # a fragment carrying a nested URL or userinfo cannot be classified
        # parameter-by-parameter, so it is dropped whole
        (
            "https://github.com/Org/repo#https://u:SECRETVAL@host/p",
            "https://github.com/Org/repo",
        ),
        (
            "https://github.com/Org/repo#user:SECRETVAL@host",
            "https://github.com/Org/repo",
        ),
        (
            "https://github.com/Org/repo#next=https%3A%2F%2Fu%3ASECRETVAL%40h%2Fp",
            "https://github.com/Org/repo",
        ),
        # hostless schemes take the same path
        ("file:///srv/repo.git#access_token=SECRETVAL", "file:///srv/repo"),
        # userinfo and a credential fragment together
        (
            "https://u:SECRETVAL@github.com/Org/repo.git#access_token=SECRETVAL",
            "https://github.com/Org/repo",
        ),
    ])
    def test_credential_fragments_are_redacted(self, input_url, expected):
        result = _normalize_git_url(input_url)
        assert result == expected
        assert "SECRETVAL" not in result

    # ------------------------------------------------------------------
    # must-redact / must-keep cross-product over parameter positions
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("key", MUST_REDACT_KEYS)
    @pytest.mark.parametrize("position", ["query", "fragment"])
    def test_credential_keys_redacted_in_every_position(self, key, position):
        sep = "?" if position == "query" else "#"
        result = _normalize_git_url(
            f"https://host/Org/repo.git{sep}{key}=SECRETVAL"
        )
        assert result == "https://host/Org/repo"
        assert "SECRETVAL" not in result

    @pytest.mark.parametrize("key", MUST_KEEP_KEYS)
    @pytest.mark.parametrize("position", ["query", "fragment"])
    def test_diagnostic_keys_kept_in_every_position(self, key, position):
        sep = "?" if position == "query" else "#"
        result = _normalize_git_url(
            f"https://host/Org/repo.git{sep}{key}=keepme"
        )
        assert "keepme" in result
        assert key in result

    @pytest.mark.parametrize("credential_key", MUST_REDACT_KEYS)
    def test_credential_key_never_masks_adjacent_diagnostics(self, credential_key):
        # A credential parameter must be dropped without taking its neighbours
        # with it, in both the query and the fragment.
        result = _normalize_git_url(
            f"https://host/Org/repo.git"
            f"?ref=main&{credential_key}=SECRETVAL&max_tokens=8"
            f"#L42=1&{credential_key}=SECRETVAL&page=2"
        )
        assert "SECRETVAL" not in result
        assert "ref=main" in result
        assert "max_tokens=8" in result
        assert "page=2" in result

    @pytest.mark.parametrize("key", MUST_KEEP_KEYS)
    def test_diagnostic_keys_are_not_credentials(self, key):
        assert _is_sensitive_query_key(key) is False

    @pytest.mark.parametrize("key", MUST_REDACT_KEYS)
    def test_credential_keys_are_recognized(self, key):
        assert _is_sensitive_query_key(key) is True

    # ------------------------------------------------------------------
    # Adversarial idempotency
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("input_url", [
        "git::https://user:SECRETVAL@host/Org/repo.git",
        "git::git::https://user:SECRETVAL@host/Org/repo.git",
        "ext::ssh user:SECRETVAL@host %S /Org/repo",
        "git::",
        "foo:/user:SECRETVAL@host/Org/repo",
        "foo:user:SECRETVAL@host/Org/repo",
        "https:user:SECRETVAL@Org/repo.git",
        "git+https://user:SECRETVAL@host/Org/repo.git",
        "hg:/Org/repo",
        "https://github.com/Org/repo#access_token=SECRETVAL",
        "https://github.com/Org/repo#https://u:SECRETVAL@host/p",
        "https://github.com/Org/repo#ref=main&access_token=SECRETVAL",
        "https://github.com/Org/repo#readme",
        "https://u:SECRETVAL@host/Org/repo.git?AWSAccessKeyId=SECRETVAL#sig=SECRETVAL",
        "https://host/Org/repo.git?max_tokens=8&session_id=SECRETVAL",
        "localhost:Org/repo.git",
        "192.168.1.10:Org/repo.git",
        r"C:\Users\me\repo.git",
        "../up/repo.git",
        _REDACTED_GIT_URL,
        "https:///user%3ASECRETVAL%40Org/repo.git",
        "https://user%3ASECRETVAL%40evil.example/Org/repo.git",
        "github.com:user%3ASECRETVAL%40Org/repo.git",
        "https://github.com/Org/repo#access_token%3DSECRETVAL",
        "https:///user%253ASECRETVAL%2540Org/repo.git",
    ])
    def test_idempotent_and_credential_free_across_adversarial_shapes(self, input_url):
        once = _normalize_git_url(input_url)
        twice = _normalize_git_url(once)
        assert twice == once
        assert "SECRETVAL" not in once
        # A second pass must not re-introduce credentials or start raising.
        assert "SECRETVAL" not in twice

    @pytest.mark.parametrize("input_url", [
        "git::",
        "::",
        ":::",
        "git::::",
        "a::b",
        "::https://host/repo",
        "git::#frag",
        "git::?query",
        "#",
        "#access_token=SECRETVAL",
        "?access_token=SECRETVAL",
        "https://host/repo#",
        "https://host/repo?",
        "https://host/repo#=",
        "https://host/repo#&&&",
        "https://host/repo#%00",
        "\n",
        "https://host/repo.git#a\nb",
        "git::https://host/repo\n",
    ])
    def test_degenerate_inputs_never_raise_or_leak(self, input_url):
        result = _normalize_git_url(input_url)
        assert isinstance(result, str)
        assert "SECRETVAL" not in result

    # ------------------------------------------------------------------
    # Credentials in shapes that bypass the authority handling
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("input_url", [
        # Windows drive paths and bare local paths take early/late exits that
        # never see the authority code, but can still carry a credential.
        r"C:/src/repo.git?access_token=SECRETVAL",
        r"C:\src\repo#access_token=SECRETVAL",
        r"C:/src/repouser:SECRETVAL@host",
        "/abs/path/repo?access_token=SECRETVAL",
        "../up/repo#oauth_token=SECRETVAL",
        "org/repo%user:SECRETVAL@ssh",
        "SECRETVAL@host",
        "user:SECRETVAL@host",
        # userinfo in a path segment, which survives once a scheme-like colon
        # has been consumed as the SCP host separator
        "..git::host/repouser:SECRETVAL@",
        "https://github.com/Org/a:SECRETVAL@b",
        "//foo:/user:SECRETVAL@",
        # a benign key whose value embeds a nested credential
        "https://github.com/Org/repo?ref=main?access_token=SECRETVAL",
        "https://github.com/Org/repo?ref=https://u:SECRETVAL@host/p",
        "https://github.com/Org/repo?ref=mainuser:SECRETVAL@org",
        "https://github.com/Org/repo#section=intro&next=https://u:SECRETVAL@h/p",
    ])
    def test_credentials_never_survive_non_authority_shapes(self, input_url):
        assert "SECRETVAL" not in _normalize_git_url(input_url)

    @pytest.mark.parametrize("input_url,expected", [
        # the same exits must still redact by name while keeping the path
        (
            r"C:/src/repo.git?ref=main&access_token=SECRETVAL",
            r"C:/src/repo?ref=main",
        ),
        ("/abs/path/repo.git#readme", "/abs/path/repo#readme"),
        ("../up/repo?ref=main", "../up/repo?ref=main"),
        # benign values that merely resemble credential text are preserved
        (
            "https://github.com/Org/repo?ref=refs/heads/topic",
            "https://github.com/Org/repo?ref=refs/heads/topic",
        ),
        (
            "https://github.com/Org/repo?X-Amz-Algorithm=AWS4-HMAC-SHA256",
            "https://github.com/Org/repo?X-Amz-Algorithm=AWS4-HMAC-SHA256",
        ),
        (
            "https://github.com/Org/repo?ref=feature@2",
            "https://github.com/Org/repo?ref=feature@2",
        ),
    ])
    def test_non_authority_shapes_keep_diagnostics(self, input_url, expected):
        assert _normalize_git_url(input_url) == expected

    # ------------------------------------------------------------------
    # Percent-encoded userinfo
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("input_url", [
        # empty or slash-mangled authority pushes encoded userinfo into the path
        "https:///user%3ASECRETVAL%40Org/repo.git",
        "https:///user%3aSECRETVAL%40Org/repo.git",
        "https:/user%3ASECRETVAL%40Org/repo.git",
        # one separator literal, the other encoded
        "https:///user%3ASECRETVAL@Org/repo.git",
        "https:///user:SECRETVAL%40Org/repo.git",
        # nested segment of a well-formed URL
        "https://github.com/Org/user%3ASECRETVAL%40b/repo.git",
        # with no literal ``@`` the userinfo hides inside what ``urlsplit``
        # reports as the hostname
        "https://user%3ASECRETVAL%40evil.example/Org/repo.git",
        "//user%3ASECRETVAL%40github.com/Org/repo.git",
        # SCP path and bare relative path
        "github.com:user%3ASECRETVAL%40Org/repo.git",
        "user%3ASECRETVAL%40host/repo.git",
        # doubly encoded, one decode short of the credential
        "https:///user%253ASECRETVAL%2540Org/repo.git",
        "https://github.com/Org/user%253ASECRETVAL%2540b/repo.git",
    ])
    def test_percent_encoded_path_userinfo_fails_closed(self, input_url):
        assert _normalize_git_url(input_url) == _REDACTED_GIT_URL

    @pytest.mark.parametrize("input_url", [
        # the query parser's single decode leaves these one decode short
        "https://github.com/Org/repo?ref=user%253ASECRETVAL%2540h",
        "https://github.com/Org/repo?ref=https%253A%252F%252Fu%253ASECRETVAL%2540h",
        "https://github.com/Org/repo#user%3ASECRETVAL%40h",
        # query-like only once decoded, so it cannot be filtered by name
        "https://github.com/Org/repo#access_token%3DSECRETVAL",
    ])
    def test_percent_encoded_credentials_in_query_and_fragment_are_dropped(
        self, input_url
    ):
        assert _normalize_git_url(input_url) == "https://github.com/Org/repo"

    @pytest.mark.parametrize("input_url,expected", [
        (
            "https://github.com/Org/repo%20name.git",
            "https://github.com/Org/repo%20name",
        ),
        # an encoded colon with no ``@`` anywhere is not userinfo
        (
            "https://github.com/Org/repo%3A2.git",
            "https://github.com/Org/repo%3A2",
        ),
        # an encoded ``@`` with no colon is an email-style segment, not userinfo
        (
            "https://github.com/Org/user%40example.com/repo.git",
            "https://github.com/Org/user%40example.com/repo",
        ),
        (
            "https://github.com/Org/re%2Bpo.git?ref=feature%2Fbranch",
            "https://github.com/Org/re%2Bpo?ref=feature%2Fbranch",
        ),
        # a stray ``%`` that is not an escape is a decode fixed point
        (
            "https://github.com/Org/100%25done/repo.git",
            "https://github.com/Org/100%25done/repo",
        ),
        (
            "https://github.com/Org/repo.git?ref=v1%3A2",
            "https://github.com/Org/repo?ref=v1%3A2",
        ),
    ])
    def test_benign_percent_encoded_urls_keep_their_bytes(self, input_url, expected):
        assert _normalize_git_url(input_url) == expected

    # Every position a credential can occupy, as a template over the encoded
    # ``:`` and ``@`` separators.
    @pytest.mark.parametrize("template", [
        "https:///user{c}SECRETVAL{a}Org/repo.git",
        "https://github.com/Org/user{c}SECRETVAL{a}b/repo.git",
        "https://user{c}SECRETVAL{a}evil.example/Org/repo.git",
        "//user{c}SECRETVAL{a}github.com/Org/repo.git",
        "github.com:user{c}SECRETVAL{a}Org/repo.git",
        "git::https:///user{c}SECRETVAL{a}Org/repo.git",
        "https://github.com/Org/repo?ref=u{c}SECRETVAL{a}h",
        "https://github.com/Org/repo#u{c}SECRETVAL{a}h",
    ])
    def test_encoded_userinfo_never_survives_at_any_depth(self, template):
        for depth in (*range(41), 100, 1000):
            url = template.format(
                c=_encoded_at_depth(":", depth), a=_encoded_at_depth("@", depth)
            )
            result = _normalize_git_url(url)
            assert "SECRETVAL" not in result, (depth, url[:80])
            assert "SECRETVAL" not in _fully_collapsed(result), (depth, url[:80])

    @pytest.mark.parametrize("template", [
        "https:///user{c}SECRETVAL{a}Org/repo.git",
        "https://github.com/Org/user{c}SECRETVAL{a}b/repo.git",
        "https://user{c}SECRETVAL{a}evil.example/Org/repo.git",
        "//user{c}SECRETVAL{a}github.com/Org/repo.git",
        "github.com:user{c}SECRETVAL{a}Org/repo.git",
        "git::https:///user{c}SECRETVAL{a}Org/repo.git",
        "https://github.com/Org/repo?ref=u{c}SECRETVAL{a}h",
        "https://github.com/Org/repo#u{c}SECRETVAL{a}h",
    ])
    def test_staggered_encoding_is_revealed_or_fails_closed(self, template):
        # Cross-replacement staggering defeats single-pass collapse by design;
        # beyond the pass cap the URL must fail closed, never pass through.
        for depth in range(11):
            url = template.format(
                c=_staggered(":", depth), a=_staggered("@", depth)
            )
            result = _normalize_git_url(url)
            assert "SECRETVAL" not in result, (depth, template)
            assert "SECRETVAL" not in _fully_collapsed(result), (depth, template)

    def test_unresolvable_encoding_fails_closed_to_placeholder(self):
        url = "https:///a" + _staggered("@", 10) + "b/repo.git"
        assert _normalize_git_url(url) == _REDACTED_GIT_URL

    @pytest.mark.parametrize("payload", [
        "%3Faccess_token%3DSECRETVAL",
        "%3Fx-api-key%3DSECRETVAL",
        "%3FX-Amz-Signature%3DSECRETVAL",
        "%23access_token%3DSECRETVAL",
        "%253Faccess_token%253DSECRETVAL",
        "%25253fACCESS%255FTOKEN%25253dSECRETVAL",
        "%3fAccess-Token%3dSECRETVAL",
    ])
    @pytest.mark.parametrize("template", [
        "https://github.com/Org/repo{p}",
        "ssh://git@github.com/Org/repo{p}.git",
        "github.com:Org/repo{p}.git",
        "//github.com/Org/repo{p}",
        "git::https://github.com/Org/repo{p}",
        "file:///srv/repo{p}",
        "/srv/repo{p}",
        r"C:\repos\repo{p}",
        "https://github.com{p}/Org/repo",
    ])
    def test_encoded_delimiter_credentials_never_survive(self, template, payload):
        result = _normalize_git_url(template.format(p=payload))
        assert "SECRETVAL" not in result, (template, payload)
        assert "SECRETVAL" not in _fully_collapsed(result), (template, payload)

    def test_encoded_delimiter_credentials_never_survive_at_any_depth(self):
        for depth in (*range(41), 100, 1000):
            url = (
                "https://github.com/Org/repo"
                + _encoded_at_depth("?access_token=", depth)
                + "SECRETVAL"
            )
            result = _normalize_git_url(url)
            assert "SECRETVAL" not in result, depth
            assert "SECRETVAL" not in _fully_collapsed(result), depth

    def test_staggered_delimiter_is_revealed_or_fails_closed(self):
        for depth in range(11):
            url = (
                "https://github.com/Org/repo"
                + _staggered("?", depth)
                + "access_token"
                + _staggered("=", depth)
                + "SECRETVAL"
            )
            result = _normalize_git_url(url)
            assert "SECRETVAL" not in result, depth
            assert "SECRETVAL" not in _fully_collapsed(result), depth

    @pytest.mark.parametrize("input_url,expected", [
        (
            "https://github.com/Org/repo%3Fref%3Dmain",
            "https://github.com/Org/repo%3Fref%3Dmain",
        ),
        (
            "https://github.com/Org/docs%23readme",
            "https://github.com/Org/docs%23readme",
        ),
        ("file:///srv/repo%3Fref%3Dmain", "file:///srv/repo%3Fref%3Dmain"),
        ("/data/file%3F.bin", "/data/file%3F.bin"),
        (r"C:\repos\repo%23notes", r"C:\repos\repo%23notes"),
        (
            "github.com:Org/repo%3Fref%3Dmain.git",
            "https://github.com/Org/repo%3Fref%3Dmain",
        ),
    ])
    def test_benign_encoded_delimiters_preserved(self, input_url, expected):
        assert _normalize_git_url(input_url) == expected

    @pytest.mark.parametrize("input_url,expected", [
        # the name of the parameter is itself percent-encoded; the query
        # parser's single decode leaves ``access%5Ftoken``, ``%74oken``
        (
            "https://github.com/Org/repo?access%255Ftoken=SECRETVAL",
            "https://github.com/Org/repo",
        ),
        (
            "https://github.com/Org/repo?%2574oken=SECRETVAL&ref=main",
            "https://github.com/Org/repo?ref=main",
        ),
        (
            "https://github.com/Org/repo?api%255Fkey=SECRETVAL",
            "https://github.com/Org/repo",
        ),
        (
            "https://github.com/Org/repo?pass%2577ord=SECRETVAL",
            "https://github.com/Org/repo",
        ),
        (
            "https://github.com/Org/repo?x%252Dapi%252Dkey=SECRETVAL",
            "https://github.com/Org/repo",
        ),
        (
            "https://github.com/Org/repo#access%255Ftoken=SECRETVAL",
            "https://github.com/Org/repo",
        ),
        (
            "https://github.com/Org/repo#%2573ig=SECRETVAL",
            "https://github.com/Org/repo",
        ),
        # must-keep names in encoded spelling stay, byte-for-byte
        (
            "https://github.com/Org/repo?max%255Ftokens=8&ref=main",
            "https://github.com/Org/repo?max%255Ftokens=8&ref=main",
        ),
        (
            "https://github.com/Org/repo?%2561uthor=me",
            "https://github.com/Org/repo?%2561uthor=me",
        ),
    ])
    def test_encoded_parameter_names_are_classified_decoded(
        self, input_url, expected
    ):
        assert _normalize_git_url(input_url) == expected

    @pytest.mark.parametrize("input_url,expected", [
        # userinfo lands in key position when the attacker omits ``=``
        (
            "https://github.com/Org/repo?u%3ASECRETVAL%40h=1",
            "https://github.com/Org/repo",
        ),
        (
            "https://github.com/Org/repo?u%25253ASECRETVAL%252540h=1&ref=main",
            "https://github.com/Org/repo?ref=main",
        ),
        # a field without ``=`` never reaches the pair screen
        (
            "https://github.com/Org/repo?u:SECRETVAL@h",
            "https://github.com/Org/repo",
        ),
        (
            "https://github.com/Org/repo?u%3ASECRETVAL%40h&ref=main",
            "https://github.com/Org/repo?ref=main",
        ),
        # benign bare fields stay byte-for-byte
        (
            "https://github.com/Org/repo?raw",
            "https://github.com/Org/repo?raw",
        ),
        (
            "https://github.com/Org/repo?a&ref=main",
            "https://github.com/Org/repo?a&ref=main",
        ),
    ])
    def test_credentials_outside_classifiable_pairs_are_dropped(
        self, input_url, expected
    ):
        assert _normalize_git_url(input_url) == expected

    # The signed-URL family is recognized only by whole-key match, not by
    # terminal word, so a nested name has to be judged by the same predicate as
    # a top-level key or it survives once folded into a benign value.
    @pytest.mark.parametrize("name", [
        "sig",
        "Signature",
        "X-Amz-Signature",
        "X-Goog-Signature",
        "X-Amz-Credential",
        "X-Amz-Security-Token",
        "AWSAccessKeyId",
        "GoogleAccessId",
    ])
    @pytest.mark.parametrize("outer", ["ref", "max_tokens"])
    @pytest.mark.parametrize("delimiter", ["?", "#"])
    def test_signed_url_name_nested_in_benign_value_is_dropped(
        self, name, outer, delimiter
    ):
        url = f"https://github.com/Org/repo{delimiter}{outer}=main?{name}=SECRETVAL"
        assert _normalize_git_url(url) == "https://github.com/Org/repo"

    @pytest.mark.parametrize("name", [
        "SignatureVersion",
        "X-Amz-Date",
        "X-Amz-Expires",
        "X-Amz-Algorithm",
        "X-Amz-SignedHeaders",
        "Expires",
        "Algorithm",
        "SignedHeaders",
        "function_signature",
        "max_tokens",
    ])
    @pytest.mark.parametrize("delimiter", ["?", "#"])
    def test_benign_name_nested_in_benign_value_is_kept(self, name, delimiter):
        url = f"https://github.com/Org/repo{delimiter}ref=main?{name}=v1"
        assert _normalize_git_url(url) == url

    @pytest.mark.parametrize("raw,expected", [
        # ``urlsplit`` silently drops these before parsing, so the sanitizer has
        # to as well or it rebuilds from text its own parser never saw.
        ("https://github.com/Org/repo\n.git", "https://github.com/Org/repo"),
        ("https://git\thub.com/Org/repo.git", "https://github.com/Org/repo"),
        ("https://github.com/Org/repo.git\r", "https://github.com/Org/repo"),
    ])
    def test_control_characters_are_removed_before_parsing(self, raw, expected):
        assert _normalize_git_url(raw) == expected

    @pytest.mark.parametrize("payload", [
        ":" * 100000,
        "a" * 100000,
        "a:" * 50000,
        "@" * 100000,
        "%40" * 30000,
        "a:b@" * 25000,
        "a:" * 50000 + "@",
        "git::" * 20000,
        "." * 100000,
        "a+" * 50000 + ":/x",
        "https://h/" + "a:" * 50000,
        "https://h/p?ref=" + "a:" * 50000,
        "https://h/p#ref=" + "a:" * 50000,
        "https://h/p?" + "a=1&" * 25000,
        "%3a" * 30000,
        "a%3a" * 25000,
        "%25" * 30000,
        "a%253a" * 15000,
        ("%" + "25" * 9 + "40") * 3000,
        "%" + "25" * 40000,
        "%3A%3a%2540%25253a" * 5000,
        _staggered("@", 10),
        "https:///u" + _staggered(":", 9) + "x" + _staggered("@", 9) + "h/r.git",
        "https://h/repo" + "%3Faccess%5Ftoken%3Da" * 5000,
        "https://h/repo" + "%23access_token%3Da" * 5000,
        "https://h/" + "%3Fref%3Dmain" * 7000,
        "%25253F" * 12000 + "access_token%3Da",
        "https://h/" + "a%3a" * 25000,
        "https://h/p?ref=" + "a%253a" * 15000,
        "https://h/p?" + "a%255Fb=1&" * 10000,
    ])
    def test_hostile_input_stays_within_linear_time(self, payload):
        """Guard the credential patterns against catastrophic backtracking.

        A remote URL is attacker-influenced text of unbounded length, so a
        quadratic pattern here would hang the CLI rather than merely slow it.
        """
        start = time.perf_counter()
        _normalize_git_url(payload)
        assert time.perf_counter() - start < 2.0

    # ------------------------------------------------------------------
    # Embedded-assignment scanning
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("value,expected", [
        # A name runs to the end of its ``[A-Za-z0-9._-]`` run and starts at the
        # run's first letter, so leading separators and digits are not names.
        ("access_token=x", ["access_token"]),
        ("_access_token=x", ["access_token"]),
        ("-access_token=x", ["access_token"]),
        ("9access_token=x", ["access_token"]),
        ("x-api-key-v2=x", ["x-api-key-v2"]),
        ("apiKeyId:x", ["apiKeyId"]),
        ("a.b-c_d=1", ["a.b-c_d"]),
        # Whitespace may separate the name from its delimiter.
        ("access_token = x", ["access_token"]),
        # Every assignment in the value is reported, in order.
        ("ref=main?access_token=x", ["ref", "access_token"]),
        ("a=1;b:2", ["a", "b"]),
        # A run with no delimiter after it, and a run with no letter in it,
        # yield nothing — this is what keeps long benign values cheap.
        ("main", []),
        ("refs/heads/topic", []),
        ("123=x", []),
        ("", []),
        ("=x", []),
        ("a" * 5000, []),
    ])
    def test_embedded_assignment_names(self, value, expected):
        assert list(_embedded_assignment_names(value)) == expected

    @pytest.mark.parametrize("shape", [
        # Each of these is a run the scanner must traverse exactly once.  A
        # pattern-based scan restarts inside the run and goes quadratic; the
        # underscore-separated shape defeats even a lookbehind-anchored pattern.
        "a",
        "_a",
        "a:",
        "k=v;",
        "a ",
        "a@",
        "._-",
    ])
    def test_assignment_scan_scales_linearly(self, shape):
        """Assert sub-quadratic scaling on a 4x size step.

        Linear work quadruples, quadratic work grows sixteenfold, so a bound of
        8x separates the two classes with margin on a noisy shared runner.
        """
        def elapsed(repeats: int) -> float:
            """Best of three; the floor is the signal and spikes are noise."""
            value = shape * repeats
            timings = []
            for _ in range(3):
                start = time.perf_counter()
                list(_embedded_assignment_names(value))
                timings.append(time.perf_counter() - start)
            return min(timings)

        small = elapsed(16_384)
        large = elapsed(65_536)
        assert large < 2.0
        # Guard against a divide-by-zero when the small case is below the clock
        # resolution; the absolute bound above already covers that case.
        if small > 1e-4:
            assert large / small < 8.0


class TestPercentScreening:
    def test_reveals_match_in_decoded_copy(self):
        assert _screen_reveals("a%253Ab", lambda form: ":" in form)

    def test_reveals_match_in_raw_text(self):
        assert _screen_reveals("a:b", lambda form: ":" in form)

    def test_text_without_escapes_is_judged_as_is(self):
        assert not _screen_reveals("Org/repo", lambda form: ":" in form)

    def test_stray_percent_is_a_safe_fixed_point(self):
        assert not _screen_reveals("100%", lambda form: "@" in form)

    @pytest.mark.parametrize("depth", [1, 2, 5, 40, 100, 1000])
    def test_uniform_nesting_collapses_in_one_pass(self, depth):
        nested_at = "%" + "25" * (depth - 1) + "40"
        assert _collapse_percent_octets(nested_at) == "@"

    @pytest.mark.parametrize("depth", [1, 2, 5, 40, 100, 1000])
    def test_mixed_case_hex_collapses(self, depth):
        assert _collapse_percent_octets("%" + "25" * (depth - 1) + "3a") == ":"
        assert _collapse_percent_octets("%" + "25" * (depth - 1) + "3A") == ":"

    def test_escapes_assembled_across_replacements_are_reached(self):
        # ``%%340``: the first pass decodes ``%34`` into ``4``, only then does
        # ``%40`` exist to decode — the shape the extra passes cover.
        assert _screen_reveals("%%340", lambda form: "@" in form)

    @pytest.mark.parametrize("depth", [1, 2, 3, 4])
    def test_staggered_towers_within_cap_are_revealed(self, depth):
        assert _screen_reveals(_staggered("@", depth), lambda form: "@" in form)

    @pytest.mark.parametrize("depth", [5, 6, 8, 10])
    def test_cap_exhaustion_fails_closed_with_bounded_work(self, depth):
        calls = 0

        def never(form):
            nonlocal calls
            calls += 1
            return False

        assert _screen_reveals(_staggered("@", depth), never) is True
        assert calls <= 5

    def test_text_converging_exactly_at_cap_is_examined_normally(self):
        assert _screen_reveals(_staggered("@", 4), lambda form: "@" in form)
        assert not _screen_reveals(_staggered("a", 4), lambda form: "@" in form)

    @pytest.mark.parametrize("form", [
        "repo?access_token=SECRETVAL",
        "repo?ref=main&x-api-key=SECRETVAL",
        "repo?X-Amz-Signature=SECRETVAL",
        "repo#access_token=SECRETVAL",
        "repo?ref=main#access_token=SECRETVAL",
        "repo?u:SECRETVAL@h",
    ])
    def test_decoded_delimiter_with_credentials_is_unsafe(self, form):
        assert _decoded_delimiter_unsafe(form)

    @pytest.mark.parametrize("form", [
        "Org/repo",
        "repo?ref=main",
        "repo?ref=main&path=a/b",
        "repo#readme",
        "repo?",
        "repo#",
        "repo?ref=main#L42",
    ])
    def test_decoded_delimiter_without_credentials_is_safe(self, form):
        assert not _decoded_delimiter_unsafe(form)

    def test_collapse_scales_linearly(self):
        def elapsed(repeats: int) -> float:
            text = ("%" + "25" * 5 + "3a") * repeats
            timings = []
            for _ in range(3):
                start = time.perf_counter()
                _collapse_percent_octets(text)
                timings.append(time.perf_counter() - start)
            return min(timings)

        small = elapsed(1_260)   # ~16 KB
        large = elapsed(5_040)   # ~64 KB
        assert large < 2.0
        if small > 1e-4:
            assert large / small < 8.0
