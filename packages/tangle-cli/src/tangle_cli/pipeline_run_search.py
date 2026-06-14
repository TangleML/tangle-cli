"""Rich pipeline-run search/filter helpers.

This module is native-free and API-client agnostic.  It builds Tangle search
``filter_query`` payloads, resolves ``created_by=me`` via ``users_me()``, and
formats results for CLI/MCP consumers.  Downstreams such as tangle-deploy can
wrap these helpers with Shopify auth and legacy Typer entry points.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any

from .logger import Logger, get_default_logger

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_PIPELINE_NAME_WIDTH = 50
_IDX_WIDTH = 3
_CREATED_AT_WIDTH = 16


@dataclass
class PageChunk:
    """Metadata for a single page of search results.

    Defined locally to keep this module importable without the native
    ``tangle-api`` extra; ``tangle_cli.models`` re-exports an equivalent
    dataclass when native models are available.
    """

    rows: list[dict[str, Any]]
    page_token: str | None
    next_page_token: str | None
    ui_filter_url: str
    next_ui_filter_url: str | None


class PipelineRunSearch:
    """Resource manager for pipeline-run search/filter behavior.

    The class is intentionally native-free. Downstream packages can inject an
    authenticated client or lazy ``client_factory`` and subclass the formatting
    or predicate builders while the legacy module-level functions remain
    available.
    """

    def __init__(
        self,
        client: Any = None,
        *,
        client_factory: Callable[[], Any] | None = None,
        logger: Logger | None = None,
    ) -> None:
        self._client = client
        self._client_factory = client_factory
        self.logger = logger or get_default_logger()

    @property
    def client(self) -> Any:
        if self._client is None:
            if self._client_factory is None:
                raise ValueError("PipelineRunSearch requires a client or client_factory")
            self._client = self._client_factory()
        return self._client

    @staticmethod
    def build_predicate(*, predicate_type: str, **fields: Any) -> dict[str, Any]:
        return build_predicate(predicate_type=predicate_type, **fields)

    @staticmethod
    def build_value_contains(*, key: str, value_substring: str) -> dict[str, Any]:
        return build_value_contains(key=key, value_substring=value_substring)

    @staticmethod
    def build_value_equals(*, key: str, value: str) -> dict[str, Any]:
        return build_value_equals(key=key, value=value)

    @staticmethod
    def build_key_exists(*, key: str) -> dict[str, Any]:
        return build_key_exists(key=key)

    @staticmethod
    def build_time_range(
        *,
        key: str,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        return build_time_range(key=key, start_time=start_time, end_time=end_time)

    def validate_created_by(self, *, value: str) -> str:
        return validate_created_by(value=value, logger=self.logger)

    @staticmethod
    def parse_annotation(text: str) -> tuple[str, str | None]:
        return parse_annotation(text)

    @staticmethod
    def normalize_query_input(text: str) -> dict[str, Any]:
        return normalize_query_input(text)

    @staticmethod
    def build_ui_filter_url(
        *,
        base_url: str,
        name: str | None = None,
        created_by: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page_token: str | None = None,
    ) -> str:
        return build_ui_filter_url(
            base_url=base_url,
            name=name,
            created_by=created_by,
            start_date=start_date,
            end_date=end_date,
            page_token=page_token,
        )

    @staticmethod
    def build_filter_query(
        *,
        name: str | None = None,
        created_by: str | None = None,
        annotations: dict[str, str | None] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any] | None:
        return build_filter_query(
            name=name,
            created_by=created_by,
            annotations=annotations,
            start_date=start_date,
            end_date=end_date,
        )

    def resolve_created_by(self, *, created_by: str | None) -> tuple[str | None, dict[str, Any] | None]:
        return resolve_created_by(created_by=created_by, client=self.client, logger=self.logger)

    def resolve_dates(
        self,
        *,
        start_date: str | None,
        end_date: str | None,
        local_time: bool,
    ) -> tuple[str | None, str | None]:
        return resolve_dates(start_date=start_date, end_date=end_date, local_time=local_time, logger=self.logger)

    @staticmethod
    def format_mcp_table(*, rows: list[dict[str, Any]], next_page_token: str | None, ui_filter_url: str) -> str:
        return _format_mcp_table(rows=rows, next_page_token=next_page_token, ui_filter_url=ui_filter_url)

    @staticmethod
    def format_cli_table(*, page_chunks: list[PageChunk], total_count: int) -> str:
        return _format_cli_table(page_chunks=page_chunks, total_count=total_count)

    def fetch_pages(
        self,
        *,
        filter_query_str: str | None,
        limit: int,
        page_token: str | None,
        base_url: str,
        name: str | None,
        created_by: str | None,
        start_date: str | None,
        end_date: str | None,
    ) -> tuple[list[dict[str, Any]], list[PageChunk], str | None]:
        return fetch_pipeline_run_search_pages(
            client=self.client,
            filter_query_str=filter_query_str,
            limit=limit,
            page_token=page_token,
            base_url=base_url,
            name=name,
            created_by=created_by,
            start_date=start_date,
            end_date=end_date,
        )

    @staticmethod
    def build_result(
        *,
        all_rows: list[dict[str, Any]],
        page_chunks: list[PageChunk],
        final_next_token: str | None,
        first_ui_url: str,
    ) -> dict[str, Any]:
        return build_pipeline_run_search_result(
            all_rows=all_rows,
            page_chunks=page_chunks,
            final_next_token=final_next_token,
            first_ui_url=first_ui_url,
        )

    def search(
        self,
        *,
        name: str | None = None,
        created_by: str | None = None,
        annotations: dict[str, str | None] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        local_time: bool = False,
        query: dict[str, Any] | None = None,
        limit: int = 10,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """Search pipeline runs and return rows, page metadata, and tables."""

        limit = max(1, min(limit, 100))
        resolved_created_by, err = self.resolve_created_by(created_by=created_by)
        if err is not None:
            return err
        resolved_start, resolved_end = self.resolve_dates(
            start_date=start_date,
            end_date=end_date,
            local_time=local_time,
        )
        filter_query_dict = query or self.build_filter_query(
            name=name,
            created_by=resolved_created_by,
            annotations=annotations,
            start_date=resolved_start,
            end_date=resolved_end,
        )
        filter_query_str = json.dumps(filter_query_dict, separators=(",", ":")) if filter_query_dict else None
        self.logger.info(f"Searching pipeline runs (limit={limit})...")
        base_url = getattr(self.client, "base_url", "").rstrip("/")
        all_rows, page_chunks, final_next_token = self.fetch_pages(
            filter_query_str=filter_query_str,
            limit=limit,
            page_token=page_token,
            base_url=base_url,
            name=name,
            created_by=resolved_created_by,
            start_date=resolved_start,
            end_date=resolved_end,
        )
        if len(page_chunks) > 1:
            self.logger.info(f"Fetched {len(page_chunks)} pages to collect {len(all_rows)} results.")
        first_ui_url = (
            page_chunks[0].ui_filter_url
            if page_chunks
            else self.build_ui_filter_url(
                base_url=base_url,
                name=name,
                created_by=resolved_created_by,
                start_date=resolved_start,
                end_date=resolved_end,
                page_token=page_token,
            )
        )
        return self.build_result(
            all_rows=all_rows,
            page_chunks=page_chunks,
            final_next_token=final_next_token,
            first_ui_url=first_ui_url,
        )


def build_predicate(*, predicate_type: str, **fields: Any) -> dict[str, Any]:
    schemas: dict[str, tuple[str, ...]] = {
        "value_contains": ("key", "value_substring"),
        "value_equals": ("key", "value"),
        "key_exists": ("key",),
        "time_range": ("key", "start_time", "end_time"),
    }
    schema = schemas.get(predicate_type)
    if schema is None:
        raise ValueError(f"Unknown predicate type: {predicate_type!r}")
    return {predicate_type: {key: fields[key] for key in schema if key in fields}}


def build_value_contains(*, key: str, value_substring: str) -> dict[str, Any]:
    return build_predicate(predicate_type="value_contains", key=key, value_substring=value_substring)


def build_value_equals(*, key: str, value: str) -> dict[str, Any]:
    return build_predicate(predicate_type="value_equals", key=key, value=value)


def build_key_exists(*, key: str) -> dict[str, Any]:
    return build_predicate(predicate_type="key_exists", key=key)


def build_time_range(
    *,
    key: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {"key": key}
    if start_time is not None:
        fields["start_time"] = start_time
    if end_time is not None:
        fields["end_time"] = end_time
    return build_predicate(predicate_type="time_range", **fields)


def validate_created_by(*, value: str, logger: Logger) -> str:
    """Warn (but do not reject) if *value* is not ``me`` and not an email."""

    if value != "me" and not _EMAIL_RE.match(value):
        logger.warn(
            f"⚠️  created_by '{value}' does not look like a valid email"
            " — results may be empty or the API may return an error."
        )
    return value


def has_timezone(*, value: str) -> bool:
    """Return True if *value* already includes a timezone offset or ``Z``."""

    return value.endswith("Z") or "+" in value or value.count("-") >= 3


def apply_local_timezone(*, value: str, logger: Logger, suppress_log: bool = False) -> str:
    """Append the system's local UTC offset to a naive datetime string."""

    now = datetime.now(tz=timezone.utc).astimezone()
    tz_name = now.tzname() or "UTC"
    offset_str = now.strftime("%z")
    offset_formatted = f"{offset_str[:3]}:{offset_str[3:]}"

    try:
        import time as _time

        iana_name = _time.tzname[0] if _time.daylight == 0 else _time.tzname[1]
    except Exception:
        iana_name = tz_name

    if not suppress_log:
        logger.info("")
        logger.info(f"🕐 Timezone: {iana_name} (UTC{offset_formatted})")
        logger.info(f"   Dates will be interpreted as {iana_name} time.")
        logger.info("")
    return f"{value}{offset_formatted}"


