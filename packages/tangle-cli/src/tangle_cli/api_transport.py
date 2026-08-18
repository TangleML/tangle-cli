"""HTTP transport helpers shared by the OpenAPI CLI and programmatic client."""

from __future__ import annotations

import json
import os
import re
import string
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_TIMEOUT_SECONDS = 30.0
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_MISSING = object()
_SENSITIVE_HEADER_NAMES = {"authorization", "cloud-auth", "cookie", "x-api-key"}
# A field name is judged by its *tokens*, not by substring containment, so
# ``max_tokens``, ``token_count``, ``function_signature``, and ``secretary_email``
# keep their values while ``access_token`` and ``my_api_key`` lose theirs.
_FIELD_NAME_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")
# camelCase/PascalCase word boundaries, so ``accessToken`` tokenizes the same way
# ``access_token`` does. The second alternative splits an acronym from the word
# that follows it (``APIKey`` -> ``API`` ``Key``) without splitting the acronym
# itself, which is what keeps ``XApiKey`` and ``oauth2Token`` recognizable.
_FIELD_NAME_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
# Tokens that name a credential on their own, as the whole field name or as its
# final token. Deliberately singular: ``tokens`` counts usage, ``token`` is one.
_CREDENTIAL_WORDS = frozenset(
    {
        "auth",
        "authentication",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "passphrase",
        "passwd",
        "password",
        "pwd",
        "secret",
        "token",
        # Credential names that arrive as one unbroken lowercase run and so offer
        # no boundary to tokenize on. Listed in full rather than matched as
        # substrings, which is what keeps ``accesskeyidformat``, ``privatekeypath``,
        # ``maxtokens``, ``sessionid``, ``oauthlib``, and ``tokenizer`` readable.
        "accessid",
        "accesskey",
        "accesskeyid",
        "accesstoken",
        "apikey",
        "apisecret",
        "apitoken",
        "authtoken",
        "awsaccesskeyid",
        "bearertoken",
        "clientsecret",
        "googleaccessid",
        "idtoken",
        "oauth",
        "privatekey",
        "refreshtoken",
        "secretkey",
        "sessionkey",
        "sessiontoken",
    }
)
# Trailing words that name an identifier. They do not make a field safe: an
# access-key ID is one half of a credential pair and is issued and revoked with
# it, so the suffix is stripped and what it was attached to is judged again.
# ``access_key_id_format`` is unaffected -- its trailing word is ``format``.
_IDENTIFIER_SUFFIX_WORDS = frozenset({"id", "ident", "identifier"})
# Words that name key material only once an identifier suffix is stripped.
# ``access`` alone is ordinary; ``access_id`` and ``GoogleAccessId`` are not.
_CREDENTIAL_ID_WORDS = frozenset({"access"})
# Credential names that only read as one across two tokens. ``key`` and ``url``
# are far too common alone, so they are sensitive only in these pairings.
_CREDENTIAL_PHRASES = frozenset(
    {
        ("access", "key"),
        ("api", "key"),
        ("presigned", "url"),
        ("private", "key"),
        ("secret", "key"),
        ("session", "key"),
        ("signed", "url"),
    }
)
# Credential fields whose value is a URL. Blanket redaction throws away the host
# and path -- the part that separates a wrong bucket from an expired grant -- so
# inside a parsed body these go through the URL scrubber instead, which strips
# the signature and keeps the rest. A value with no URL in it has no such
# structure to lean on and is still redacted whole, as are header and query
# occurrences, whose values are never scrubbed.
_URL_VALUED_CREDENTIAL_PHRASES = frozenset({("presigned", "url"), ("signed", "url")})
# Query parameters that carry the credential portion of a presigned/SAS URL
# (AWS SigV4, GCS, Azure). Redacting the signature neutralizes the grant, so the
# rest of the SigV4 parameter set (``X-Amz-Algorithm``, ``X-Amz-Date``,
# ``X-Amz-Expires``, ``X-Amz-SignedHeaders``) stays readable and remains useful
# for diagnosing an expired or malformed link. These names are matched in full,
# so ``function_signature`` is untouched.
_SIGNED_URL_QUERY_RE = re.compile(
    r"^(x-(amz|goog|ms)-(signature|credential|security-token)"
    r"|sig|signature|awsaccesskeyid|googleaccessid)$",
    re.IGNORECASE,
)
# HTTP authentication schemes that prefix the credential rather than being one.
# The scheme name is diagnostic and kept; only the credential after it is cut.
_AUTH_SCHEME = (
    r"Bearer|Basic|Digest|Token|JWT|OAuth|ApiKey|SSWS|Negotiate|NTLM"
    r"|GoogleLogin|AWS4-HMAC-SHA256"
)
# The ``=``/``:`` separator of an assignment, on its own. Scanning for
# separators rather than for a fixed list of key names is what makes the field
# name open-ended: the name is recovered by a bounded lookbehind and judged by
# :func:`_is_sensitive_query_key`, so affixed names (``access_token``,
# ``my_api_key``) are covered without an alternation having to enumerate them.
# Free text is judged by the query predicate rather than the body one because a
# reflected ``X-Amz-Signature=...`` reads as a credential wherever it appears.
# The pattern deliberately stops at the separator so a declined match consumes
# nothing of the value -- a nested assignment under a harmless outer key
# (``{"detail": "token=..."``) is still reached. Optional quotes are absorbed so
# truncated JSON is scrubbed too, and ``(?!//)`` keeps ``https://host`` from
# reading as an assignment. Everything after the separator character is only
# scanned once a separator matched, so scanning is linear in the text length.
_ASSIGNMENT_SEPARATOR_RE = re.compile(r"[:=][ \t]*[\"']?(?!//)")
# Characters that may sit between a field name and its separator.
_FIELD_NAME_PADDING = " \t\"'"
# Characters a field name (or its padding) can end with, for an O(1) pre-check
# that skips the lookbehind for separators no name could precede.
_FIELD_NAME_TAIL_CHARS = _FIELD_NAME_PADDING + "_.-"
# The value of an assignment, anchored immediately after its separator. The
# value stops at whitespace, a form/list delimiter, a quote, or ``?`` so
# form-encoded, HTML-embedded, and query-string values stay bounded.
_ASSIGNMENT_VALUE_RE = re.compile(
    rf"(?:(?P<scheme>{_AUTH_SCHEME})[ \t]+)?(?P<value>[^\s&;,<>\"'?]+)",
    re.IGNORECASE,
)
# Trailing field name immediately preceding an assignment separator.
_FIELD_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]*$")
# Longest field name considered; bounds the per-separator lookbehind.
_MAX_FIELD_NAME_CHARS = 64
# An auth scheme carrying its credential without a preceding field name (e.g.
# ``rejected header: Bearer sk-1``, where the name is not itself sensitive).
# Matched case-sensitively against canonical scheme spellings so ordinary prose
# ("token count: 42") is not read as a credential.
_BARE_AUTH_SCHEME_RE = re.compile(
    r"\b(?P<scheme>Bearer|Basic|Digest|Negotiate|NTLM|SSWS|JWT|ApiKey|Token|OAuth"
    r"|GoogleLogin|AWS4-HMAC-SHA256)[ \t]+(?P<value>[A-Za-z0-9\-._~+/=]+)"
)
# Scheme names that are also ordinary English words, so a bare occurrence needs a
# credential long enough that "Token 12345 expired" cannot trip it.
_AMBIGUOUS_BARE_SCHEMES = {"Token", "OAuth"}
_MIN_AMBIGUOUS_SCHEME_CREDENTIAL_CHARS = 16
# The only values an unambiguous scheme may keep. Judging the value's *shape*
# ("does this look opaque?") would pass a short or word-like credential --
# ``Bearer abc``, ``Basic secret`` -- so the value is cut unconditionally unless
# it is one of these words, which describe auth mechanics ("Basic authentication
# failed", "Bearer token missing") and are never issued as credentials.
_SCHEME_PROSE_WORDS = frozenset(
    {
        "access",
        "and",
        "auth",
        "authentication",
        "authorization",
        "challenge",
        "credential",
        "credentials",
        "error",
        "failed",
        "failure",
        "header",
        "headers",
        "invalid",
        "is",
        "login",
        "missing",
        "negotiation",
        "not",
        "or",
        "prefix",
        "realm",
        "rejected",
        "required",
        "scheme",
        "schemes",
        "signature",
        "support",
        "supported",
        "token",
        "tokens",
        "type",
        "unsupported",
        "validation",
        "value",
        "values",
        "was",
    }
)
# Upper bound on backend-supplied error detail rendered on a single line.
_MAX_BACKEND_DETAIL_CHARS = 500
# Character classes for locating an embedded ``scheme://...`` run. The scan
# anchors on the ``://`` literal and walks outward, because a regex of the form
# ``[a-zA-Z][a-zA-Z0-9+.\-]*://`` re-scans every alphanumeric run once per
# starting offset and so costs O(n^2) on a body that is one long run.
_URL_SCHEME_CHARS = frozenset(string.ascii_letters + string.digits + "+.-")
_URL_SCHEME_START_CHARS = frozenset(string.ascii_letters)
_URL_SEPARATOR = "://"
_URL_STOP_CHARS = frozenset("'\"<>")
# Punctuation that ordinarily terminates a sentence rather than a URL.
_URL_TRAILING_PUNCTUATION = ").,;'\""
# ``user:pass@host`` userinfo carries the credential before the ``@``; the ``@``
# is the anchor, and the host side is never part of the secret.
_USERINFO_STOP_CHARS = frozenset("/@")
# Percent-encoding layers peeled off a displayed leaf while looking for a hidden
# credential assignment. Encoding ``access_token=x`` again hides the separator
# from a scanner that decodes once, so one layer is not enough; the cap is what
# keeps the walk linear, since each peel is O(len) and there is a fixed number of
# them. A leaf still changing under decode when the cap runs out is dropped.
_MAX_DECODE_LAYERS = 4
_REDACTED = "<redacted>"
_REDACTED_DOCUMENT = "<redacted document>"
_REDACTED_DEEP = "<redacted: nesting too deep>"
# Structure nesting kept when redacting a parsed JSON body. Error detail is
# collapsed onto one bounded line anyway, so nothing legible is lost past this
# point, and a hostile body cannot make an always-on error path recurse.
_MAX_JSON_DEPTH = 32
_OPAQUE_DOCUMENT_KEY_NAMES = {
    "component_yaml",
    "dockerfile",
    "manifest",
    "pipeline_yaml",
    "text",
    "yaml",
}


