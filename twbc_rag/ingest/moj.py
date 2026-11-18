"""National laws-and-regulations database (全國法規資料庫) bulk JSON.

The open API is two zip files, not a per-pcode endpoint:

* https://law.moj.gov.tw/api/ch/law/json
* https://law.moj.gov.tw/api/ch/order/json

Each zip holds one JSON document: ``{"UpdateDate": "...", "Laws": [...]}``.
There is no ``pcode`` field. It is the query parameter on ``LawURL``.
The attachment array is spelled ``LawAttachements`` in the official schema.
"""

from __future__ import annotations

import json
import logging
import re
import zipfile
from pathlib import Path

from twbc_rag.ingest.dates import parse_moj_date
from twbc_rag.ingest.http_client import download_cached
from twbc_rag.ingest.types import RawArticle, RawAttachment, RawLaw, SourceError

logger = logging.getLogger(__name__)

MOJ_LAW_URL = "https://law.moj.gov.tw/api/ch/law/json"
MOJ_ORDER_URL = "https://law.moj.gov.tw/api/ch/order/json"
_PCODE = re.compile(r"pcode=([A-Za-z]\d+)", re.IGNORECASE)


def pcode_from_url(url: str) -> str | None:
    match = _PCODE.search(url or "")
    if not match:
        return None
    return match.group(1).upper()


def fetch_moj_payload(url: str, dest: Path, *, refresh: bool) -> dict:
    path = download_cached(url, dest, refresh=refresh)
    return read_moj_zip(path)


def read_moj_zip(path: Path) -> dict:
    if not zipfile.is_zipfile(path):
        raise SourceError(f"{path} is not a zip file (MOJ may have returned an error page)")
    with zipfile.ZipFile(path) as archive:
        json_names = [name for name in archive.namelist() if name.lower().endswith(".json")]
        if not json_names:
            raise SourceError(f"No JSON member in {path}")
        raw = archive.read(json_names[0])
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise SourceError(f"Invalid JSON in {path}") from exc
    if not isinstance(payload, dict) or "Laws" not in payload:
        raise SourceError(f"Unexpected MOJ payload shape in {path}")
    return payload


def parse_moj_payload(payload: dict, pcodes: set[str] | None = None) -> list[RawLaw]:
    update = str(payload.get("UpdateDate") or "")
    laws: list[RawLaw] = []
    for obj in payload.get("Laws") or []:
        if not isinstance(obj, dict):
            continue
        url = str(obj.get("LawURL") or "")
        pcode = pcode_from_url(url)
        if pcode is None:
            logger.warning("Skipping MOJ record with no pcode in LawURL: %s", obj.get("LawName"))
            continue
        if pcodes is not None and pcode not in pcodes:
            continue
        laws.append(parse_moj_law(obj, pcode=pcode, update_date=update))
    return laws


def parse_moj_law(obj: dict, *, pcode: str, update_date: str | None) -> RawLaw:
    modified_raw = str(obj.get("LawModifiedDate") or "")
    modified, _ = parse_moj_date(modified_raw)
    if modified is None:
        raise SourceError(f"{pcode} has no usable LawModifiedDate ({modified_raw!r})")
    effective, effective_raw = parse_moj_date(str(obj.get("LawEffectiveDate") or ""))
    attachments = []
    raw_attachments = obj.get("LawAttachements")
    if raw_attachments is None:
        raw_attachments = obj.get("LawAttachments") or []
    for item in raw_attachments:
        if not isinstance(item, dict):
            continue
        file_url = str(item.get("FileURL") or "").strip()
        if not file_url:
            continue
        attachments.append(
            RawAttachment(file_name=str(item.get("FileName") or "").strip(), file_url=file_url)
        )
    articles = []
    for item in obj.get("LawArticles") or []:
        if not isinstance(item, dict):
            continue
        articles.append(
            RawArticle(
                article_type=str(item.get("ArticleType") or "").strip(),
                article_no=str(item.get("ArticleNo") or "").strip(),
                content=_newlines(str(item.get("ArticleContent") or "")),
            )
        )
    url = str(obj.get("LawURL") or f"https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode={pcode}")
    return RawLaw(
        pcode=pcode,
        name=str(obj.get("LawName") or pcode).strip(),
        law_level=str(obj.get("LawLevel") or "").strip(),
        official_url=url,
        category=str(obj.get("LawCategory") or "").strip(),
        modified_date=modified,
        modified_date_raw=modified_raw,
        effective_date=effective,
        effective_date_raw=effective_raw,
        effective_note=_newlines(str(obj.get("LawEffectiveNote") or "")).strip(),
        histories=_newlines(str(obj.get("LawHistories") or "")).strip(),
        foreword=_newlines(str(obj.get("LawForeword") or "")).strip(),
        attachments=attachments,
        articles=articles,
        source="moj_api",
        source_update_date=update_date or None,
    )


def _newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")