def parse_annotation(text: str) -> tuple[str, str | None]:
    """Parse ``key=value`` or ``key`` annotation filters."""

    if "=" in text:
        key, value = text.split("=", 1)
        return key, value
    return text, None


def normalize_query_input(text: str) -> dict[str, Any]:
    """Parse raw ``--query`` input, auto-detecting URL-encoding."""

    try:
        loaded = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        loaded = None
    if isinstance(loaded, dict):
        return loaded

    try:
        decoded = urllib.parse.unquote(text)
        loaded = json.loads(decoded)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(
            "Invalid --query input: not valid JSON (plain or URL-encoded). "
            f"Parse error: {exc}"
        ) from exc
    if not isinstance(loaded, dict):
        raise ValueError("Invalid --query input: JSON value must be an object")
    return loaded


def build_ui_filter_url(
    *,
    base_url: str,
    name: str | None = None,
    created_by: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page_token: str | None = None,
) -> str:
    """Build a Tangle UI URL with a friendly ``?filter=`` query parameter."""

    filter_obj: dict[str, str] = {}
    if name:
        filter_obj["pipeline_name"] = name
    if created_by:
        filter_obj["created_by"] = created_by
    if start_date:
        filter_obj["created_after"] = start_date
    if end_date:
        filter_obj["created_before"] = end_date
    if not filter_obj:
        return base_url
    params: dict[str, str] = {"filter": json.dumps(filter_obj)}
    if page_token:
        params["page_token"] = page_token
    return f"{base_url}/?{urllib.parse.urlencode(params)}"