def tangle_verbose_enabled() -> bool:
    value = os.environ.get("TANGLE_VERBOSE")
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _field_name_tokens(name: str) -> list[str]:
    """Split *name* on non-alphanumerics and camelCase/PascalCase boundaries."""

    split = _FIELD_NAME_CAMEL_BOUNDARY_RE.sub(" ", name).lower()
    return [token for token in _FIELD_NAME_TOKEN_SPLIT_RE.split(split) if token]


def _names_credential(tokens: list[str]) -> bool:
    """Do *tokens* end in a word, or pair of words, that names a credential?"""

    if tokens[-1] in _CREDENTIAL_WORDS:
        return True
    return len(tokens) >= 2 and tuple(tokens[-2:]) in _CREDENTIAL_PHRASES


def _is_url_valued_credential_field(name: str) -> bool:
    """Is *name* a credential field whose value is expected to be a URL?"""

    tokens = _field_name_tokens(name)
    return len(tokens) >= 2 and tuple(tokens[-2:]) in _URL_VALUED_CREDENTIAL_PHRASES


def _is_sensitive_field_name(name: str) -> bool:
    """Is *name* a header or body field whose value must never be displayed?

    The name is split into tokens on any non-alphanumeric character and on
    camelCase/PascalCase word boundaries, then judged by its final token (or
    final two), so an affixed credential such as ``access_token``,
    ``accessToken``, ``client_secret``, or ``myApiKey`` is caught without the
    predicate having to enumerate prefixes or spellings. A trailing identifier
    word is stripped and the remainder judged again, which is what catches
    ``access_key_id``, ``AwsAccessKeyId``, and ``GoogleAccessId``.

    Judging tokens rather than substrings is what keeps ordinary fields
    readable: ``max_tokens``, ``tokenCount``, ``function_signature``,
    ``signatureVersion``, ``password_policy``, ``private_key_path``, and
    ``secretaryEmail`` all end in a token that names something other than a
    credential. ``access_key_id_format`` is readable for the same reason -- its
    identifier word is not trailing, so nothing is stripped.
    """

    tokens = _field_name_tokens(name)
    if not tokens:
        return False
    if _names_credential(tokens):
        return True
    while tokens[-1] in _IDENTIFIER_SUFFIX_WORDS:
        tokens = tokens[:-1]
        if not tokens:
            return False
        if tokens[-1] in _CREDENTIAL_ID_WORDS or _names_credential(tokens):
            return True
    return False


