"""Static public Tangle API client.

``TangleApiClient`` is the stable wrapper class consumed by downstream tools.
Endpoint methods are generated offline into :mod:`tangle_api.generated.operations`
from the checked-in OpenAPI snapshot; handwritten methods in this file keep the
higher-level semantic helpers that downstream callers use.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, is_dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urljoin, urlparse, urlsplit, urlunsplit

import requests

from .api_transport import (
    DEFAULT_TIMEOUT_SECONDS,
    VerifyArgument,
    _join_operation_url,
    _normalize_base_url,
    _request_headers,
    _VERIFY_UNSET,
    default_base_url,
    log_http_exchange,
    resolve_verify,
    tangle_verbose_enabled,
)
from tangle_api.generated.operations import GeneratedTangleApiOperations
from . import models as _cli_models
from .logger import Logger, _null_logger, get_default_logger
from .models import (
    ComponentInfo,
    ComponentSpec,
    GetExecutionInfoResponse,
    GraphExecutionState,
    PipelineRun,
    RunDetails,
    TaskSpec,
)


class _RetryBudget:
    """Shared attempt/send/deadline budget for one logical request.

    The transient-5xx, 429 rate-limit, and 401 auth-refresh retry layers all
    draw from a single instance so a composed outage cannot multiply their
    per-layer limits. Attempts and sends are counted separately because they
    bound different things: ``attempts`` is how many logical tries remain (one
    per entry into the redirect helper) while ``sends`` is how many physical
    ``session.request`` calls remain, every same-origin redirect hop included.
    Keeping ``sends`` larger than ``attempts`` is what lets a legal redirect
    chain still be followed on a retry, instead of its hops being spent as if
    they were tries. ``deadline`` is a ``time.monotonic`` value past which no
    further send is admitted.
    """

    __slots__ = ("attempts", "deadline", "sends")

    def __init__(self, max_attempts: int, max_sends: int, deadline: float) -> None:
        self.attempts = max_attempts
        self.sends = max_sends
        self.deadline = deadline

    def start_attempt(self) -> None:
        """Charge one logical try; callers gate on :meth:`can_retry` first."""

        self.attempts -= 1

    def try_consume_send(self) -> bool:
        """Atomically admit one physical send, charging the budget for it.

        Checking and charging together, immediately before the send, is what
        stops a request from slipping out after a long ``Retry-After`` or
        backoff has already carried the clock past the deadline.
        """

        if self.sends <= 0 or time.monotonic() >= self.deadline:
            return False
        self.sends -= 1
        return True

    def exhaustion_reason(self) -> str:
        """Why :meth:`try_consume_send` refused, for the exhaustion error text.

        Mirrors that method's check order so the reported cause is the one that
        actually stopped the send.
        """

        return "send pool exhausted" if self.sends <= 0 else "deadline exceeded"

    def can_retry(self) -> bool:
        return self.attempts > 0 and self.sends > 0 and time.monotonic() < self.deadline

    def allows_wait(self, delay: float) -> bool:
        """True when sleeping ``delay`` would still leave time to send again.

        Waits are bounded by the remaining deadline rather than truncated to it:
        a sleep that would reach the deadline can only be followed by a send the
        budget must refuse, so the caller stops immediately instead.
        """

        return delay < self.deadline - time.monotonic()


class TangleApiClient(GeneratedTangleApiOperations):
    """Single public API wrapper for Tangle backends.

    The constructor keeps the historical ``tangle-deploy`` shape while also
    accepting the auth/header knobs used by the dynamic-discovery client. No
    OpenAPI schema is loaded at runtime; all endpoint wrappers are checked in.
    """

    _REDIRECT_STATUSES = {301, 302, 303, 307, 308}
    _MAX_REDIRECTS = 5
    _RATE_LIMIT_BACKOFF_SECONDS = 1.0
    _MAX_RETRY_AFTER_SECONDS = 60.0
    # Opening a log stream retries transient failures (transport-open errors
    # and retryable 5xx) with doubling backoff, spent entirely before any line
    # is yielded. Connect and response-header reads are bounded; after the final
    # response is accepted, only its body socket is reset to unbounded idle.
    # An already-open stream that drops is the caller's to handle; never
    # re-opening an established stream means lines cannot be duplicated.
    _RETRYABLE_STREAM_STATUSES = frozenset({500, 502, 503, 504})
    _MAX_STREAM_OPEN_ATTEMPTS = 7
    _STREAM_OPEN_BACKOFF_SECONDS = 1.0
    _MAX_STREAM_OPEN_BACKOFF_SECONDS = 30.0
    # Requests that cannot be replayed after a transient failure (mutating
    # methods and streamed GETs) keep the historical four-attempt 429 allowance
    # and its 1/2/4s backoff rather than spending the larger shared GET budget
    # on rate limiting alone.
    _MAX_RATE_LIMIT_RETRIES = 3
    _RETRYABLE_GET_STATUSES = frozenset({500, 502, 503, 504})
    _MAX_GET_RETRIES = 6
    _GET_RETRY_BACKOFF_SECONDS = 1.0
    _MAX_GET_RETRY_BACKOFF_SECONDS = 30.0
    # A single logical request may issue at most ``_MAX_GET_RETRIES + 1``
    # attempts and ``_MAX_PHYSICAL_SENDS`` physical sends -- counting every
    # same-origin redirect hop -- shared across the transient-5xx, 429
    # rate-limit, and 401 auth-refresh layers, and must not spend more than
    # ``_MAX_RETRY_ELAPSED_SECONDS`` retrying. One shared budget prevents the
    # layers from multiplying into a large physical request count during an
    # outage (e.g. interleaved 503/429 responses, a 401 mid-sequence, or a
    # redirect chain in front of every retry).
    #
    # Sends are pooled separately from attempts because one attempt behind a
    # redirect chain costs several sends. The pool is the larger of two floors:
    # two full ``_MAX_REDIRECTS``-deep chains (12), so such a chain stays
    # followable both before and after a 401 refresh, and two sends per logical
    # attempt (14), so a one-hop gateway does not halve the effective retry
    # count. The second dominates at current constants. The worst case stays far
    # below attempts x chain length.
    _MAX_PHYSICAL_SENDS = max(2 * (_MAX_REDIRECTS + 1), 2 * (_MAX_GET_RETRIES + 1))
    _MAX_RETRY_ELAPSED_SECONDS = 120.0

    def __init__(
        self,
        base_url: str | None = None,
        *,
        logger: Logger | None = None,
        verbose: bool = False,
        headers: Mapping[str, str] | None = None,
        token: str | None = None,
        auth_header: str | None = None,
        header: list[str] | str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
        include_env_credentials: bool = True,
        verify: VerifyArgument = None,
    ) -> None:
        self.base_url = _normalize_base_url(base_url or default_base_url())
        env_verbose = tangle_verbose_enabled()
        self.verbose = verbose or env_verbose
        self.logger = logger or (get_default_logger() if self.verbose else _null_logger)
        self.headers = dict(headers or {})
        self.token = token
        self.auth_header = auth_header
        self.header = header
        self.timeout = timeout
        self.session = session or requests.Session()
        self.include_env_credentials = include_env_credentials
        self._verify = resolve_verify(verify)

    def _response_model(self, model_name: str, default: Any) -> Any:
        """Use CLI-composed models for generated operation deserialization."""

        return getattr(_cli_models, model_name, default)

    def set_verbose(self, enabled: bool) -> None:
        """Enable or disable request logging."""

        self.verbose = enabled

    def _refresh_auth(self) -> None:
        """Hook for subclasses to refresh auth before/retry after a request.

        Subclasses commonly mutate ``self.headers`` or session state here. The
        base implementation intentionally does nothing.
        """

    def _make_request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        json_data: Any = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Issue an HTTP request and return the raw ``requests.Response``.

        This method preserves the subclass extension point used by
        ``tangle-deploy``: auth can be refreshed by overriding
        :meth:`_refresh_auth`, and callers that need streaming can pass standard
        ``requests`` keyword arguments such as ``stream=True``.
        """

        if "json" in kwargs and json_data is None:
            json_data = kwargs.pop("json")
        timeout = kwargs.pop("timeout", self.timeout)
        extra_headers = kwargs.pop("headers", None)
        url = self._url(path)
        clean_params = self._clean_mapping(params)
        request_method = method.upper()

        budget = _RetryBudget(
            self._MAX_GET_RETRIES + 1,
            self._MAX_PHYSICAL_SENDS,
            time.monotonic() + self._MAX_RETRY_ELAPSED_SECONDS,
        )

        self._refresh_auth()
        response = self._request_with_rate_limit_retries(
            request_method,
            url,
            params=clean_params,
            json_data=json_data,
            extra_headers=extra_headers,
            timeout=timeout,
            request_kwargs=kwargs,
            budget=budget,
        )
        # The auth-refresh retry draws from the same budget, so a 401 late in a
        # transient/rate-limit sequence cannot start a fresh round of retries.
        if response.status_code == 401 and budget.can_retry():
            # The 401 response is discarded by the auth-refresh retry. For a
            # streamed request it is an open streamed connection, so close it
            # before refreshing auth and issuing the second request to avoid
            # leaking it. ``response.headers`` stays available after close.
            if kwargs.get("stream"):
                response.close()
            self._refresh_auth()
            try:
                response = self._request_with_rate_limit_retries(
                    request_method,
                    url,
                    params=clean_params,
                    json_data=json_data,
                    extra_headers=extra_headers,
                    timeout=timeout,
                    request_kwargs=kwargs,
                    budget=budget,
                )
            except requests.exceptions.RetryError:
                # The retry ran out of sends before any response came back. The
                # 401 already in hand is a real backend answer, so report it
                # rather than an exhaustion error.
                return response
        return response

    def _request_with_rate_limit_retries(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        json_data: Any,
        extra_headers: Mapping[str, str] | None,
        timeout: float | tuple[float, float | None],
        request_kwargs: Mapping[str, Any],
        budget: _RetryBudget,
    ) -> requests.Response:
        # Replayable GETs are bounded by the shared budget alone; everything else
        # bypasses the transient layer and so keeps its historical 429 cap.
        max_rounds = (
            None
            if self._is_transient_retryable(method, request_kwargs)
            else self._MAX_RATE_LIMIT_RETRIES
        )
        rate_limit_round = 0
        last_response: requests.Response | None = None
        while True:
            try:
                response = self._request_with_transient_retries(
                    method,
                    url,
                    params=params,
                    json_data=json_data,
                    extra_headers=extra_headers,
                    timeout=timeout,
                    request_kwargs=request_kwargs,
                    budget=budget,
                )
            except requests.exceptions.RetryError:
                # Sends ran out part-way through a redirect chain. The 429 from
                # the previous round is a real backend answer and reports better
                # than the exhaustion error.
                if last_response is None:
                    raise
                return last_response
            if response.status_code != 429:
                return response
            if max_rounds is not None and rate_limit_round >= max_rounds:
                return response
            delay = self._rate_limit_delay(response, rate_limit_round)
            # A 429 retry re-enters the transient layer, so it must draw from the
            # shared budget rather than a per-round allowance. A ``Retry-After``
            # that would outlast the deadline ends the sequence here instead of
            # sleeping into a send the budget must then refuse.
            if not budget.can_retry() or not budget.allows_wait(delay):
                return response
            # Release the superseded 429 so its connection is not held for the
            # whole wait. Streamed responses are released here too: a 429 is
            # never the stream the caller asked for, and nothing has read its
            # body. The status stays readable afterwards, so this response is
            # still reportable if a later round exhausts the budget mid-chain.
            last_response = response
            self._release_response(response)
            self._sleep_for_rate_limit(delay)
            rate_limit_round += 1

    @staticmethod
    def _is_transient_retryable(method: str, request_kwargs: Mapping[str, Any]) -> bool:
        """Only non-streamed GETs may be replayed after a transient failure.

        Mutating methods must never be duplicated, and a streamed GET's consumer
        owns any stream-open retries.
        """

        return method.upper() == "GET" and not request_kwargs.get("stream")

    def _request_with_transient_retries(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        json_data: Any,
        extra_headers: Mapping[str, str] | None,
        timeout: float | tuple[float, float | None],
        request_kwargs: Mapping[str, Any],
        budget: _RetryBudget,
    ) -> requests.Response:
        """Retry idempotent GETs on transient 5xx and transport errors.

        Mutating methods are sent once (never duplicated). Streamed GETs bypass
        this layer so their consumer owns any stream-open retries. 429s are left
        to the rate-limit layer, whose retries re-enter this layer with a fresh
        backoff. ``SSLError`` raises immediately: certificate failures are
        deterministic, so retrying only delays the report. Every physical send
        draws from the shared ``budget`` at the send boundary, so the transient,
        rate-limit, and auth-refresh layers cannot multiply into a large request
        count. Each doubling sleep is capped at
        ``_MAX_GET_RETRY_BACKOFF_SECONDS``, is skipped entirely when it would
        outlast the deadline, and is announced through ``self.logger`` (a null
        logger on non-verbose clients built without one), so a stalled GET is
        bounded.
        """

        if not self._is_transient_retryable(method, request_kwargs):
            return self._request_with_same_origin_redirects(
                method,
                url,
                params=params,
                json_data=json_data,
                extra_headers=extra_headers,
                timeout=timeout,
                request_kwargs=request_kwargs,
                budget=budget,
            )
        backoff = self._GET_RETRY_BACKOFF_SECONDS
        attempt = 0
        last_response: requests.Response | None = None
        while True:
            attempt += 1
            delay = min(backoff, self._MAX_GET_RETRY_BACKOFF_SECONDS)
            backoff *= 2.0
            try:
                response = self._request_with_same_origin_redirects(
                    method,
                    url,
                    params=params,
                    json_data=json_data,
                    extra_headers=extra_headers,
                    timeout=timeout,
                    request_kwargs=request_kwargs,
                    budget=budget,
                )
            except requests.exceptions.RetryError:
                # Sends ran out part-way through a redirect chain. A completed
                # 5xx from an earlier attempt tells the caller what the backend
                # actually said, so ``raise_for_status`` still reports the true
                # status instead of an exhaustion error.
                if last_response is None:
                    raise
                return last_response
            # Transient transport failures (reset/refused, timeout, truncated or
            # corrupt body) can succeed on retry; other request errors surface.
            except (
                requests.ConnectionError,
                requests.Timeout,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ContentDecodingError,
            ) as exc:
                # SSLError subclasses ConnectionError but signals a certificate
                # or TLS configuration problem that no retry can fix.
                if isinstance(exc, requests.exceptions.SSLError):
                    raise
                # Budget exhausted (attempts or deadline), or the backoff alone
                # would outlast the deadline: surface the failure.
                if not budget.can_retry() or not budget.allows_wait(delay):
                    raise
                self._sleep_for_transient_retry(delay, attempt, type(exc).__name__)
            else:
                if response.status_code not in self._RETRYABLE_GET_STATUSES:
                    return response
                # Budget exhausted, or the backoff alone would outlast the
                # deadline: return the final 5xx for raise_for_status.
                if not budget.can_retry() or not budget.allows_wait(delay):
                    return response
                # Release the intermediate response so its connection returns to
                # the pool. Its body is already buffered (these GETs are never
                # streamed), so it stays reportable if the retry runs out of
                # sends mid-chain.
                last_response = response
                self._release_response(response)
                self._sleep_for_transient_retry(delay, attempt, f"HTTP {response.status_code}")

    def _sleep_for_transient_retry(self, delay: float, attempt: int, reason: str) -> None:
        self.logger.warn(  # noqa: G010 - Logger intentionally exposes warn().
            f"transient {reason} on GET; retrying in {delay:.1f}s "
            f"(attempt {attempt + 1}/{self._MAX_GET_RETRIES + 1})"
        )
        time.sleep(delay)

    def _rate_limit_delay(self, response: requests.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        delay = self._retry_after_delay(retry_after)
        if delay is None:
            delay = self._RATE_LIMIT_BACKOFF_SECONDS * (2 ** attempt)
        return min(delay, self._MAX_RETRY_AFTER_SECONDS)

    def _sleep_for_rate_limit(self, delay: float) -> None:
        if self.verbose:
            self.logger.info(f"429 rate limited; retrying in {delay:.1f}s")
        time.sleep(delay)

    def _sleep_for_stream_open_retry(self, backoff: float, next_attempt: int, reason: str) -> None:
        delay = min(backoff, self._MAX_STREAM_OPEN_BACKOFF_SECONDS)
        self.logger.warn(
            f"transient {reason} opening log stream; retrying in {delay:.1f}s "
            f"(attempt {next_attempt}/{self._MAX_STREAM_OPEN_ATTEMPTS})"
        )
        time.sleep(delay)

    @staticmethod
    def _retry_after_delay(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            pass
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            return None
        return max(0.0, retry_at.timestamp() - time.time())

    def _request_with_same_origin_redirects(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        json_data: Any,
        extra_headers: Mapping[str, str] | None,
        timeout: float | tuple[float, float | None],
        request_kwargs: Mapping[str, Any],
        budget: _RetryBudget,
    ) -> requests.Response:
        """Send one request, following only same-origin redirects.

        The client may carry custom auth headers/cookies in ``session.headers``.
        ``requests`` does not strip those custom credentials on cross-origin
        redirects, so redirects are handled manually and constrained to the
        original origin.

        This helper is entered once per logical attempt, so it charges the
        shared budget one attempt on entry and one send per hop immediately
        before that hop goes out. Charging hops keeps a redirect in front of
        every retry from multiplying the total request count, while charging
        them against the send pool rather than the attempt count leaves a legal
        chain followable on every attempt.
        """

        budget.start_attempt()
        current_method = method
        current_url = url
        current_params = params
        current_json = json_data
        response: requests.Response | None = None

        for _ in range(self._MAX_REDIRECTS + 1):
            if not budget.try_consume_send():
                raise requests.exceptions.RetryError(
                    f"Retry budget exhausted ({budget.exhaustion_reason()}) "
                    f"while sending {current_method} {self._credential_safe_url(current_url)}"
                )
            request_headers = self._headers(extra_headers)
            call_kwargs = dict(request_kwargs)
            if self._verify is not _VERIFY_UNSET and "verify" not in call_kwargs:
                call_kwargs["verify"] = self._verify
            response = self.session.request(
                current_method,
                current_url,
                params=current_params,
                json=current_json,
                headers=request_headers,
                timeout=timeout,
                allow_redirects=False,
                **call_kwargs,
            )
            if self.verbose:
                # For streamed responses, reading ``response.text`` would buffer
                # the entire body here, defeating callers that stream via
                # ``iter_content``/``iter_lines``; a followed container-log
                # stream may never terminate. Log a placeholder and leave the
                # body unread.
                response_body = (
                    "<streaming body omitted>"
                    if request_kwargs.get("stream")
                    else response.text
                )
                log_http_exchange(
                    self.logger,
                    method=current_method,
                    url=current_url,
                    request_headers=request_headers,
                    request_body=current_json,
                    response_status=response.status_code,
                    response_headers=dict(response.headers),
                    response_body=response_body,
                )
            if response.status_code not in self._REDIRECT_STATUSES:
                return response

            location = response.headers.get("Location")
            if not location:
                return response

            next_url = urljoin(response.url, location)
            if not self._same_origin(response.url, next_url):
                # Close before raising so callers that catch this and fall back
                # to another route (or a streamed open) do not leak the open
                # streamed redirect response and its pooled connection.
                try:
                    response.close()
                except Exception:
                    pass
                raise requests.HTTPError(
                    f"Refusing to follow cross-origin redirect from {response.url} to {next_url}",
                    response=response,
                )

            self._release_response(response)
            if response.status_code == 303 or (
                response.status_code in {301, 302} and current_method not in {"GET", "HEAD"}
            ):
                current_method = "GET"
                current_json = None
            current_url = next_url
            current_params = None

        raise requests.TooManyRedirects(
            f"Exceeded {self._MAX_REDIRECTS} redirects for {url}",
            response=response,
        )

    @staticmethod
    def _credential_safe_url(url: str) -> str:
        """Render a URL for error text with every credential-bearing part removed.

        A same-origin redirect can land on a signed URL whose query, fragment,
        or authority userinfo carries credentials, and this rendering flows
        into CLI output and logs. Only scheme, host[:port], and path survive.
        ``urlsplit`` rejects some malformed authorities (an unclosed IPv6
        bracket, for one), and ``.hostname``/``.port`` reject malformed ports,
        so the authority is taken from the raw netloc and parse failures fall
        back to a placeholder rather than letting error formatting raise.
        """

        try:
            parts = urlsplit(url)
        except ValueError:
            return "<unparseable URL>"
        netloc = parts.netloc.rpartition("@")[2]
        return urlunsplit((parts.scheme, netloc, parts.path, "", "")) or "<unparseable URL>"

    @staticmethod
    def _release_response(response: requests.Response) -> None:
        """Return an abandoned response's connection to the pool."""

        try:
            response.close()
        except Exception:
            pass

    @staticmethod
    def _same_origin(left: str, right: str) -> bool:
        left_parts = urlparse(left)
        right_parts = urlparse(right)
        return (
            left_parts.scheme.lower() == right_parts.scheme.lower()
            and left_parts.netloc.lower() == right_parts.netloc.lower()
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        path_params: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        json_data: Any = None,
        response_model: Any = None,
    ) -> Any:
        formatted_path = self._format_path(path, path_params)
        response = self._make_request(method, formatted_path, params=params, json_data=json_data)
        response.raise_for_status()
        data = self._decode_response(response)
        if response_model is not None and isinstance(data, dict):
            return response_model.from_dict(data)
        if response_model is not None and isinstance(data, list):
            return [
                response_model.from_dict(item) if isinstance(item, dict) else item
                for item in data
            ]
        return data

    def _headers(self, extra_headers: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = dict(self.headers)
        if extra_headers:
            headers.update({name: str(value) for name, value in extra_headers.items()})
        return _request_headers(
            self.token,
            self.header,
            self.auth_header,
            headers,
            include_env_credentials=self.include_env_credentials,
        )

    def _url(self, path: str) -> str:
        return _join_operation_url(self.base_url, path)

    @staticmethod
    def _format_path(path: str, path_params: Mapping[str, Any] | None = None) -> str:
        if not path_params:
            return path
        for name, value in path_params.items():
            path = path.replace("{" + name + "}", quote(str(value), safe=""))
        return path

    @staticmethod
    def _clean_mapping(values: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not values:
            return None
        cleaned = {key: value for key, value in values.items() if value is not None}
        return cleaned or None

    @staticmethod
    def _decode_response(response: requests.Response) -> Any:
        if response.status_code == 204 or not response.content:
            return None
        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type.lower():
            return response.json()
        try:
            return response.json()
        except ValueError:
            return response.text

    # ---- Handwritten semantic helpers consumed by tangle-deploy ----------

    def get_execution_details(self, execution_id: str) -> GetExecutionInfoResponse:
        details = self.executions_details(execution_id)
        self._enrich_execution_tree(details)
        return details

    @staticmethod
    def _make_stream_body_idle_unbounded(response: requests.Response) -> None:
        """Clear the accepted response socket's timeout for quiet body reads.

        ``requests`` has no public API for using a finite response-header timeout
        followed by an unbounded streamed-body timeout. Keep the locked
        requests 2.34.2 / urllib3 2.7.0 transport access isolated here and
        fail closed if their response layout changes.
        """

        raw = response.raw
        socket = None
        try:
            socket = raw._fp.fp.raw._sock  # type: ignore[attr-defined]
        except AttributeError:
            connection = getattr(raw, "connection", None)
            socket = getattr(connection, "sock", None)
        settimeout = getattr(socket, "settimeout", None)
        if not callable(settimeout):
            response.close()
            raise requests.ConnectionError(
                "opened log stream but could not disable the body read timeout"
            )
        try:
            settimeout(None)
        except Exception as exc:
            response.close()
            raise requests.ConnectionError(
                "opened log stream but could not disable the body read timeout"
            ) from exc

    def stream_execution_container_log(self, execution_id: str) -> requests.Response:
        """Open the streaming container-log response for ``execution_id``.

        The endpoint delivers raw log lines over a long-lived chunked HTTP
        response; the client streams those lines as-is and does no
        event-protocol parsing.

        Establishing the stream (open + status check) follows a transient-error
        retry budget: transport-open errors (connection/timeout) and retryable
        5xx responses are retried with exponential backoff before any line is
        read. Same-origin redirect protection errors (cross-origin ``HTTPError``
        / ``TooManyRedirects``) are not transport blips and propagate
        immediately. Once the stream is open the caller owns the response and
        must close it; :meth:`iter_execution_container_log_lines` does that.

        The request uses finite ``(connect, read)`` timeouts of
        ``(self.timeout, self.timeout)`` through receipt of the final response
        headers. Once that response is accepted, its body socket alone is reset
        to an unbounded idle timeout, because a healthy follow stream can stay
        silent for as long as the container emits no output.
        """

        path = self._format_path(
            "/api/executions/{id}/stream_container_log",
            {"id": execution_id},
        )
        backoff = self._STREAM_OPEN_BACKOFF_SECONDS
        last_exc: requests.RequestException | None = None
        last_error_response: requests.Response | None = None
        for attempt in range(1, self._MAX_STREAM_OPEN_ATTEMPTS + 1):
            try:
                response = self._make_request(
                    "GET", path, stream=True, timeout=(self.timeout, self.timeout)
                )
            except (requests.HTTPError, requests.TooManyRedirects) as exc:
                # Same-origin redirect guard errors carry the rejected streamed
                # response and are intentionally not retried. No iterator ever
                # receives that response, so close it before re-raising.
                if exc.response is not None:
                    exc.response.close()
                raise
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                last_error_response = None
                if attempt == self._MAX_STREAM_OPEN_ATTEMPTS:
                    break
                self._sleep_for_stream_open_retry(backoff, attempt + 1, type(exc).__name__)
                backoff *= 2.0
                continue
            if response.status_code in self._RETRYABLE_STREAM_STATUSES:
                response.close()
                last_error_response = response
                last_exc = None
                if attempt == self._MAX_STREAM_OPEN_ATTEMPTS:
                    break
                self._sleep_for_stream_open_retry(
                    backoff, attempt + 1, f"HTTP {response.status_code}"
                )
                backoff *= 2.0
                continue
            try:
                response.raise_for_status()
            except requests.HTTPError:
                # Non-retryable status (e.g. 400/403/404): close the open
                # streamed response before propagating so it is not leaked.
                response.close()
                raise
            self._make_stream_body_idle_unbounded(response)
            return response
        if last_exc is not None:
            raise last_exc
        if last_error_response is not None:
            last_error_response.raise_for_status()
        # Defensive: every exhausted attempt records either a transport error
        # (re-raised above) or a retryable-status response (raise_for_status
        # always raises for those), so this cannot be reached.
        raise RuntimeError(  # pragma: no cover
            "log stream open retries exhausted without a failure to re-raise"
        )

    def iter_execution_container_log_lines(self, execution_id: str) -> Iterable[str]:
        """Return an iterator of decoded container-log lines for ``execution_id``.

        The stream is opened eagerly, so open failures (HTTP status or transport
        errors) raise from this call rather than on first iteration; anything
        raised while iterating is a drop of an already-open stream. The
        underlying streaming response is always closed when iteration finishes
        or the consumer stops early.
        """

        response = self.stream_execution_container_log(execution_id)

        def lines() -> Iterator[str]:
            try:
                # Decode whole ``bytes`` lines as UTF-8 explicitly rather than
                # via ``decode_unicode=True``: requests' charset guessing falls
                # back to latin-1 when the response declares no charset and
                # would mojibake non-ASCII output. Whole-line decoding also
                # reassembles multibyte sequences split across stream chunks.
                for raw in response.iter_lines():
                    yield raw.decode("utf-8", "replace")
            finally:
                response.close()

        return lines()

    def get_component_spec(self, digest: str) -> ComponentSpec:
        """Return a parsed domain component spec from the generated component endpoint."""

        return ComponentSpec.from_dict(_to_plain(self.components_get(digest)))

    def resolve_digest(self, digest: str) -> str:
        """Resolve a component digest/name, following deprecation successors."""

        current = digest
        seen: set[str] = set()

        while current not in seen:
            seen.add(current)
            matches = self._published_component_rows(include_deprecated=True, digest=current)
            if not matches:
                matches = self._published_component_rows(
                    include_deprecated=True,
                    name_substring=current,
                )
            if len(matches) != 1:
                return current

            component = matches[0]
            resolved = str(component.get("digest") or current)
            successor = component.get("superseded_by")
            if component.get("deprecated") and successor:
                current = str(successor)
                continue
            return resolved

        return current

    def _published_component_rows(
        self,
        include_deprecated: bool = False,
        name_substring: str | None = None,
        published_by_substring: str | None = None,
        digest: str | None = None,
    ) -> list[dict[str, Any]]:
        data = _to_plain(
            self.published_components_list(
                include_deprecated=include_deprecated,
                name_substring=name_substring,
                published_by_substring=published_by_substring,
                digest=digest,
            )
        )
        if isinstance(data, dict):
            return list(data.get("published_components") or [])
        return list(data or [])

    def list_published_component_infos(
        self,
        include_deprecated: bool = False,
        name_substring: str | None = None,
        published_by_substring: str | None = None,
        digest: str | None = None,
        *,
        fetch_specs: bool = False,
    ) -> list[ComponentInfo]:
        infos = [
            ComponentInfo.from_dict(component)
            for component in self._published_component_rows(
                include_deprecated=include_deprecated,
                name_substring=name_substring,
                published_by_substring=published_by_substring,
                digest=digest,
            )
        ]
        if fetch_specs:
            for info in infos:
                if not info.digest:
                    continue
                try:
                    info.component_spec = self.get_component_spec(info.digest)
                except Exception as exc:  # pragma: no cover - best-effort enrichment
                    info.spec_error = str(exc)
        return infos

    def find_existing_components(
        self,
        components: Iterable[ComponentSpec | Mapping[str, Any] | str] | None = None,
        *,
        names: Iterable[str] | None = None,
        digests: Iterable[str] | None = None,
        include_deprecated: bool = False,
        published_by: str | None = None,
        published_by_substring: str | None = None,
        verbose: bool = False,
    ) -> list[ComponentInfo]:
        """Find published components matching component specs, names, or digests.

        ``components`` may contain domain component specs, mapping-like component
        references, or plain component names. Results are de-duplicated by digest
        when available, falling back to name.
        """

        search_names = set(names or [])
        search_digests = set(digests or [])
        for component in components or []:
            data = _to_plain(component)
            if isinstance(component, str):
                search_names.add(component)
            elif isinstance(component, ComponentSpec):
                search_names.update(name for name in component.search_names if name)
                if component.digest:
                    search_digests.add(component.digest)
            elif isinstance(data, Mapping):
                if data.get("name"):
                    search_names.add(str(data["name"]))
                if data.get("digest"):
                    search_digests.add(str(data["digest"]))

        publisher_filter = published_by_substring or published_by
        found: dict[str, ComponentInfo] = {}

        def add(info: ComponentInfo) -> None:
            key = info.digest or info.name
            if not key:
                return
            found[key] = info
            if verbose:
                self.logger.info(f"   Found existing component: {info.name} ({key[:16]}...)")

        for digest in search_digests:
            for info in self.list_published_component_infos(
                include_deprecated=include_deprecated,
                published_by_substring=publisher_filter,
                digest=digest,
            ):
                add(info)
        for name in search_names:
            for info in self.list_published_component_infos(
                include_deprecated=include_deprecated,
                published_by_substring=publisher_filter,
                name_substring=name,
            ):
                if info.name.lower() == name.lower():
                    add(info)
        return list(found.values())

    def get_run_details(
        self,
        run_id: str,
        include_implementations: bool = False,
        include_annotations: bool = False,
        include_execution_state: bool = False,
        execution_id: str | None = None,
    ) -> RunDetails:
        annotations_run_id: str | None = run_id
        try:
            run = PipelineRun.from_dict(_to_plain(self.pipeline_runs_get(run_id)))
            root_execution_id = execution_id or run.root_execution_id
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404 or execution_id is not None:
                raise
            root_execution_id = run_id
            annotations_run_id = None
            run = PipelineRun(
                id=run_id,
                root_execution_id=root_execution_id,
                raw={"id": run_id, "root_execution_id": root_execution_id},
            )

        execution = self.get_execution_details(root_execution_id) if root_execution_id else None
        if execution and not include_implementations:
            self._strip_execution_raw_tasks_for_run_details(execution)
            execution.strip_implementations()
        raw_annotations = (
            self.pipeline_runs_annotations(annotations_run_id)
            if include_annotations and annotations_run_id
            else None
        )
        annotations = raw_annotations if isinstance(raw_annotations, dict) else None
        execution_state = (
            GraphExecutionState.from_dict(
                _to_plain(self.executions_graph_execution_state(root_execution_id))
            )
            if include_execution_state and root_execution_id
            else None
        )
        return RunDetails(
            run=run,
            execution=execution,
            annotations=annotations,
            execution_state=execution_state,
        )

    def get_run_pipeline_spec(self, run_id: str) -> TaskSpec | None:
        try:
            run = self.pipeline_runs_get(run_id)
            root_execution_id = getattr(run, "root_execution_id", None)
            if root_execution_id is None and isinstance(run, dict):
                root_execution_id = run.get("root_execution_id")
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
            root_execution_id = run_id

        if not root_execution_id:
            return None
        execution = self.executions_details(root_execution_id)
        return execution.task_spec

    def _enrich_execution_tree(self, execution: GetExecutionInfoResponse) -> None:
        child_ids = execution.raw.get("child_task_execution_ids") or {}
        if not isinstance(child_ids, dict):
            return

        raw_tasks = self._execution_graph_tasks(execution)
        for task_name, child_execution_id in child_ids.items():
            if not child_execution_id:
                continue
            child = self.executions_details(child_execution_id)
            self._enrich_execution_tree(child)
            execution.child_executions[task_name] = child

            task = execution.task_spec.graph_tasks.get(task_name)
            raw_task = raw_tasks.get(task_name) if isinstance(raw_tasks, dict) else None
            if raw_task is None and task is not None:
                raw_task = task.raw

            context = {
                "execution_id": child.id,
                "input_artifacts": child.input_artifacts,
                "output_artifacts": child.output_artifacts,
            }
            if child.raw.get("state") is not None:
                context["state"] = child.raw["state"]

            if task is not None:
                task.raw.update(context)
            if isinstance(raw_task, dict):
                raw_task.update(context)
                child_impl = (
                    child.task_spec.component_spec.implementation
                    if child.task_spec.component_spec
                    else None
                )
                raw_spec = raw_task.get("componentRef", {}).get("spec")
                if isinstance(raw_spec, dict) and child_impl:
                    raw_spec["implementation"] = child_impl

    @staticmethod
    def _execution_graph_tasks(execution: GetExecutionInfoResponse) -> dict[str, Any]:
        implementation = (
            execution.task_spec.component_spec.implementation
            if execution.task_spec.component_spec
            else None
        )
        if not isinstance(implementation, dict):
            return {}
        graph = implementation.get("graph")
        if not isinstance(graph, dict):
            return {}
        tasks = graph.get("tasks")
        return tasks if isinstance(tasks, dict) else {}

    def _strip_execution_raw_tasks_for_run_details(
        self,
        execution: GetExecutionInfoResponse,
    ) -> None:
        for raw_task in self._execution_graph_tasks(execution).values():
            if isinstance(raw_task, dict):
                self._strip_raw_task_for_run_details(raw_task)
        for child in execution.child_executions.values():
            self._strip_execution_raw_tasks_for_run_details(child)

    def _strip_raw_task_for_run_details(self, task: dict[str, Any]) -> None:
        component_ref = task.get("componentRef")
        if not isinstance(component_ref, dict):
            return
        component_ref.pop("text", None)
        spec = component_ref.get("spec")
        if not isinstance(spec, dict):
            return

        annotations = spec.get("metadata", {}).get("annotations")
        if isinstance(annotations, dict):
            for key in ComponentSpec._STRIP_ANNOTATION_KEYS:
                annotations.pop(key, None)

        implementation = spec.get("implementation")
        if not isinstance(implementation, dict):
            return
        graph = implementation.get("graph")
        if isinstance(graph, dict) and isinstance(graph.get("tasks"), dict):
            for child_task in graph["tasks"].values():
                if isinstance(child_task, dict):
                    self._strip_raw_task_for_run_details(child_task)
        else:
            spec.pop("implementation", None)


def _to_plain(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return value.model_dump(by_alias=True)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_to_plain(item) for item in value)
    if isinstance(value, dict):
        return {key: _to_plain(item) for key, item in value.items()}
    return value


__all__ = ["TangleApiClient"]