def build_filter_query(
    *,
    name: str | None = None,
    created_by: str | None = None,
    annotations: dict[str, str | None] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any] | None:
    """Translate friendly search params into a Tangle ``filter_query`` object."""

    predicates: list[dict[str, Any]] = []
    if name:
        predicates.append(build_value_contains(key="system/pipeline_run.name", value_substring=name))
    if created_by:
        predicates.append(build_value_equals(key="system/pipeline_run.created_by", value=created_by))
    if annotations:
        for key, value in annotations.items():
            if value is None:
                predicates.append(build_key_exists(key=key))
            elif value == "":
                predicates.append(build_value_equals(key=key, value=""))
            else:
                predicates.append(build_value_contains(key=key, value_substring=value))
    if start_date or end_date:
        predicates.append(
            build_time_range(
                key="system/pipeline_run.date.created_at",
                start_time=start_date,
                end_time=end_date,
            )
        )
    return {"and": predicates} if predicates else None


def resolve_created_by(
    *,
    created_by: str | None,
    client: Any,
    logger: Logger,
) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve ``created_by=me`` to the current user's id/email if requested."""

    if not created_by:
        return created_by, None
    resolved = created_by
    if created_by.lower() == "me":
        user_info = client.users_me()
        if user_info:
            resolved = str(getattr(user_info, "id", None) or user_info.get("id"))
            logger.info(f"Resolved 'me' to: {resolved}")
        else:
            return None, {"error": "Could not resolve 'me': authentication failed or user not found."}
    validate_created_by(value=resolved, logger=logger)
    return resolved, None