def _redact_headers(headers: dict[str, Any] | None) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for name, value in (headers or {}).items():
        normalized_name = name.lower()
        redacted[name] = (
            _REDACTED
            if normalized_name in _SENSITIVE_HEADER_NAMES or _is_sensitive_field_name(name)
            else value
        )
    return redacted


def _redact_sensitive_values(value: Any, key: str | None = None) -> Any:
    """Rebuild *value* with credential-bearing fields and string leaves redacted.

    Iterative rather than recursive, and bounded by :data:`_MAX_JSON_DEPTH`: this
    runs on every failed request, on a body an untrusted backend controls, and a
    few kilobytes of ``[[[[...]]]]`` is enough to exhaust the interpreter stack.
    Anything nested deeper than the bound is replaced wholesale, so the bound
    fails closed -- an unexamined subtree is never emitted.
    """

    holder: list[Any] = [None]
    # (container, slot, node, node_key, depth); the result is written into the
    # slot the parent reserved, which keeps dict insertion order intact.
    pending: list[tuple[Any, Any, Any, str | None, int]] = [(holder, 0, value, key, 0)]
    while pending:
        container, slot, node, node_key, depth = pending.pop()
        if (
            node_key
            and isinstance(node, str)
            and _URL_SEPARATOR in node
            and _is_url_valued_credential_field(node_key)
        ):
            container[slot] = _redact_text_secrets(node)
        elif node_key and _is_sensitive_field_name(node_key):
            container[slot] = _REDACTED
        elif (
            node_key
            and node_key.lower() in _OPAQUE_DOCUMENT_KEY_NAMES
            and isinstance(node, str)
            and node
        ):
            container[slot] = _REDACTED_DOCUMENT
        elif isinstance(node, dict):
            if depth >= _MAX_JSON_DEPTH:
                container[slot] = _REDACTED_DEEP
                continue
            branch: dict[str, Any] = {}
            container[slot] = branch
            for child_key in node:
                branch[str(child_key)] = None
            # Reversed so the stack pops in source order and a duplicated
            # ``str(key)`` resolves to the last occurrence, as a dict would.
            for child_key, child in reversed(list(node.items())):
                name = str(child_key)
                pending.append((branch, name, child, name, depth + 1))
        elif isinstance(node, list):
            if depth >= _MAX_JSON_DEPTH:
                container[slot] = _REDACTED_DEEP
                continue
            items: list[Any] = [None] * len(node)
            container[slot] = items
            for index, child in enumerate(node):
                pending.append((items, index, child, None, depth + 1))
        elif isinstance(node, str):
            # A non-sensitive field can still quote a sensitive assignment back at
            # us (``{"detail": "invalid token=... supplied"}``), so string leaves
            # get the same free-text scrub as an unparseable body.
            container[slot] = _redact_text_secrets(node)
        else:
            container[slot] = node
    return holder[0]


def _safe_json_text(value: Any) -> str:
    redacted = _redact_sensitive_values(value)
    try:
        return json.dumps(redacted, indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError, RecursionError):
        return str(redacted)


def _redact_assignments(text: str) -> str:
    """Redact credential assignments and auth-scheme credentials in *text*.

    Each ``=``/``:`` separator is found once, the field name preceding it is
    recovered by a bounded lookbehind, and :func:`_is_sensitive_query_key`
    decides -- so any spelling of a credential field is covered, including
    affixed names such as ``access_token`` or ``my_api_key`` that no fixed
    alternation would list. Only the value is replaced, so non-sensitive
    assignments (``page=2``) and the surrounding diagnostic prose survive. An
    auth scheme keeps its name (``Bearer <redacted>``) because the scheme is
    useful and the credential is not.

    Work is bounded per separator and per match, with no nested quantifier, so
    cost stays linear in ``len(text)``. Nothing here parses a URL or re-enters
    :func:`sanitize_url`, which is what lets the URL sanitizer reuse it as a leaf.
    """

    def _replace_bare_scheme(match: re.Match[str]) -> str:
        scheme, value = match.group("scheme"), match.group("value")
        if scheme in _AMBIGUOUS_BARE_SCHEMES:
            # "Token"/"OAuth" are ordinary words, so only a value long enough to
            # be a credential distinguishes prose from a leak.
            if len(value) < _MIN_AMBIGUOUS_SCHEME_CREDENTIAL_CHARS:
                return match.group(0)
        elif value.lower() in _SCHEME_PROSE_WORDS:
            return match.group(0)
        return f"{scheme} {_REDACTED}"

    chunks: list[str] = []
    cursor = 0
    for separator in _ASSIGNMENT_SEPARATOR_RE.finditer(text):
        start = separator.start()
        if start < cursor:
            # Inside a value that was already redacted.
            continue
        if start == 0 or not (
            text[start - 1].isalnum() or text[start - 1] in _FIELD_NAME_TAIL_CHARS
        ):
            # Nothing that could end a field name; skip the lookbehind entirely.
            continue
        preceding = text[max(0, start - _MAX_FIELD_NAME_CHARS) : start]
        name = _FIELD_NAME_RE.search(preceding.rstrip(_FIELD_NAME_PADDING))
        if name is None or not _is_sensitive_query_key(name.group(0)):
            continue
        value = _ASSIGNMENT_VALUE_RE.match(text, separator.end())
        if value is None:
            continue
        chunks.append(text[cursor : value.start("value")])
        chunks.append(_REDACTED)
        cursor = value.end("value")
    chunks.append(text[cursor:])
    return _BARE_AUTH_SCHEME_RE.sub(_replace_bare_scheme, "".join(chunks))


