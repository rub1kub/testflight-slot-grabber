from __future__ import annotations

import gzip
import hashlib
import http.client
import logging
import secrets
import ssl
import time
import urllib.parse
import zlib
from pathlib import Path
from typing import Dict, Optional, Tuple

from .logging_setup import log_event, log_exception
from .models import HttpResponse


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ResponseTooLarge(RuntimeError):
    pass


class HttpClientError(OSError):
    pass


def _tls_context() -> ssl.SSLContext:
    default = ssl.create_default_context()
    paths = ssl.get_default_verify_paths()
    if paths.cafile and Path(paths.cafile).exists():
        return default
    macos_ca = Path("/etc/ssl/cert.pem")
    if macos_ca.exists():
        return ssl.create_default_context(cafile=str(macos_ca))
    return default


class TestFlightHttpClient:
    def __init__(self, timeout_seconds: float, logger: Optional[logging.Logger] = None) -> None:
        self.timeout_seconds = timeout_seconds
        self.logger = logger
        self.context = _tls_context()
        self._connection: Optional[http.client.HTTPSConnection] = None
        self._origin: Optional[Tuple[str, int]] = None

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
        self._connection = None
        self._origin = None

    def _discard_connection(self) -> None:
        try:
            self.close()
        except OSError:
            self._connection = None
            self._origin = None

    def _connection_for(self, host: str, port: int) -> Tuple[http.client.HTTPSConnection, bool]:
        origin = (host, port)
        reused = self._connection is not None and self._origin == origin
        if self._connection is None or self._origin != origin:
            self._discard_connection()
            self._connection = http.client.HTTPSConnection(
                host,
                port=port,
                timeout=self.timeout_seconds,
                context=self.context,
            )
            self._origin = origin
        return self._connection, reused

    @staticmethod
    def cache_busted_url(url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query.append(("_tfsg", f"{time.time_ns()}-{secrets.token_hex(4)}"))
        return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))

    def _request_once(
        self,
        url: str,
        range_probe_bytes: Optional[int],
    ) -> Tuple[int, Dict[str, str], str, Dict[str, object]]:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise HttpClientError(f"refusing non-HTTPS or hostless URL: {url}")
        port = parsed.port or 443
        path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        connection, connection_reused = self._connection_for(parsed.hostname, port)
        request_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip",
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        }
        if range_probe_bytes is not None:
            request_headers["Range"] = f"bytes=0-{range_probe_bytes - 1}"
        connection.request("GET", path, headers=request_headers)
        response = connection.getresponse()
        try:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            wire_bytes = len(raw)
            status = int(response.status)
            headers = {key.lower(): value for key, value in response.getheaders()}
            charset = response.headers.get_content_charset() or "utf-8"
            will_close = response.will_close
        finally:
            response.close()

        if will_close:
            self._discard_connection()
        if len(raw) > MAX_RESPONSE_BYTES:
            self._discard_connection()
            raise ResponseTooLarge(f"response exceeds {MAX_RESPONSE_BYTES} bytes")
        if headers.get("content-encoding", "").lower() == "gzip":
            if status == 206:
                # A prefix of a gzip stream has no trailer, so gzip.decompress
                # correctly rejects it. Incremental zlib still yields every
                # complete HTML byte contained in the prefix.
                raw = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw)
            else:
                raw = gzip.decompress(raw)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ResponseTooLarge(f"decoded response exceeds {MAX_RESPONSE_BYTES} bytes")
        body = raw.decode(charset, "replace")
        return status, headers, body, {
            "wire_bytes": wire_bytes,
            "decoded_bytes": len(raw),
            "body_sha256": hashlib.sha256(raw).hexdigest(),
            "connection_reused": connection_reused,
            "server_requested_close": will_close,
            "charset": charset,
            "content_encoding": headers.get("content-encoding"),
        }

    def _request_with_reconnect(
        self,
        url: str,
        range_probe_bytes: Optional[int],
        request_id: str,
        redirect_index: int,
    ) -> Tuple[int, Dict[str, str], str, Dict[str, object]]:
        last_error: Optional[BaseException] = None
        for attempt_index in range(2):
            try:
                status, headers, body, diagnostics = self._request_once(url, range_probe_bytes)
                diagnostics["attempts"] = attempt_index + 1
                return status, headers, body, diagnostics
            except ResponseTooLarge:
                raise
            except (http.client.HTTPException, OSError, ssl.SSLError) as exc:
                last_error = exc
                if self.logger is not None:
                    log_event(
                        self.logger,
                        logging.WARNING,
                        "http_attempt_failed",
                        "TestFlight HTTP attempt failed; connection will be recreated",
                        request_id=request_id,
                        redirect_index=redirect_index,
                        attempt=attempt_index + 1,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                self._discard_connection()
        raise HttpClientError(f"request failed after reconnect: {last_error}") from last_error

    def fetch(self, url: str, range_probe_bytes: Optional[int] = None) -> HttpResponse:
        request_id = secrets.token_hex(8)
        current_url = self.cache_busted_url(url)
        started = time.monotonic()
        if self.logger is not None:
            log_event(
                self.logger,
                logging.DEBUG,
                "http_request_started",
                "Starting TestFlight HTTP request",
                request_id=request_id,
                method="GET",
                target_url=url,
                request_url=current_url,
                range_probe_bytes=range_probe_bytes,
                timeout_seconds=self.timeout_seconds,
                request_headers={
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "accept-language": "en-US,en;q=0.9",
                    "accept-encoding": "gzip",
                    "cache-control": "no-cache, no-store, max-age=0",
                    "pragma": "no-cache",
                    "range": f"bytes=0-{range_probe_bytes - 1}" if range_probe_bytes is not None else None,
                    "user-agent": USER_AGENT,
                },
            )
        total_attempts = 0
        try:
            for redirect_count in range(6):
                status, headers, body, request_diagnostics = self._request_with_reconnect(
                    current_url,
                    range_probe_bytes,
                    request_id,
                    redirect_count,
                )
                total_attempts += int(request_diagnostics.get("attempts", 1))
                location = headers.get("location")
                is_redirect = status in {301, 302, 303, 307, 308} and bool(location)
                final_url = urllib.parse.urljoin(current_url, location) if is_redirect and location else current_url
                elapsed_ms = int((time.monotonic() - started) * 1000)
                diagnostics: Dict[str, object] = {
                    **request_diagnostics,
                    "request_id": request_id,
                    "method": "GET",
                    "target_url": url,
                    "request_url": current_url,
                    "range_probe_bytes": range_probe_bytes,
                    "redirect_count": redirect_count,
                    "attempts_total": total_attempts,
                    "elapsed_ms": elapsed_ms,
                    "response_headers": headers,
                }
                if not is_redirect or urllib.parse.urlsplit(final_url).scheme != "https":
                    if self.logger is not None:
                        log_event(
                            self.logger,
                            logging.DEBUG,
                            "http_response_completed",
                            "Completed TestFlight HTTP request",
                            status_code=status,
                            final_url=final_url,
                            **diagnostics,
                        )
                    return HttpResponse(
                        status_code=status,
                        final_url=final_url,
                        headers=headers,
                        body=body,
                        elapsed_ms=elapsed_ms,
                        diagnostics=diagnostics,
                    )
                if self.logger is not None:
                    log_event(
                        self.logger,
                        logging.INFO,
                        "http_redirect",
                        "Following TestFlight HTTP redirect",
                        request_id=request_id,
                        redirect_index=redirect_count,
                        status_code=status,
                        source_url=current_url,
                        destination_url=final_url,
                        response_headers=headers,
                    )
                current_url = final_url
            raise HttpClientError(f"too many redirects while fetching {url}")
        except Exception as exc:
            if self.logger is not None:
                log_exception(
                    self.logger,
                    "http_request_failed",
                    "TestFlight HTTP request failed",
                    request_id=request_id,
                    target_url=url,
                    request_url=current_url,
                    range_probe_bytes=range_probe_bytes,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    attempts_total=total_attempts,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
            raise
