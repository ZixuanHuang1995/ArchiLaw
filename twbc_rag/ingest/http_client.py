"""HTTP helpers. MOJ occasionally fails TLS handshake; retry once without verify."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from twbc_rag.ingest.types import SourceError

logger = logging.getLogger(__name__)

USER_AGENT = "twbc-rag/0.1 (educational building-code corpus; source=law.moj.gov.tw)"
_HEADERS = {"User-Agent": USER_AGENT, "Accept": "*/*"}


def download_bytes(url: str, *, timeout: float = 180.0) -> bytes:
    try:
        return _get(url, verify=True, timeout=timeout)
    except SourceError as exc:
        if not _looks_like_tls(exc):
            raise
        logger.warning(
            "TLS error fetching %s; retrying once without certificate verification",
            url,
        )
        return _get(url, verify=False, timeout=timeout)


def download_cached(url: str, dest: Path, *, refresh: bool) -> Path:
    if dest.exists() and dest.stat().st_size > 0 and not refresh:
        logger.info("Using cached download %s", dest)
        return dest
    payload = download_bytes(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    logger.info("Downloaded %s (%s bytes)", url, len(payload))
    return dest


def _get(url: str, *, verify: bool, timeout: float) -> bytes:
    last: Exception | None = None
    with httpx.Client(
        timeout=httpx.Timeout(timeout, connect=30.0),
        follow_redirects=True,
        headers=_HEADERS,
        verify=verify,
    ) as client:
        for attempt in range(3):
            try:
                response = client.get(url)
                response.raise_for_status()
                return response.content
            except httpx.HTTPStatusError as exc:
                last = exc
                status = exc.response.status_code
                if status not in {429, 500, 502, 503, 504}:
                    raise SourceError(f"GET {url} returned HTTP {status}") from exc
            except httpx.HTTPError as exc:
                last = exc
            time.sleep(1.5 * (attempt + 1))
    raise SourceError(f"GET {url} failed after retries: {last}") from last


def _looks_like_tls(exc: Exception) -> bool:
    message = str(exc).upper()
    return any(token in message for token in ("TLS", "SSL", "CERTIFICATE", "HANDSHAKE"))