def _redact_text_secrets(text: str) -> str:
    """Redact credentials in free text.

    Handles every representation the structured JSON path cannot: form-encoded
    bodies, plain text, HTML error pages, truncated/unparseable JSON, and string
    leaves inside otherwise well-formed JSON. Credentials carried in a URL or in
    bare ``user:pass@host`` userinfo are stripped first by
    :func:`_scrub_secret_text`, the same primitive used on exception text; what
    survives as a plain assignment is then caught by :func:`_redact_assignments`.
    """

    return _redact_assignments(_scrub_secret_text(text))


def _content_to_text(content: bytes | str | None) -> str:
    if content is None:
        return "<empty>"
    if isinstance(content, bytes):
        if not content:
            return "<empty>"
        text = content.decode("utf-8", errors="replace")
    else:
        text = content
    if not text:
        return "<empty>"
    try:
        parsed = json.loads(text)
    except Exception:
        return _redact_text_secrets(text)
    return _safe_json_text(parsed)


def _is_sensitive_query_key(key: str) -> bool:
    """Is *key* a query parameter whose value must never be displayed?

    Stricter than the header/body predicate: a query string is also where a
    presigned-URL grant lives, and those parameter names (``sig``, ``signature``,
    ``X-Amz-*``) are credentials in a way the same word is not when it names a
    body field such as ``function_signature``.
    """

    stripped = key.strip()
    return _is_sensitive_field_name(stripped) or bool(
        _SIGNED_URL_QUERY_RE.fullmatch(stripped)
    )


def _redact_parameter_credentials(query: str) -> str:
    """Redact credential-named parameters in *query*, or drop it wholesale.

    The leaf of the sanitizer, and where the descent stops. It judges parameter
    names and strips userinfo, but it will not parse a value as a URL in turn —
    so a value that still looks like one is redacted whole rather than emitted
    unexamined. That is what makes the depth bound safe instead of merely finite:
    nesting a credential one level deeper than the sanitizer looks buries it
    rather than smuggling it out.
    """

    if not query:
        return ""
    try:
        pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
    except ValueError:
        return _REDACTED
    if not pairs or urllib.parse.urlencode(pairs) != query:
        return _REDACTED
    return urllib.parse.urlencode(
        [
            (_redact_bare_userinfo(key), _redact_leaf_value(key, value))
            for key, value in pairs
        ],
        safe="<>",
    )


def _redact_leaf_value(key: str, value: str) -> str:
    """Redact *value* if its name is sensitive or it hides a further URL."""

    if _is_sensitive_query_key(key) or _URL_SEPARATOR in value:
        return _REDACTED
    return _redact_assignments(_redact_bare_userinfo(value))


def _redact_nested_url_credentials(value: str) -> str:
    """Redact credential parameters inside a nested absolute URL.

    A return URL is routinely carried inside another URL
    (``?next=https%3A%2F%2Fhost%2Fcb%3Faccess_token%3D...``), and once
    :func:`urllib.parse.parse_qsl` has decoded it the inner credential is plainly
    visible. Its scheme, host, and path are kept so the destination stays
    diagnosable; only its own parameters are judged, by
    :func:`_redact_parameter_credentials`, which does not descend again. Going
    exactly one level deep is deliberate: :func:`sanitize_url` must not be
    re-entered here, or a URL nested inside a URL inside a URL would drive the
    recursion as deep as an untrusted body cared to nest it.
    """

    if _URL_SEPARATOR not in value:
        return value
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return _REDACTED
    if not parsed.scheme or not (parsed.query or parsed.fragment):
        return value
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            _redact_parameter_credentials(parsed.query),
            _redact_parameter_credentials(parsed.fragment),
        )
    )


def _hides_credential(value: str) -> bool:
    """Report whether bounded percent-decoding of *value* reveals a credential.

    ``state=access_token%3D...`` is legible after the single decode
    :func:`urllib.parse.parse_qsl` already performed. Encoding it again hides the
    separator from :func:`_redact_assignments`, and encoding it a third time hides
    it from any check that decodes only once more. Layers are therefore peeled up
    to ``_MAX_DECODE_LAYERS`` and every intermediate form is scanned.

    Every shape the caller redacts at the surface is looked for at each layer, not
    just assignments: ``alice%253Apw%2540host`` is userinfo one decode further
    down, and a scan for ``=`` alone walks straight past it.

    The walk is bounded rather than run to a fixpoint: an attacker supplies the
    value, so the number of layers must not be theirs to choose. When the cap runs
    out on a value that is still changing under decode, encoding remains that was
    never looked behind, and that is reported as hiding a credential -- burying one
    deeper than the sanitizer looks drops the value instead of publishing it.
    """

    for _ in range(_MAX_DECODE_LAYERS):
        decoded = urllib.parse.unquote(value)
        if decoded == value:
            return False
        if (
            _redact_assignments(decoded) != decoded
            or _redact_bare_userinfo(decoded) != decoded
        ):
            return True
        value = decoded
    return urllib.parse.unquote(value) != value