def resolve_dates(
    *,
    start_date: str | None,
    end_date: str | None,
    local_time: bool,
    logger: Logger,
) -> tuple[str | None, str | None]:
    """Apply local timezone to naive datetimes."""

    resolved_start = start_date
    resolved_end = end_date
    tz_logged = False
    for label, date_val, attr in (
        ("start-date", resolved_start, "start"),
        ("end-date", resolved_end, "end"),
    ):
        if not date_val:
            continue
        if not has_timezone(value=date_val):
            if not local_time:
                logger.warn(
                    f"⚠️  --{label} '{date_val}' has no timezone — assuming local time."
                    " Pass an explicit timezone (e.g. 'Z' or '+00:00')"
                    " or --local-time to silence this warning."
                )
            date_val = apply_local_timezone(value=date_val, logger=logger, suppress_log=tz_logged)
            tz_logged = True
        if attr == "start":
            resolved_start = date_val
        else:
            resolved_end = date_val
    return resolved_start, resolved_end


def _format_datetime(value: str) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return value[:16] if len(value) > 16 else value


def _format_mcp_table(*, rows: list[dict[str, Any]], next_page_token: str | None, ui_filter_url: str) -> str:
    if not rows:
        return "No pipeline runs found matching the search criteria."
    lines = [
        f"| {'#':>3} | Run ID | Pipeline Name | Created By | Created At (UTC) |",
        "|-----|--------|--------------|------------|------------|",
    ]
    for row in rows:
        run_id = row["run_id"]
        short_id = f"{run_id[:7]}...{run_id[-3:]}" if len(run_id) > 12 else run_id
        created_at = _format_datetime(row["created_at"])
        lines.append(
            f"| {row['index']:>3} | [{short_id}]({row['run_url']}) | "
            f"{row['pipeline_name']} | {row['created_by']} | {created_at} |"
        )
    lines.append("")
    lines.append(f"Showing {len(rows)} results.")
    if ui_filter_url:
        lines.append(f"[View filtered results in Tangle UI]({ui_filter_url})")
    if next_page_token:
        lines.append(f"Next page token: `{next_page_token}`")
    return "\n".join(lines)


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _compute_column_widths(all_rows: list[dict[str, Any]]) -> tuple[int, int, int]:
    name_w = min(
        max((len(r["pipeline_name"]) for r in all_rows), default=_MAX_PIPELINE_NAME_WIDTH),
        _MAX_PIPELINE_NAME_WIDTH,
    )
    name_w = max(name_w, len("Pipeline Name"))
    email_w = max((len(r["created_by"]) for r in all_rows), default=len("Created By"))
    email_w = max(email_w, len("Created By"))
    url_w = max((len(r["run_url"]) for r in all_rows), default=len("Tangle Link"))
    url_w = max(url_w, len("Tangle Link"))
    return name_w, email_w, url_w


