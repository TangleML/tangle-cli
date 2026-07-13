"""Artifact lookup and download helpers for Tangle pipeline runs.

This module resolves artifact metadata and can fetch artifact contents (direct
data route, inline metadata value, or signed URL) to a caller-provided
directory. It never mutates or deletes artifacts.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol
from urllib.parse import quote, urljoin, urlparse

import requests

from .handler import TangleCliHandler

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Artifact bytes are streamed to disk in fixed-size chunks so that large
# artifacts are never fully buffered in memory.
_DOWNLOAD_CHUNK_SIZE = 1024 * 1024

# ``O_NOFOLLOW`` is absent on some platforms (e.g. Windows); fall back to 0
# there. ``O_EXCL`` alone still refuses pre-existing symlinks on POSIX.
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

# Status codes from the direct ``/data`` route that mean "the bytes are not
# available here, try another path" rather than a hard failure: 404 (no direct
# data route for this artifact), 403 (direct download forbidden — bytes live
# behind a signed URL), and 410 (direct route gone). For all of these the
# inline metadata ``value`` or a signed URL is the correct fallback.
_DATA_FALLBACK_STATUS_CODES = frozenset({403, 404, 410})

# Redirect status codes. The direct ``/data`` route can 3xx-redirect to a
# cross-origin storage URL (e.g. GCS/S3). The authenticated client refuses to
# forward Tangle credentials off-origin and raises an ``HTTPError`` carrying the
# redirect response, so a redirect status observed on that pre-response error
# means "the bytes live off-origin, fall back to the signed URL" rather than a
# terminal failure.
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})

# Redirect-follow bound for absolute signed-URL downloads. Redirects there are
# followed manually (never automatically by ``requests``) so every hop can be
# rechecked against the http(s) allowlist before it is fetched.
_MAX_SIGNED_URL_REDIRECTS = 5

# Connect/read timeout for each unauthenticated signed-URL fetch (per hop).
_SIGNED_URL_TIMEOUT_SECONDS = 60.0

# Absolute signed URLs (and their redirect targets) pointing at internal hosts
# — loopback, link-local (incl. the cloud metadata IP), RFC1918/unique-local
# private ranges, and the unspecified address — are rejected by default so a
# hostile signed URL cannot aim the CLI at services reachable only from the
# local network (SSRF). Self-hosted/local deployments legitimately serve
# loopback signed URLs; this env var opts back in.
_ALLOW_INTERNAL_HOSTS_ENV = "TANGLE_ALLOW_INTERNAL_ARTIFACT_HOSTS"

# The socket layer (``inet_aton`` semantics via ``getaddrinfo``) accepts IPv4
# spellings that ``ipaddress.ip_address`` rejects: pure decimal (2130706433),
# octal (0177.0.0.1), hex (0x7f000001, 0x7f.0.0.1), and dotted short forms
# (127.1) — each of which can smuggle a loopback/metadata address past a
# canonical-literal check. Any host made only of such numeric labels that is
# *not* a canonical IP literal is rejected outright rather than re-implementing
# ``inet_aton`` value semantics: no legitimate signed URL spells its host that
# way, and an all-numeric label is not a valid DNS name.
_INET_ATON_NUMERIC_HOST_RE = re.compile(r"(?i)^(0x[0-9a-f]*|\d+)(\.(0x[0-9a-f]*|\d+)){0,3}$")

# NAT64 translation prefixes (RFC 6052 well-known 64:ff9b::/96 and RFC 8215
# local-use 64:ff9b:1::/48) embed an IPv4 address in the low 32 bits; a NAT64
# gateway would connect to that embedded IPv4, so it is what gets classified.
_NAT64_PREFIXES = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)

# Carrier-grade NAT shared address space (RFC 6598). ``is_private`` is False
# for it (it is neither private nor globally routable), so it needs its own
# membership check.
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

# Sentinel distinguishing an absent inline ``value`` field (no bytes here — fall
# back to the signed URL) from an explicit inline ``null`` (a real value written
# as JSON ``null``). ``None`` alone cannot tell these apart.
_MISSING = object()


class ArtifactClient(Protocol):
    """Subset of the static API client used for artifact lookup and download."""

    def get_run_details(self, run_id: str) -> Any: ...

    def get_execution_details(self, execution_id: str) -> Any: ...

    def pipeline_runs_get(self, run_id: str) -> Any: ...

    def executions_details(self, execution_id: str) -> Any: ...

    def artifacts_get(self, artifact_id: str) -> Any: ...

    def request_raw(self, method: str, path: str, **kwargs: Any) -> Any: ...

    def artifacts_signed_artifact_url(self, artifact_id: str) -> Any: ...


@dataclass
class ArtifactComponentQuery:
    """Filter for selecting artifacts by component name or digest."""

    name: str | None = None
    digest: str | None = None
    outputs: list[str] = field(default_factory=list)


@dataclass
class ArtifactInfo:
    """Resolved artifact metadata from GET /api/artifacts/{id}.

    This dataclass intentionally lives in this native-free module. Generated
    response objects are accepted structurally via ``from_response`` so no
    ``tangle_api`` import is required.
    """

    id: str
    uri: str
    key: str = ""
    total_size: int = 0
    is_dir: bool = False
    hash: str | None = None
    created_at: str | None = None
    error: str | None = None
    local_path: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], key: str = "") -> ArtifactInfo:
        ad = data.get("artifact_data", {})
        return cls(
            id=data.get("id", ""),
            uri=_mapping_or_attr(ad, "uri", ""),
            key=key,
            total_size=_mapping_or_attr(ad, "total_size", 0),
            is_dir=_mapping_or_attr(ad, "is_dir", False),
            hash=_optional_str(_mapping_or_attr(ad, "hash")),
            created_at=_optional_str(_mapping_or_attr(ad, "created_at")),
        )

    @classmethod
    def from_response(cls, response: Any, *, key: str = "") -> ArtifactInfo:
        """Create a flattened artifact DTO from a generated or duck-typed response."""

        artifact_data = getattr(response, "artifact_data", None)
        total_size = _mapping_or_attr(artifact_data, "total_size", 0)
        is_dir = _mapping_or_attr(artifact_data, "is_dir", False)
        return cls(
            id=str(getattr(response, "id", "") or ""),
            uri=str(_mapping_or_attr(artifact_data, "uri", "") or ""),
            key=key,
            total_size=total_size if isinstance(total_size, int) else 0,
            is_dir=is_dir if isinstance(is_dir, bool) else False,
            hash=_optional_str(_mapping_or_attr(artifact_data, "hash")),
            created_at=_optional_str(_mapping_or_attr(artifact_data, "created_at")),
        )


class ArtifactManager(TangleCliHandler):
    """Artifact metadata, listing, and download manager.

    Downstream packages can inject an already-authenticated client or a lazy
    ``client_factory`` (for example, one that applies provider auth). In addition
    to resolving metadata, the manager can download artifact contents (direct
    data route, inline metadata value, or signed URL) and write them to a
    caller-provided directory. It never mutates or deletes artifacts.
    """

    def __init__(
        self,
        client: ArtifactClient | None = None,
        *,
        client_factory: Any | None = None,
        logger: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(client=client, client_factory=client_factory, logger=logger, **kwargs)

    def collect_artifacts(
        self,
        execution: Any,
        tasks_query: dict[str, list[str] | None],
        components_query: list[ArtifactComponentQuery],
        prefix: str = "",
    ) -> dict[str, str]:
        """Collect artifact IDs by walking an enriched execution tree."""

        artifact_ids: dict[str, str] = {}
        task_spec = _mapping_or_attr(execution, "task_spec")
        graph_tasks = _mapping_or_attr(task_spec, "graph_tasks", {})
        if not isinstance(graph_tasks, dict):
            return artifact_ids

        for task_name, child_task in graph_tasks.items():
            task_name = str(task_name)
            key_prefix = f"{prefix}{task_name}" if prefix else task_name
            output_filters: list[list[str]] = []

            for query_name in (task_name, key_prefix):
                if query_name in tasks_query:
                    # A ``null`` filter means all outputs, same as ``[]`` — and
                    # the same as ``executions``/``components`` null filters.
                    output_filters.append(tasks_query[query_name] or [])
                    break

            child_digest = _mapping_or_attr(child_task, "digest")
            child_name = _mapping_or_attr(child_task, "name")
            for component in components_query:
                if (component.digest and child_digest == component.digest) or (
                    component.name and child_name == component.name
                ):
                    output_filters.append(component.outputs)

            out_artifacts = _artifact_id_map(_mapping_or_attr(child_task, "execution_output_artifacts", {}))
            if output_filters and out_artifacts:
                include_all = any(not output_filter for output_filter in output_filters)
                requested_outputs = {
                    output_name
                    for output_filter in output_filters
                    for output_name in output_filter
                }
                for output_name, artifact_id in out_artifacts.items():
                    if include_all or output_name in requested_outputs:
                        artifact_ids[f"{key_prefix}/{output_name}"] = artifact_id

            if _mapping_or_attr(child_task, "is_graph", False):
                child_executions = _mapping_or_attr(execution, "child_executions", {})
                child_execution = child_executions.get(task_name) if isinstance(child_executions, dict) else None
                if child_execution:
                    artifact_ids.update(
                        self.collect_artifacts(
                            child_execution,
                            tasks_query,
                            components_query,
                            prefix=f"{key_prefix}/",
                        )
                    )

        return artifact_ids

    def collect_execution_artifacts(
        self,
        execution_ids: dict[str, list[str] | None],
    ) -> dict[str, str]:
        """Collect artifact IDs directly from execution IDs."""

        artifact_ids: dict[str, str] = {}
        client = self._require_client()
        for execution_id, output_filter in execution_ids.items():
            execution = client.get_execution_details(execution_id)
            output_artifacts = _artifact_id_map(_mapping_or_attr(execution, "output_artifacts", {}))
            for output_name, artifact_id in output_artifacts.items():
                if not output_filter or output_name in output_filter:
                    artifact_ids[f"{execution_id}/{output_name}"] = artifact_id
        return artifact_ids

    def get_artifacts(
        self,
        run_id: str,
        query: dict[str, Any],
    ) -> dict[str, ArtifactInfo]:
        """Get artifact metadata for tasks/components in a pipeline run.

        Query keys:
          - ``tasks``: ``{<task_name>: [<output_names>]}``
          - ``components``: ``[{"name"|"digest": ..., "outputs": [...]}]``
          - ``executions``: ``{<execution_id>: [<output_names>]}``
          - ``artifact_ids``: ``[<artifact_id>, ...]``

        Empty (or ``null``) output lists mean all outputs. Per-artifact
        lookup failures are returned as ``ArtifactInfo(error=...)`` entries
        instead of failing the whole command.
        """

        # ``--query`` must be a JSON object; valid JSON that is not an object
        # (a list, string, or number) is rejected as a concise CLI error before
        # any client/network work, and nested key shapes are checked the same
        # way so a malformed inner value cannot raise from mid-walk.
        if not isinstance(query, dict):
            raise RuntimeError("--query must be a JSON object")
        _validate_query_shape(query)

        artifact_ids: dict[str, str] = {}

        for artifact_id in query.get("artifact_ids", []) or []:
            artifact_ids[str(artifact_id)] = str(artifact_id)

        executions_query = query.get("executions", {}) or {}
        if executions_query:
            artifact_ids.update(self.collect_execution_artifacts(executions_query))

        tasks_query = query.get("tasks", {}) or {}
        components_query_raw = query.get("components", []) or []
        if tasks_query or components_query_raw:
            details = self._require_client().get_run_details(run_id)
            execution = _mapping_or_attr(details, "execution")
            if not execution:
                raise RuntimeError("No execution details found for run")
            artifact_ids.update(
                self.collect_artifacts(
                    execution,
                    tasks_query,
                    _component_queries(components_query_raw),
                )
            )

        artifacts: dict[str, ArtifactInfo] = {}
        for key, artifact_id in artifact_ids.items():
            try:
                response = self._require_client().artifacts_get(artifact_id)
                artifacts[key] = _artifact_info_from_response(response, artifact_id=artifact_id, key=key)
            except Exception as exc:
                artifacts[key] = ArtifactInfo(id=artifact_id, uri="", key=key, error=str(exc))

        return artifacts

    def _resolve_root_execution(self, run_id: str) -> Any:
        """Resolve a pipeline run id to its (unenriched) root execution.

        Artifact listing/download operate on the run's *root execution*, but the
        CLI (like the ``--query`` path) accepts a *run* id; run ids and execution
        ids are distinct namespaces. Resolution stays shallow: ``get_run_details``
        would call ``get_execution_details``, which recursively enriches the whole
        descendant tree — one API call per descendant, so a single unreachable
        nested execution the command never displays would fail the command. This
        maps the run id to its ``root_execution_id`` (via ``pipeline_runs_get``,
        falling back to treating the id as an execution id on 404) and fetches
        just that one execution with the shallow ``executions_details``. Direct
        children are fetched only when ``include_children`` is set.
        """

        client = self._require_client()
        try:
            run = client.pipeline_runs_get(run_id)
            root_execution_id = _mapping_or_attr(run, "root_execution_id") or run_id
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
            root_execution_id = run_id
        execution = client.executions_details(root_execution_id)
        if not execution:
            raise RuntimeError(f"No execution details found for run {run_id}")
        return execution

    def list_result_artifacts(
        self,
        run_id: str,
        *,
        include_children: bool = False,
    ) -> list[dict[str, str]]:
        """List root output artifacts and optionally direct child outputs."""

        try:
            root = self._resolve_root_execution(run_id)
            root_artifacts = _artifact_id_map(_mapping_or_attr(root, "output_artifacts", {}))
            rows = [
                {"owner": "root", "output": output_name, "artifact_id": artifact_id}
                for output_name, artifact_id in root_artifacts.items()
            ]
            if not include_children:
                return rows

            child_executions = _direct_child_executions(root)
            if not child_executions:
                client = self._require_client()
                # Fetch only the *direct* children, shallowly: each child's own
                # ``output_artifacts`` is all that is listed, so the recursive
                # ``get_execution_details`` enrichment (one call per descendant)
                # is deliberately avoided here too.
                child_executions = {
                    task_name: client.executions_details(execution_id)
                    for task_name, execution_id in _child_execution_ids(root).items()
                }
        except (RuntimeError, requests.HTTPError):
            # Already clean / formatted by the CLI's HTTP-error surfacing.
            raise
        except Exception as exc:
            # Any other client/transport failure would escape the CLI's
            # RuntimeError handler as a raw traceback. Surface it concisely.
            raise RuntimeError(
                f"Failed to list artifacts for run {run_id}: "
                f"{_http_error_detail(_exc_status(exc), exc)}"
            ) from exc

        rows.extend(
            {"owner": task_name, "output": output_name, "artifact_id": artifact_id}
            for task_name, child in child_executions.items()
            for output_name, artifact_id in _artifact_id_map(
                _mapping_or_attr(child, "output_artifacts", {})
            ).items()
        )
        return rows

    def download_result_artifacts(
        self,
        run_id: str,
        *,
        out_dir: str | Path,
        only: Iterable[str] | None = None,
        include_children: bool = False,
    ) -> dict[str, Path]:
        """Download root output artifacts and optionally direct child outputs."""

        out_dir_path = Path(out_dir)
        try:
            out_dir_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # --out-dir pointing at an existing file (FileExistsError /
            # NotADirectoryError) or an otherwise uncreatable path would
            # otherwise raise a raw traceback past the CLI's RuntimeError handler.
            raise RuntimeError(
                f"Cannot create output directory {out_dir_path}: "
                f"{exc.strerror or exc}"
            ) from exc
        only_set = set(only) if only else None
        results: dict[str, Path] = {}
        seen: dict[str, Path] = {}
        # Distinct artifact ids can sanitize/truncate to the same filename
        # (``artifact_id[:12]``). Track each claimed filename so a collision
        # between *different* artifacts fails cleanly instead of silently
        # overwriting an already-downloaded file.
        claimed: dict[str, str] = {}

        for row in self.list_result_artifacts(
            run_id,
            include_children=include_children,
        ):
            output_name = row["output"]
            if only_set is not None and output_name not in only_set:
                continue
            artifact_id = row["artifact_id"]
            path = seen.get(artifact_id)
            if path is None:
                filename = _artifact_filename(row["owner"], output_name, artifact_id)
                prior = claimed.get(filename)
                if prior is not None and prior != artifact_id:
                    raise RuntimeError(
                        f"Refusing to download artifact {artifact_id}: target "
                        f"filename {filename!r} already used by artifact {prior}"
                    )
                path = self._download_artifact_to(artifact_id, out_dir_path / filename)
                claimed[filename] = artifact_id
                seen[artifact_id] = path
            results[f"{row['owner']}::{output_name}"] = path
        return results

    def _download_artifact_to(self, artifact_id: str, dest: Path) -> Path:
        """Stream artifact bytes to ``dest``, returning the path written.

        Tries the direct ``/data`` route first; on a fallback status the bytes
        come from an inline metadata value (JSON-encoded, written with a
        ``.json`` suffix appended to ``dest``) or a signed URL (raw bytes,
        written to ``dest`` as-is). The destination is opened exclusively and
        without following symlinks, so an existing file/dir/symlink at the
        target fails cleanly rather than being overwritten or followed.
        """

        client = self._require_client()
        request = getattr(client, "request_raw", None)
        if not callable(request):
            raise RuntimeError("Artifact downloads require a client with raw request support")
        endpoint = _data_endpoint(artifact_id)
        try:
            response = request("GET", endpoint, stream=True)
        except requests.RequestException as exc:
            status = _exc_status(exc)
            if status in _REDIRECT_STATUS_CODES:
                # The direct data route redirected to cross-origin storage; the
                # client refused to forward Tangle credentials off-origin and
                # raised before returning a body. Fall back to the signed-URL
                # path (fetched unauthenticated) instead of stopping at the 302.
                self._download_signed_artifact_to(artifact_id, dest, exc, status)
                return dest
            # Transport failure before a response object exists (DNS/TLS/
            # timeout/connection) on the direct data route. Without this the
            # raw requests exception would escape the CLI's RuntimeError handler.
            raise RuntimeError(
                f"Artifact download failed: GET {endpoint} transport failed: "
                f"{_http_error_detail(status, exc)}"
            ) from exc
        try:
            try:
                response.raise_for_status()
            except Exception as exc:
                status = _response_status_code(response)
                if status not in _DATA_FALLBACK_STATUS_CODES:
                    # Surface as a concise CLI error instead of a raw requests
                    # traceback (e.g. a 500 on the direct data route).
                    raise RuntimeError(
                        f"Artifact download failed: GET {endpoint} returned "
                        f"{_http_error_detail(status, exc)}"
                    ) from exc
                metadata_value = self._artifact_value_bytes(artifact_id)
                if metadata_value is not None:
                    json_dest = dest.with_name(dest.name + ".json")
                    _write_new_file_bytes(json_dest, metadata_value)
                    return json_dest
                self._download_signed_artifact_to(artifact_id, dest, exc, status)
                return dest
            try:
                _stream_response_to_file(response, dest)
            except requests.RequestException as exc:
                # A transport failure mid-stream (e.g. a dropped connection)
                # would otherwise escape as a raw requests traceback.
                raise RuntimeError(
                    f"Artifact download failed: GET {endpoint} streaming failed: "
                    f"{_http_error_detail(_exc_status(exc), exc)}"
                ) from exc
        finally:
            _close_quietly(response)
        return dest

    def _artifact_value_bytes(self, artifact_id: str) -> bytes | None:
        try:
            artifact = self._require_client().artifacts_get(artifact_id)
        except Exception:
            # Fetching the inline value is a best-effort step in the fallback
            # chain (direct /data -> inline value -> signed URL). Any failure
            # here means "no inline value from this step"; the caller then tries
            # the signed URL, which is the authoritative final attempt and
            # surfaces its own clean error if it also fails. Narrowing this catch
            # would turn a recoverable inline-fetch failure into a hard error and
            # skip the signed-URL fallback.
            return None
        artifact_data = _mapping_or_attr(artifact, "artifact_data")
        value = _mapping_or_attr(artifact_data, "value", _MISSING)
        if value is None and not isinstance(artifact_data, dict):
            # The shipped generated client deserializes ``artifact_data`` as a
            # plain dict (the response field is typed ``Any``), so ``dict.get``
            # above already tells absent from explicit null. A typed model
            # shaped like the generated ``ArtifactData`` defaults ``value`` to
            # ``None``, which ``getattr`` cannot distinguish from a returned
            # inline null — pydantic's set-field tracking can: a never-set
            # ``value`` falls through to the signed URL, while an explicitly
            # returned null stays a real inline value.
            fields_set = getattr(artifact_data, "model_fields_set", None)
            if fields_set is None:
                fields_set = getattr(artifact_data, "__fields_set__", None)
            if fields_set is not None and "value" not in fields_set:
                value = _MISSING
        # A genuinely absent ``value`` field means there are no inline bytes
        # here, so fall through to the signed-URL path. An explicit inline
        # ``null`` is a real value and is written as JSON ``null``.
        if value is _MISSING:
            return None
        # Every inline value (strings included) is JSON-encoded so the written
        # ``.json`` file is always valid JSON; ``str()`` would emit a Python
        # repr, and a raw string would not round-trip as JSON.
        return json.dumps(value).encode("utf-8")

    def _download_signed_artifact_to(
        self, artifact_id: str, dest: Path, data_error: Exception, data_status: int | None
    ) -> None:
        endpoint = _data_endpoint(artifact_id)
        direct = f"GET {endpoint} returned {_http_error_detail(data_status, data_error)}"
        try:
            signed_response = self._require_client().artifacts_signed_artifact_url(artifact_id)
        except Exception as exc:
            raise RuntimeError(
                f"Artifact {artifact_id} download failed: {direct}; "
                f"signed-URL request failed: {_http_error_detail(_exc_status(exc), exc)}"
            ) from exc
        signed_url = _mapping_or_attr(signed_response, "signed_url")
        if not signed_url:
            raise RuntimeError(
                f"Artifact {artifact_id} download failed: {direct}; no signed URL available"
            ) from data_error
        signed_url = str(signed_url)
        if not (_is_absolute_http_url(signed_url) or _is_relative_path_url(signed_url)):
            # A non-http(s) absolute URL (file://, gs://, s3://) or a
            # protocol-relative //host/... URL is neither fetchable
            # unauthenticated as http(s) nor a same-origin path; handing it to
            # the client's raw request raises a ValueError that would escape the
            # CLI as a traceback, and fetching it could forward credentials to
            # an unexpected host. Reject cleanly (without echoing the URL, which
            # can carry signed credentials).
            raise RuntimeError(
                f"Artifact {artifact_id} download failed: {direct}; unsupported "
                f"signed URL (expected an http(s) URL or a same-origin path)"
            ) from data_error
        try:
            response = self._download_signed_url(signed_url)
        except RuntimeError as exc:
            # Signed-URL policy failures raised below (internal host,
            # disallowed redirect target, redirect without Location, too many
            # redirects, missing raw-request support) get the same artifact/
            # direct context as the transport failures they sit alongside.
            raise RuntimeError(
                f"Artifact {artifact_id} download failed: {direct}; {exc}"
            ) from exc
        except requests.RequestException as exc:
            # DNS/TLS/timeout/connection failure before any response object
            # exists (e.g. absolute signed URL ``requests.get``). Without this
            # the raw requests exception would escape the CLI's RuntimeError
            # handler as a traceback.
            raise RuntimeError(
                f"Artifact {artifact_id} download failed: {direct}; "
                f"signed-URL transport failed: {_signed_url_error_detail(_exc_status(exc), exc)}"
            ) from exc
        try:
            try:
                response.raise_for_status()
            except Exception as exc:
                raise RuntimeError(
                    f"Artifact {artifact_id} download failed: {direct}; signed-URL download "
                    f"returned {_signed_url_error_detail(_response_status_code(response), exc)}"
                ) from exc
            try:
                _stream_response_to_file(response, dest)
            except requests.RequestException as exc:
                raise RuntimeError(
                    f"Artifact {artifact_id} download failed: {direct}; signed-URL "
                    f"download streaming failed: {_signed_url_error_detail(_exc_status(exc), exc)}"
                ) from exc
        finally:
            _close_quietly(response)

    def _download_signed_url(self, signed_url: str) -> requests.Response:
        # Absolute signed URLs point at the storage origin (e.g. GCS), so they
        # are fetched with an unauthenticated, streamed ``requests.get`` — Tangle
        # API auth headers/tokens are never sent off-origin. Relative signed
        # paths stay same-origin via the authenticated client (which itself
        # refuses cross-origin redirects).
        if _is_absolute_http_url(signed_url):
            return self._download_absolute_signed_url(signed_url)
        client = self._require_client()
        request = getattr(client, "request_raw", None)
        if not callable(request):
            raise RuntimeError("Relative signed artifact URLs require raw request support")
        return request("GET", signed_url, stream=True)

    @staticmethod
    def _download_absolute_signed_url(signed_url: str) -> requests.Response:
        """Fetch an absolute signed URL, rechecking each redirect hop.

        Redirects are followed manually (``allow_redirects=False``) so every
        hop is re-validated against the http(s) allowlist that admitted the
        initial signed URL. An automatic follow would hand a non-http(s)
        redirect target straight to ``requests``, whose ``InvalidSchema`` error
        echoes the target URL — and redirect targets derived from signed URLs
        can carry signed credentials. Rejection messages never echo the URL.

        The initial URL and every redirect target are also checked against the
        internal-host blocklist before being fetched (see
        ``_reject_internal_host``).
        """

        _reject_internal_host(signed_url, context="signed URL host")
        current_url = signed_url
        for _ in range(_MAX_SIGNED_URL_REDIRECTS + 1):
            response = requests.get(
                current_url, timeout=_SIGNED_URL_TIMEOUT_SECONDS, stream=True, allow_redirects=False
            )
            if response.status_code not in _REDIRECT_STATUS_CODES:
                return response
            location = response.headers.get("Location")
            _close_quietly(response)
            if not location:
                raise RuntimeError(
                    f"signed-URL download returned HTTP {response.status_code} "
                    "with no redirect Location"
                )
            current_url = urljoin(current_url, location)
            if not _is_absolute_http_url(current_url):
                raise RuntimeError(
                    "signed URL redirected to an unsupported target (expected an http(s) URL)"
                )
            _reject_internal_host(current_url, context="signed URL redirected to host")
        raise RuntimeError(
            f"signed URL exceeded {_MAX_SIGNED_URL_REDIRECTS} redirects"
        )

    @staticmethod
    def serialize_artifacts(artifacts: dict[str, ArtifactInfo]) -> list[dict[str, Any]]:
        """Serialize artifact dict to a JSON-friendly list, dropping ``None`` fields."""

        result: list[dict[str, Any]] = []
        for artifact in artifacts.values():
            data = asdict(artifact) if is_dataclass(artifact) else dict(artifact)
            result.append({key: value for key, value in data.items() if value is not None})
        return result


def _mapping_or_attr(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _artifact_id_map(raw_artifacts: Any) -> dict[str, str]:
    """Normalize API artifact maps to ``{output_name: artifact_id}``."""

    if not isinstance(raw_artifacts, dict):
        return {}

    artifact_ids: dict[str, str] = {}
    for output_name, value in raw_artifacts.items():
        if isinstance(value, str):
            artifact_ids[str(output_name)] = value
        elif isinstance(value, dict) and value.get("id"):
            artifact_ids[str(output_name)] = str(value["id"])
        elif getattr(value, "id", None):
            artifact_ids[str(output_name)] = str(value.id)
    return artifact_ids


def _query_shape_error(location: str, expected: str, value: Any) -> RuntimeError:
    return RuntimeError(f"--query {location} must be {expected}, got {type(value).__name__}")


def _validate_output_names(location: str, outputs: Any) -> None:
    if outputs is None:
        return
    if not isinstance(outputs, list) or not all(isinstance(name, str) for name in outputs):
        raise _query_shape_error(location, "a list of output-name strings", outputs)


def _validate_query_shape(query: dict[str, Any]) -> None:
    """Reject malformed nested ``--query`` values before any API call.

    Each supported key has a fixed shape (documented on ``get_artifacts``).
    Anything else would otherwise surface as an ``AttributeError`` or
    ``TypeError`` traceback from deep inside the artifact walk (for example
    ``.items()`` on a list) instead of a clean error. ``null`` top-level
    sections are treated as absent, and ``null`` per-entry output lists mean
    all outputs (same as ``[]``), matching the ``or``-defaults applied
    downstream.
    """

    for key in ("tasks", "executions"):
        section = query.get(key)
        if section is None:
            continue
        if not isinstance(section, dict):
            raise _query_shape_error(
                f"'{key}'", "an object mapping names to output-name lists", section
            )
        for name, outputs in section.items():
            _validate_output_names(f"'{key}' entry {name!r}", outputs)

    components = query.get("components")
    if components is not None:
        if not isinstance(components, list):
            raise _query_shape_error("'components'", "a list of objects", components)
        for index, component in enumerate(components):
            if not isinstance(component, dict):
                raise _query_shape_error(f"'components' entry {index}", "an object", component)
            for field_name in ("name", "digest"):
                field_value = component.get(field_name)
                if field_value is not None and not isinstance(field_value, str):
                    raise _query_shape_error(
                        f"'components' entry {index} key '{field_name}'", "a string", field_value
                    )
            _validate_output_names(
                f"'components' entry {index} key 'outputs'", component.get("outputs")
            )

    artifact_ids = query.get("artifact_ids")
    if artifact_ids is not None:
        if not isinstance(artifact_ids, list):
            raise _query_shape_error("'artifact_ids'", "a list of artifact-id strings", artifact_ids)
        for index, artifact_id in enumerate(artifact_ids):
            if not isinstance(artifact_id, str):
                raise _query_shape_error(f"'artifact_ids' entry {index}", "a string", artifact_id)


def _component_queries(raw_components: list[dict[str, Any]]) -> list[ArtifactComponentQuery]:
    return [
        ArtifactComponentQuery(
            name=component.get("name"),
            digest=component.get("digest"),
            outputs=component.get("outputs") or [],
        )
        for component in raw_components
    ]


def _artifact_info_from_response(response: Any, *, artifact_id: str, key: str) -> ArtifactInfo:
    if isinstance(response, dict):
        return ArtifactInfo.from_dict(response, key=key)
    return ArtifactInfo.from_response(response, key=key)


def _child_execution_ids(execution: Any) -> dict[str, str]:
    """Map direct child task names to their execution ids."""

    raw = _mapping_or_attr(execution, "child_task_execution_ids", {})
    if not isinstance(raw, dict):
        return {}
    return {str(name): str(execution_id) for name, execution_id in raw.items() if execution_id}


def _direct_child_executions(execution: Any) -> dict[str, Any]:
    """Return already-inlined direct child executions keyed by task name.

    Some responses embed the child executions directly (``child_executions``),
    which avoids a follow-up fetch per child. When absent, the caller resolves
    ids via ``_child_execution_ids`` instead.
    """

    raw = _mapping_or_attr(execution, "child_executions", {})
    if not isinstance(raw, dict):
        return {}
    return {str(name): child for name, child in raw.items() if child}


def _data_endpoint(artifact_id: str) -> str:
    # Percent-encode the id (``safe=''`` encodes ``/`` too) so an id containing
    # a slash or other path-significant characters cannot escape the intended
    # ``/api/artifacts/{id}/data`` path.
    return f"/api/artifacts/{quote(artifact_id, safe='')}/data"


def _safe_name(name: str) -> str:
    """Reduce an arbitrary label to filesystem-safe characters."""

    cleaned = _SAFE_NAME_RE.sub("_", str(name)).strip("._")
    return cleaned or "artifact"


def _artifact_filename(owner: str, output_name: str, artifact_id: str) -> str:
    # A short id suffix keeps names readable while disambiguating same-named
    # outputs across owners; the collision guard in download handles the rare
    # case where two distinct ids share a 12-char prefix. No extension is
    # forced: inline JSON values gain a ``.json`` suffix at write time.
    return f"{_safe_name(owner)}__{_safe_name(output_name)}__{_safe_name(artifact_id[:12])}"


def _open_new_artifact_file(dest: Path) -> BinaryIO:
    """Open ``dest`` for writing, refusing overwrite and never following symlinks.

    ``O_CREAT | O_EXCL`` fails atomically if the path already exists (a file,
    directory, or symlink), so downloads never clobber existing data or follow a
    symlink to write outside the output directory, with no check-then-write
    TOCTOU window.
    """

    try:
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"Refusing to overwrite existing path: {dest.name}") from exc
    except OSError as exc:
        raise RuntimeError(
            f"Cannot write artifact to {dest.name}: {exc.strerror or exc}"
        ) from exc
    return os.fdopen(fd, "wb")


def _write_new_file_bytes(dest: Path, data: bytes) -> None:
    handle = _open_new_artifact_file(dest)
    try:
        handle.write(data)
        handle.close()
    except OSError as exc:
        # Same clean-error contract as the streaming path: a local write failure
        # is a concise RuntimeError, and the partial file is removed.
        _discard_partial_download(handle, dest)
        raise RuntimeError(
            f"Cannot write artifact to {dest.name}: {exc.strerror or exc}"
        ) from exc
    except BaseException:
        _discard_partial_download(handle, dest)
        raise


def _stream_response_to_file(response: requests.Response, dest: Path) -> None:
    """Stream a response body to ``dest`` in chunks, cleaning up on failure."""

    handle = _open_new_artifact_file(dest)
    try:
        for chunk in response.iter_content(chunk_size=_DOWNLOAD_CHUNK_SIZE):
            if chunk:
                handle.write(chunk)
        # Close on the success path so a buffered-flush error surfaces as a
        # write failure handled below, not from a ``finally`` block.
        handle.close()
    except requests.RequestException:
        # Mid-stream transport failures get route context from the caller.
        # ``RequestException`` subclasses ``OSError``, so it must be re-raised
        # before the OSError handler below can claim it.
        _discard_partial_download(handle, dest)
        raise
    except OSError as exc:
        # A mid-stream local write failure (e.g. disk full) would otherwise
        # escape the CLI's RuntimeError handler as a raw traceback.
        _discard_partial_download(handle, dest)
        raise RuntimeError(
            f"Cannot write artifact to {dest.name}: {exc.strerror or exc}"
        ) from exc
    except BaseException:
        _discard_partial_download(handle, dest)
        raise


def _discard_partial_download(handle: BinaryIO, dest: Path) -> None:
    _close_quietly(handle)
    dest.unlink(missing_ok=True)


def _close_quietly(response: Any) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            # Closing a response is best-effort resource cleanup in ``finally``
            # blocks; a close failure must never replace the real error.
            pass


def _response_status_code(response: Any) -> int | None:
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _exc_status(exc: Exception) -> int | None:
    return _response_status_code(getattr(exc, "response", None))


def _http_error_detail(status: int | None, exc: Exception) -> str:
    """Concise, traceback-free description of a failed HTTP request."""

    if status is not None:
        return f"HTTP {status}"
    message = str(exc).strip()
    return message or exc.__class__.__name__


def _signed_url_error_detail(status: int | None, exc: Exception) -> str:
    """Describe a signed-URL failure without echoing the (credential-bearing) URL.

    ``requests`` embeds the full request URL in many exception messages; a
    signed URL carries credentials in its query string, so only the status and
    exception type are reported — never ``str(exc)``.
    """

    if status is not None:
        return f"HTTP {status}"
    return exc.__class__.__name__


def _is_absolute_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _is_relative_path_url(url: str) -> bool:
    """True for a same-origin path (no scheme, no host) fetched via the client.

    A protocol-relative ``//host/...`` URL has a netloc and a non-http(s)
    absolute URL (``file://``, ``gs://``, ``s3://``) has a scheme, so both are
    excluded here — they are neither fetchable unauthenticated as http(s) nor a
    safe same-origin path for the authenticated client.
    """

    parsed = urlparse(url)
    return not parsed.scheme and not parsed.netloc


def _internal_artifact_hosts_allowed() -> bool:
    value = os.environ.get(_ALLOW_INTERNAL_HOSTS_ENV, "")
    return value.strip().lower() in ("1", "true", "yes", "on")


def _embedded_ipv4(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | None:
    """Extract the IPv4 address embedded in an IPv4-mapped/NAT64 IPv6 address."""

    if not isinstance(ip, ipaddress.IPv6Address):
        return None
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if any(ip in prefix for prefix in _NAT64_PREFIXES):
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _host_rejection_reason(host: str | None) -> str | None:
    """Why ``host`` must not be fetched, or ``None`` for an external host.

    Checks the host literal only — no DNS resolution — so this is not a
    defense against DNS rebinding (a public hostname resolving to an internal
    address is not caught). Rejects ``localhost`` names, loopback (127.0.0.0/8,
    ::1), link-local (169.254.0.0/16 including the cloud metadata IP,
    fe80::/10), RFC1918 and IPv6 unique-local (fc00::/7) private ranges,
    carrier-grade NAT (100.64.0.0/10), the unspecified address (0.0.0.0, ::),
    and non-canonical ``inet_aton`` numeric spellings (see
    ``_INET_ATON_NUMERIC_HOST_RE``).
    Trailing dots are stripped before matching, and IPv4 addresses embedded in
    IPv4-mapped (``::ffff:127.0.0.1``) or NAT64 (``64:ff9b::a9fe:a9fe``) IPv6
    literals are unwrapped and classified by their IPv4 rules.
    """

    if not host:
        return None
    host = host.rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return "is an internal address"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if _INET_ATON_NUMERIC_HOST_RE.match(host):
            return "is a non-canonical numeric address"
        return None
    embedded = _embedded_ipv4(ip)
    if embedded is not None:
        ip = embedded
    if ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_unspecified:
        return "is an internal address"
    # CGNAT space (RFC 6598) is not covered by is_private but is only routable
    # inside a provider's network, so treat it as internal too.
    if ip.version == 4 and ip in _CGNAT_NETWORK:
        return "is an internal address"
    return None


def _reject_internal_host(url: str, *, context: str) -> None:
    """Raise unless ``url``'s host is external or the env override is set.

    ``urlparse().hostname`` lowercases the host and strips IPv6 brackets, so
    the checks in ``_host_rejection_reason`` see a normalized literal. The
    message names the host (not the full URL, which can carry signed
    credentials).
    """

    host = urlparse(url).hostname
    reason = _host_rejection_reason(host)
    if reason is None or _internal_artifact_hosts_allowed():
        return
    raise RuntimeError(
        f"{context} {host!r} {reason}; refusing to fetch "
        f"(set {_ALLOW_INTERNAL_HOSTS_ENV}=1 to allow internal hosts, "
        "e.g. for local/dev environments)"
    )


__all__ = [
    "ArtifactClient",
    "ArtifactComponentQuery",
    "ArtifactInfo",
    "ArtifactManager",
]