def _scrub_parameter_value(value: str) -> str:
    """Scrub a decoded parameter value in every shape a credential arrives in.

    An absolute URL is sanitized structurally, so its host and path survive. Any
    other shape -- a relative return path (``/cb?access_token=...``), an opaque
    ``mailto:``/``data:`` URI, or a bare ``access_token=...`` pair that a caller
    round-trips through ``state`` -- has no structure worth preserving, so it is
    scanned for credential assignments instead. The scan runs last either way:
    ahead of :func:`_redact_nested_url_credentials` it would rewrite the nested
    query out of its exact-round-trip form and cost that URL its host and path.
    """

    value = _redact_bare_userinfo(value)
    if _URL_SEPARATOR in value:
        value = _redact_nested_url_credentials(value)
    value = _redact_assignments(value)
    if _hides_credential(value):
        # Encoded past what the scans above can read (``state=access_token%253D...``,
        # ``next=alice%253Apw%2540host``). Emitting it would publish a value never
        # examined, so fail closed.
        return _REDACTED
    return value


def _sanitize_query_pairs(pairs: list[tuple[str, str]]) -> str:
    """Re-encode *pairs*, redacting credential names and scrubbing what remains.

    :func:`urllib.parse.parse_qsl` has already percent-decoded both halves of a
    pair, so a credential smuggled through a harmless-looking name is legible
    here: as userinfo (``next=https%3A%2F%2Fu%3Apw%40host``), as a parameter of a
    nested URL (``next=...%3Faccess_token%3D...``), as a bare assignment
    (``state=access_token%3D...``), or in the parameter name itself, either as
    userinfo (``alice%3Apw%40host=1``) or as a whole assignment encoded into the
    name (``access_token%3D...=1``). A name is displayed just as a value is, so
    both halves go through the same leaf scrub.

    Sensitivity is judged on the name as parsed, before that scrub, so rewriting
    a name cannot change the verdict on its value.

    Only non-recursive primitives are used. :func:`_scrub_secret_text` in
    particular is not, because it routes embedded URLs back through
    :func:`sanitize_url`, which arrives here again.
    """

    return urllib.parse.urlencode(
        [
            (
                _scrub_parameter_value(key),
                _REDACTED
                if _is_sensitive_query_key(key)
                else _scrub_parameter_value(value),
            )
            for key, value in pairs
        ],
        safe="<>",
    )


def _sanitize_fragment(fragment: str) -> str:
    """Return *fragment* with credentials removed, or drop it wholesale.

    A fragment is not merely an anchor: the OAuth implicit flow returns
    ``#access_token=...&token_type=Bearer`` there precisely so the credential
    stays out of the query, and it reaches this function on the always-on error
    display path.

    A fragment carrying neither ``=`` nor ``&`` is an anchor. Its userinfo is
    still stripped (``#u:pw@host`` is a valid anchor and a leak), and if it
    embeds a whole URL it is dropped instead, because sanitizing that properly
    would mean re-entering :func:`sanitize_url` from inside itself. Otherwise
    the fragment is parsed as a query string and re-encoded from the parsed
    pairs, so every value emitted is one that was examined. If the re-encoding
    does not reproduce the original exactly, some separator this does not model
    (``;``, a stray encoding) could be hiding a value that was never examined,
    so the fragment is dropped rather than guessed at.
    """

    if not fragment:
        return ""
    if "=" not in fragment and "&" not in fragment:
        if _URL_SEPARATOR in fragment:
            return _REDACTED
        anchor = _redact_assignments(_redact_bare_userinfo(fragment))
        if _hides_credential(anchor):
            return _REDACTED
        return anchor
    try:
        pairs = urllib.parse.parse_qsl(fragment, keep_blank_values=True)
    except ValueError:
        return _REDACTED
    if not pairs or urllib.parse.urlencode(pairs) != fragment:
        return _REDACTED
    return _sanitize_query_pairs(pairs)


def sanitize_url(url: Any) -> str:
    """Return *url* with credentials removed so it is safe to display or log.

    Strips any ``user:password@`` userinfo and redacts the values of query
    parameters that look like tokens, credentials, or presigned/SAS-URL
    signatures. A parameter kept under a non-sensitive name is scrubbed in turn,
    since a credential rides through one either as userinfo
    (``next=https%3A%2F%2Fu%3Apw%40host``) or as a parameter of the URL nested
    inside it (``next=...%3Faccess_token%3D...``); the nested URL is descended
    exactly one level. The scheme, host, port, path, and non-sensitive parameter
    names are preserved so the target stays recognizable. The fragment is
    sanitized on the same terms by :func:`_sanitize_fragment`, because the OAuth
    implicit flow delivers its access token there.
    """

    text = str(url)
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return _REDACTED
    if not parsed.scheme and not parsed.netloc:
        return text
    try:
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        # ``urlsplit`` defers authority validation until ``hostname``/``port`` is
        # read, so a bad port (``host:99999``, ``host:bad``) raises here rather
        # than above. Once the authority does not parse, the userinfo boundary
        # inside it cannot be trusted either, so the whole authority is dropped.
        # The scheme, path, and query are still sanitized and still diagnostic.
        netloc = _REDACTED
    else:
        # ``hostname`` unwraps IPv6 literals, so re-bracket them before appending
        # an optional port; otherwise ``[2001:db8::1]:8443`` becomes ambiguous
        # garbage.
        if ":" in host:
            host = f"[{host}]"
        netloc = f"{_REDACTED}@{host}" if (parsed.username or parsed.password) else host
        if port is not None:
            netloc = f"{netloc}:{port}"
    query = parsed.query
    if query:
        query = _sanitize_query_pairs(
            urllib.parse.parse_qsl(query, keep_blank_values=True)
        )
    fragment = _sanitize_fragment(parsed.fragment)
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, query, fragment))