def _cli_header_and_sep(*, name_w: int, email_w: int, url_w: int) -> tuple[str, str]:
    hdr = (
        f"| {'#':>{_IDX_WIDTH}} "
        f"| {'Pipeline Name':<{name_w}} "
        f"| {'Created By':<{email_w}} "
        f"| {'Created At (UTC)':<{_CREATED_AT_WIDTH}} "
        f"| {'Tangle Link':<{url_w}} |"
    )
    sep = (
        f"|{'─' * (_IDX_WIDTH + 2)}"
        f"|{'─' * (name_w + 2)}"
        f"|{'─' * (email_w + 2)}"
        f"|{'─' * (_CREATED_AT_WIDTH + 2)}"
        f"|{'─' * (url_w + 2)}|"
    )
    return hdr, sep


def _format_cli_table(*, page_chunks: list[PageChunk], total_count: int) -> str:
    if not page_chunks or total_count == 0:
        return "\n🔍 No pipeline runs found matching the search criteria.\n"
    all_rows = [row for chunk in page_chunks for row in chunk.rows]
    name_w, email_w, url_w = _compute_column_widths(all_rows)
    hdr, sep = _cli_header_and_sep(name_w=name_w, email_w=email_w, url_w=url_w)
    lines: list[str] = ["", "🔍 Pipeline Run Search Results", "─" * len(sep)]
    for chunk_idx, chunk in enumerate(page_chunks):
        page_num = chunk_idx + 1
        first_idx = chunk.rows[0]["index"]
        last_idx = chunk.rows[-1]["index"]
        lines.append("")
        if len(page_chunks) > 1:
            lines.append(f"📄 Page {page_num}  (rows {first_idx}–{last_idx})")
            lines.append("")
        lines.append(hdr)
        lines.append(sep)
        for row in chunk.rows:
            name_val = _truncate(row["pipeline_name"], name_w)
            created_at = _format_datetime(row["created_at"])
            lines.append(
                f"| {row['index']:>{_IDX_WIDTH}} "
                f"| {name_val:<{name_w}} "
                f"| {row['created_by']:<{email_w}} "
                f"| {created_at:<{_CREATED_AT_WIDTH}} "
                f"| {row['run_url']:<{url_w}} |"
            )
        lines.append(sep)
        footer_label = f"Page {page_num} · Rows {first_idx}–{last_idx} of {total_count}"
        lines.extend(["", f"   ── {footer_label} ──", ""])
        if chunk.ui_filter_url:
            lines.extend(["   🔗 View this page in UI:", f"      {chunk.ui_filter_url}", ""])
        if chunk.next_page_token:
            lines.extend(["   📄 Page token:", f"      {chunk.next_page_token}", ""])
            if chunk.next_ui_filter_url:
                lines.extend(["   ➡️  Next page in UI:", f"      {chunk.next_ui_filter_url}", ""])
        lines.append(f"   {'─' * (len(footer_label) + 6)}")
        lines.append("")
    lines.append("─" * len(sep))
    lines.append(f"✅ Total: {total_count} results across {len(page_chunks)} page(s).")
    lines.append("")
    return "\n".join(lines)