def _bounded_detail(text: str | None) -> str:
    """Collapse whitespace and cap length of backend-supplied error detail."""

    if not text:
        return ""
    collapsed = " ".join(str(text).split())
    if len(collapsed) > _MAX_BACKEND_DETAIL_CHARS:
        collapsed = collapsed[:_MAX_BACKEND_DETAIL_CHARS].rstrip() + "…"
    return collapsed


def http_status_line(exc: httpx.HTTPStatusError) -> str:
    """Return the ``HTTP <status> <reason>`` summary for a status error."""

    response = exc.response
    return f"HTTP {response.status_code} {response.reason_phrase}".strip()


def format_http_status_error(exc: httpx.HTTPStatusError, *, include_detail: bool = True) -> str:
    """Build a concise one-line message for an httpx HTTP status error.

    Includes the status, request method, and a credential-safe URL. When
    *include_detail* is set, the response body is first run through the same
    secret redaction used for verbose logging -- key-by-key for JSON, and
    ``key=value``/``key: value`` assignment scrubbing for form, plain-text, and
    HTML bodies -- and only then whitespace normalized and length bounded, so a
    secret can never survive by sitting past the truncation point. Backend
    messages remain visible without leaking reflected credentials or dumping
    multi-line/oversized payloads.
    """

    request = exc.request
    method = request.method if request is not None else "?"
    url = sanitize_url(request.url) if request is not None else "?"
    message = f"{http_status_line(exc)} for {method} {url}"
    if include_detail:
        try:
            body = exc.response.text
        except Exception:  # pragma: no cover - defensive: streamed/undecodable body
            body = ""
        # Backends and proxies can reflect submitted fields (tokens, passwords)
        # into validation/authentication errors -- as JSON, form data, or an HTML
        # error page -- so redact before the body reaches stderr and CI logs.
        # Redaction precedes bounding so truncation cannot expose a secret.
        detail = _bounded_detail(_content_to_text(body)) if body else ""
        if detail:
            message = f"{message}: {detail}"
    return message


def _redact_embedded_urls(text: str) -> str:
    """Route every ``scheme://...`` run in *text* through :func:`sanitize_url`.

    Anchored on the ``://`` literal: the scheme is recovered by walking back over
    the scheme characters and the target by walking forward to the first
    delimiter. Consecutive runs cannot overlap, so every character is visited a
    bounded number of times whatever shape the input has.
    """

    chunks: list[str] = []
    cursor = 0
    separator = text.find(_URL_SEPARATOR)
    while separator != -1:
        start = separator
        while start > cursor and text[start - 1] in _URL_SCHEME_CHARS:
            start -= 1
        while start < separator and text[start] not in _URL_SCHEME_START_CHARS:
            start += 1
        if start < separator:
            end = separator + len(_URL_SEPARATOR)
            while end < len(text) and not (
                text[end].isspace() or text[end] in _URL_STOP_CHARS
            ):
                end += 1
            raw = text[start:end]
            trailing = ""
            while raw and raw[-1] in _URL_TRAILING_PUNCTUATION:
                trailing = raw[-1] + trailing
                raw = raw[:-1]
            chunks.append(text[cursor:start])
            chunks.append(sanitize_url(raw) + trailing)
            cursor = end
        separator = text.find(_URL_SEPARATOR, max(separator + 1, cursor))
    chunks.append(text[cursor:])
    return "".join(chunks)


def _redact_bare_userinfo(text: str) -> str:
    """Redact schemeless ``user:pass@host`` userinfo, keeping the host.

    Anchored on ``@`` and walking back only to the nearest delimiter, so the cost
    is linear even when the text is one unbroken run.
    """

    chunks: list[str] = []
    cursor = 0
    at = text.find("@")
    while at != -1:
        start = at
        while start > cursor and not (
            text[start - 1].isspace() or text[start - 1] in _USERINFO_STOP_CHARS
        ):
            start -= 1
        userinfo = text[start:at]
        colon = userinfo.find(":")
        # An empty username is legal userinfo (``:password@host``), so the colon
        # may sit at offset zero; it may not sit last, or there is no credential.
        if 0 <= colon < len(userinfo) - 1:
            chunks.append(text[cursor:start])
            chunks.append(_REDACTED)
            cursor = at
        at = text.find("@", at + 1)
    chunks.append(text[cursor:])
    return "".join(chunks)


def _scrub_secret_text(text: str) -> str:
    """Redact URLs and bare userinfo embedded in free-form text.

    A crafted or third-party ``httpx`` exception -- and equally a reflected
    response body -- can carry a proxy URL, a signed query, or ``user:pass@host``
    inside its message. Never emit that raw: route every ``scheme://`` run
    through :func:`sanitize_url` and strip any remaining schemeless userinfo,
    while leaving benign diagnostics (errno, TLS reason) intact.
    """

    return _redact_bare_userinfo(_redact_embedded_urls(text))


def describe_request_error(exc: httpx.RequestError) -> str:
    """Return an actionable, credential-safe reason for an httpx request error.

    Connection, timeout, proxy, and TLS failures are labeled so the user knows
    what to check; the underlying detail is included when it adds information.
    Any URL or userinfo embedded in the exception text is redacted first.
    """

    detail = _scrub_secret_text(" ".join(str(exc).split()))
    lowered = detail.lower()
    if isinstance(exc, httpx.ProxyError):
        return f"proxy error: {detail}" if detail else "proxy error"
    if isinstance(exc, httpx.TimeoutException):
        label = {
            httpx.ConnectTimeout: "connection timed out",
            httpx.ReadTimeout: "read timed out",
            httpx.WriteTimeout: "write timed out",
            httpx.PoolTimeout: "connection pool timed out",
        }.get(type(exc), "request timed out")
        return f"{label}: {detail}" if detail and label not in lowered else label
    if isinstance(exc, httpx.ConnectError):
        if any(token in lowered for token in ("ssl", "certificate", "tls", "handshake")):
            return f"TLS error: {detail}" if detail else "TLS error"
        return f"connection failed: {detail}" if detail else "connection failed"
    if detail:
        return detail
    return exc.__class__.__name__


def format_request_error(exc: httpx.RequestError) -> str:
    """Build a concise one-line message for an httpx connection-level error."""

    request = getattr(exc, "request", None)
    reason = describe_request_error(exc)
    if request is None:
        return f"Failed to reach the backend: {reason}"
    url = sanitize_url(request.url)
    return f"Failed to reach {request.method} {url}: {reason}"


def log_http_exchange(
    logger: Any,
    *,
    method: str,
    url: str,
    request_headers: dict[str, Any] | None = None,
    request_body: Any = None,
    response_status: int | None = None,
    response_headers: dict[str, Any] | None = None,
    response_body: bytes | str | None = None,
) -> None:
    """Log a redacted HTTP exchange for TANGLE_VERBOSE diagnostics."""

    emit = getattr(logger, "info", None)
    if not callable(emit):
        emit = lambda message: print(message, file=sys.stderr, flush=True)
    emit(f"[tangle-api] request: {method} {url}")
    emit(f"[tangle-api] request headers: {_safe_json_text(_redact_headers(request_headers))}")
    if isinstance(request_body, (bytes, str)) or request_body is None:
        request_body_text = _content_to_text(request_body)
    else:
        request_body_text = _safe_json_text(request_body)
    emit(f"[tangle-api] request body: {request_body_text}")
    if response_status is not None:
        emit(f"[tangle-api] response status: {response_status}")
    if response_headers is not None:
        emit(f"[tangle-api] response headers: {_safe_json_text(_redact_headers(response_headers))}")
    if response_body is not None:
        emit(f"[tangle-api] response body: {_content_to_text(response_body)}")


def default_base_url() -> str:
    configured_url = os.environ.get("TANGLE_API_URL")
    if configured_url:
        return _normalize_base_url(configured_url)
    if _ambient_auth_env_present():
        raise SystemExit(
            "TANGLE_API_URL is required when Tangle auth environment variables "
            f"are set; refusing to send credentials to default {DEFAULT_API_URL}"
        )
    return _normalize_base_url(DEFAULT_API_URL)


def _ambient_auth_env_present() -> bool:
    return any(
        os.environ.get(name)
        for name in (
            "TANGLE_API_AUTH_HEADER",
            "TANGLE_AUTH_HEADER",
            "TANGLE_API_HEADERS",
            "TANGLE_API_TOKEN",
        )
    )


def default_token() -> str | None:
    return os.environ.get("TANGLE_API_TOKEN") or None


def default_auth_header() -> str | None:
    return os.environ.get("TANGLE_API_AUTH_HEADER") or os.environ.get("TANGLE_AUTH_HEADER") or None


def _normalize_base_url(base_url: str) -> str:
    base_url = base_url.strip().rstrip("/")
    if base_url.endswith("/openapi.json"):
        base_url = base_url[: -len("/openapi.json")]
    return base_url.rstrip("/")


def _openapi_url(base_url: str) -> str:
    base_url = base_url.strip().rstrip("/")
    if base_url.endswith("/openapi.json"):
        return base_url
    return urllib.parse.urljoin(base_url + "/", "openapi.json")


def _request_headers(
    token: str | None,
    cli_header_entries: list[str] | str | None,
    cli_auth_header: str | None,
    extra_headers: dict[str, str] | None = None,
    *,
    include_env_credentials: bool = True,
) -> dict[str, str]:
    """Build request headers without printing or otherwise exposing secrets.

    Precedence, lowest to highest:
    default Accept header, ``TANGLE_API_HEADERS``, auth env vars,
    bearer token, explicit auth header, CLI/header entries, explicit mapping.
    """

    headers = {"Accept": "application/json"}
    if include_env_credentials:
        headers.update(_headers_from_env())
        env_auth_header = default_auth_header()
        if env_auth_header:
            headers["Authorization"] = _normalize_auth_header(
                env_auth_header, "TANGLE_API_AUTH_HEADER"
            )
        token = token or default_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cli_auth_header:
        headers["Authorization"] = _normalize_auth_header(cli_auth_header, "--auth-header")
    headers.update(_parse_header_entries(_header_entries(cli_header_entries), "--header"))
    if extra_headers:
        for name, value in extra_headers.items():
            _validate_header(name, str(value), "headers")
            headers[name] = str(value)
    return headers


def _normalize_auth_header(raw: str, source: str) -> str:
    """Accept either an Authorization value or ``Authorization: value``."""

    value = raw.strip()
    if value.lower().startswith("authorization:"):
        value = value.split(":", 1)[1].strip()
    if not value or "\n" in value or "\r" in value:
        raise SystemExit(f"Invalid {source}; expected an authorization header value")
    return value


def _headers_from_env() -> dict[str, str]:
    raw = os.environ.get("TANGLE_API_HEADERS")
    if not raw or not raw.strip():
        return {}
    return _parse_header_entries(_env_header_entries(raw), "TANGLE_API_HEADERS")


def _env_header_entries(raw: str) -> list[str]:
    """Parse env headers as JSON object/list or newline-separated entries."""

    raw = raw.strip()
    if raw[0] in "[{":
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit("Invalid TANGLE_API_HEADERS JSON") from exc
        if isinstance(parsed, dict):
            return [f"{name}: {value}" for name, value in parsed.items()]
        if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
            return parsed
        raise SystemExit("TANGLE_API_HEADERS must be a JSON object or string list")
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _header_entries(entries: list[str] | str | None) -> list[str]:
    if entries is None:
        return []
    if isinstance(entries, str):
        return [entries]
    return list(entries)