def fetch_pipeline_run_search_pages(
    *,
    client: Any,
    filter_query_str: str | None,
    limit: int,
    page_token: str | None,
    base_url: str,
    name: str | None,
    created_by: str | None,
    start_date: str | None,
    end_date: str | None,
) -> tuple[list[dict[str, Any]], list[PageChunk], str | None]:
    """Paginate through the API collecting up to ``limit`` rows."""

    all_rows: list[dict[str, Any]] = []
    page_chunks: list[PageChunk] = []
    current_token = page_token
    running_index = 0
    while running_index < limit:
        response = client.pipeline_runs_list(
            filter_query=filter_query_str,
            page_token=current_token,
            include_pipeline_names=True,
        )
        page_runs = response.get("pipeline_runs", [])
        next_token = response.get("next_page_token")
        if not page_runs:
            break
        page_runs = page_runs[: limit - running_index]
        chunk_rows: list[dict[str, Any]] = []
        for run in page_runs:
            running_index += 1
            run_id = run.get("id", "")
            chunk_rows.append(
                {
                    "index": running_index,
                    "run_id": run_id,
                    "pipeline_name": run.get("pipeline_name", ""),
                    "created_by": run.get("created_by", ""),
                    "created_at": run.get("created_at", ""),
                    "run_url": f"{base_url}/runs/{run_id}",
                }
            )
        ui_url_for_page = build_ui_filter_url(
            base_url=base_url,
            name=name,
            created_by=created_by,
            start_date=start_date,
            end_date=end_date,
            page_token=current_token,
        )
        next_ui_url = (
            build_ui_filter_url(
                base_url=base_url,
                name=name,
                created_by=created_by,
                start_date=start_date,
                end_date=end_date,
                page_token=next_token,
            )
            if next_token
            else None
        )
        page_chunks.append(
            PageChunk(
                rows=chunk_rows,
                page_token=current_token,
                next_page_token=next_token,
                ui_filter_url=ui_url_for_page,
                next_ui_filter_url=next_ui_url,
            )
        )
        all_rows.extend(chunk_rows)
        current_token = next_token
        if not current_token:
            break
    final_next_token = current_token if running_index >= limit and current_token else None
    return all_rows, page_chunks, final_next_token


def build_pipeline_run_search_result(
    *,
    all_rows: list[dict[str, Any]],
    page_chunks: list[PageChunk],
    final_next_token: str | None,
    first_ui_url: str,
) -> dict[str, Any]:
    pages_meta: list[dict[str, Any]] = []
    for idx, chunk in enumerate(page_chunks):
        pages_meta.append(
            {
                "page": idx + 1,
                "rows": f"{chunk.rows[0]['index']}–{chunk.rows[-1]['index']}",
                "ui_url": chunk.ui_filter_url,
                "page_token": chunk.page_token,
                "next_page_token": chunk.next_page_token,
                "next_ui_url": chunk.next_ui_filter_url,
            }
        )
    return {
        "runs": all_rows,
        "count": len(all_rows),
        "pages": pages_meta,
        "markdown_table": _format_mcp_table(
            rows=all_rows,
            next_page_token=final_next_token,
            ui_filter_url=first_ui_url,
        ),
        "cli_table": _format_cli_table(page_chunks=page_chunks, total_count=len(all_rows)),
        "next_page_token": final_next_token,
        "ui_filter_url": first_ui_url,
    }


def search_pipeline_runs(
    *,
    client: Any,
    name: str | None = None,
    created_by: str | None = None,
    annotations: dict[str, str | None] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    local_time: bool = False,
    query: dict[str, Any] | None = None,
    limit: int = 10,
    page_token: str | None = None,
    logger: Logger | None = None,
) -> dict[str, Any]:
    """Backward-compatible wrapper for :meth:`PipelineRunSearch.search`."""

    return PipelineRunSearch(client=client, logger=logger).search(
        name=name,
        created_by=created_by,
        annotations=annotations,
        start_date=start_date,
        end_date=end_date,
        local_time=local_time,
        query=query,
        limit=limit,
        page_token=page_token,
    )