def _parse_header_entries(entries: list[str], source: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for entry in entries:
        if ":" in entry:
            name, value = entry.split(":", 1)
        elif "=" in entry:
            name, value = entry.split("=", 1)
        else:
            raise SystemExit(f"Invalid {source} entry; expected 'Name: value'")
        name = name.strip()
        value = value.strip()
        _validate_header(name, value, source)
        headers[name] = value
    return headers


def _validate_header(name: str, value: str, source: str) -> None:
    if not name or not _HEADER_NAME_RE.fullmatch(name) or "\n" in value or "\r" in value:
        raise SystemExit(f"Invalid {source} header name or value")


def request_operation(
    operation: Any,
    values: dict[str, Any],
    *,
    base_url: str | None = None,
    token: str | None = None,
    auth_header: str | None = None,
    header_entries: list[str] | str | None = None,
    headers: dict[str, str] | None = None,
    body: Any = _MISSING,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    allow_body_file_references: bool = False,
    include_env_credentials: bool = True,
) -> httpx.Response:
    """Dispatch one normalized OpenAPI operation as an HTTP request.

    ``values`` contains operation params using either generated Python names or
    original OpenAPI names. The returned response has already had
    ``raise_for_status()`` applied, matching the generated CLI behavior.
    """

    method, url, request_headers, content = build_operation_request(
        operation,
        values,
        base_url=base_url,
        token=token,
        auth_header=auth_header,
        header_entries=header_entries,
        headers=headers,
        body=body,
        allow_body_file_references=allow_body_file_references,
        include_env_credentials=include_env_credentials,
    )
    response = httpx.request(
        method,
        url,
        content=content,
        headers=request_headers,
        timeout=timeout,
    )
    if tangle_verbose_enabled():
        log_http_exchange(
            None,
            method=method,
            url=url,
            request_headers=request_headers,
            request_body=content,
            response_status=response.status_code,
            response_headers=dict(response.headers),
            response_body=response.text,
        )
    response.raise_for_status()
    return response


def build_operation_request(
    operation: Any,
    values: dict[str, Any],
    *,
    base_url: str | None = None,
    token: str | None = None,
    auth_header: str | None = None,
    header_entries: list[str] | str | None = None,
    headers: dict[str, str] | None = None,
    body: Any = _MISSING,
    allow_body_file_references: bool = False,
    include_env_credentials: bool = True,
) -> tuple[str, str, dict[str, str], bytes | None]:
    """Build method, URL, headers, and body bytes for an operation."""

    base_url = _normalize_base_url(base_url or default_base_url())
    path = operation.path
    query: dict[str, Any] = {}
    body_fields: dict[str, Any] = {}
    remaining = dict(values)

    for parameter in operation.parameters:
        if parameter.local_name in remaining:
            value = remaining.pop(parameter.local_name)
        elif parameter.original_name in remaining:
            value = remaining.pop(parameter.original_name)
        else:
            if parameter.location == "path" and parameter.required:
                raise TypeError(f"Missing required path parameter: {parameter.local_name}")
            if parameter.location in {"query", "body"} and parameter.required:
                # A required body field can also be satisfied by the generic body.
                if parameter.location == "body" and body is not _MISSING and body is not None:
                    continue
                raise TypeError(f"Missing required parameter: {parameter.local_name}")
            continue
        if value is None:
            continue
        if parameter.location == "path":
            path = path.replace(
                "{" + parameter.original_name + "}",
                urllib.parse.quote(str(value), safe=""),
            )
        elif parameter.location == "query":
            query[parameter.original_name] = value
        elif parameter.location == "body":
            body_fields[parameter.original_name] = value

    if remaining:
        names = ", ".join(sorted(remaining))
        raise TypeError(f"Unexpected parameter(s) for {operation.group_name}.{operation.command_name}: {names}")

    url = _join_operation_url(base_url, path)
    if query:
        url = f"{url}?{_urlencode_query(query)}"

    request_body = None
    if operation.has_request_body:
        if body is _MISSING:
            body = None
        request_body = (
            _coerce_body_argument(
                body, allow_file_references=allow_body_file_references
            )
            if body is not None
            else None
        )
    if body_fields:
        if request_body is None:
            request_body = {}
        if not isinstance(request_body, dict):
            raise TypeError("body must be a JSON object when body field parameters are used")
        request_body.update(body_fields)

    request_headers = _request_headers(
        token,
        header_entries,
        auth_header,
        headers,
        include_env_credentials=include_env_credentials,
    )
    content = _body_to_content(request_body)
    if content is not None and "Content-Type" not in request_headers:
        request_headers["Content-Type"] = "application/json"
    return operation.method, url, request_headers, content


def _join_operation_url(base_url: str, path: str) -> str:
    """Join a schema path to ``base_url`` without allowing origin changes."""

    parsed_path = urllib.parse.urlparse(path)
    if parsed_path.scheme or parsed_path.netloc:
        raise ValueError(f"OpenAPI operation path must be relative: {path!r}")
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _urlencode_query(query: dict[str, Any]) -> str:
    """Encode query params, preserving repeated values for list options."""

    items: list[tuple[str, Any]] = []
    for key, value in query.items():
        if isinstance(value, (list, tuple)):
            items.extend((key, item) for item in value)
        else:
            items.append((key, value))
    return urllib.parse.urlencode(items, doseq=True)


def _load_body_argument(body: str) -> Any:
    """Parse a CLI ``--body`` value; leading ``@`` reads JSON from a file."""

    if body.startswith("@"):
        body = Path(body[1:]).expanduser().read_text(encoding="utf-8")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON body: {exc}") from exc


def _coerce_body_argument(body: Any, *, allow_file_references: bool = False) -> Any:
    if not isinstance(body, str):
        return body
    if allow_file_references:
        return _load_body_argument(body)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def _body_to_content(request_body: Any) -> bytes | None:
    if request_body is None:
        return None
    if isinstance(request_body, bytes):
        return request_body
    if isinstance(request_body, bytearray):
        return bytes(request_body)
    return json.dumps(request_body).encode("utf-8")
