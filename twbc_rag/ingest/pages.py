"""Attachment links on LawAll.aspx.

The bulk JSON already lists ``LawAttachements``. The HTML page repeats those
links and, for articles whose table cannot be rendered, adds an inline
「完整條文」 anchor inside ``div.text-danger``. Merging the two keeps the
filename from the API and the article placement / warning from the page.
Week 1 stores the URL only.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html import unescape

from bs4 import BeautifulSoup

from twbc_rag.ingest.http_client import download_bytes
from twbc_rag.ingest.types import SourceError

logger = logging.getLogger(__name__)

_FILE_ID = re.compile(r"FileId=(\d+)")
_ARTICLE_IN_NAME = re.compile(r"第\s*(\d+)\s*(?:-\s*(\d+))?\s*條")
_FLNO = re.compile(r"flno=(\d+)(?:-(\d+))?")
_PAGE = "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode={pcode}"


@dataclass
class PageAsset:
    file_name: str
    source_url: str
    article_main: int | None = None
    article_sub: int | None = None
    warning: str | None = None

    @property
    def file_id(self) -> str | None:
        return file_id_of(self.source_url)


def file_id_of(url: str) -> str | None:
    match = _FILE_ID.search(url or "")
    return match.group(1) if match else None


def canonical_file_url(href: str) -> str:
    href = unescape(href).strip()
    file_id = file_id_of(href)
    if file_id:
        return f"https://law.moj.gov.tw/LawClass/LawGetFile.ashx?FileId={file_id}"
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return "https://law.moj.gov.tw/LawClass/" + href.lstrip("/")


def media_type_of(file_name: str, url: str) -> str:
    source = file_name or url
    if "." not in source:
        return "other"
    ext = source.rsplit(".", 1)[-1].lower().split("?")[0]
    if ext in {"pdf", "jpg", "jpeg", "png", "gif", "doc", "docx", "tif", "tiff", "odt"}:
        return ext
    return "other"


def parse_law_page(html: str) -> list[PageAsset]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, PageAsset] = {}
    order: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"])
        if "LawGetFile" not in href:
            continue
        url = canonical_file_url(href)
        key = file_id_of(url) or url
        name = " ".join(anchor.get_text(" ", strip=True).split())
        warning = None
        main: int | None = None
        sub: int | None = None
        row = anchor.find_parent("div", class_="row")
        if row is not None:
            danger = row.find(class_="text-danger")
            if danger is not None:
                warning = " ".join(danger.get_text(" ", strip=True).split())
            flno_anchor = row.find("a", href=_FLNO)
            if flno_anchor is not None:
                flno = _FLNO.search(str(flno_anchor.get("href") or ""))
                if flno:
                    main = int(flno.group(1))
                    sub = int(flno.group(2) or 0)
        if main is None:
            named = _ARTICLE_IN_NAME.search(name)
            if named:
                main = int(named.group(1))
                sub = int(named.group(2) or 0)
        if key in found:
            current = found[key]
            if warning and not current.warning:
                current.warning = warning
            if main is not None and current.article_main is None:
                current.article_main = main
                current.article_sub = sub
            if name and name not in {"完整條文", ""} and current.file_name in {"", "完整條文"}:
                current.file_name = name
            continue
        found[key] = PageAsset(
            file_name=name,
            source_url=url,
            article_main=main,
            article_sub=sub if main is not None else None,
            warning=warning,
        )
        order.append(key)
    return [found[key] for key in order]


def fetch_law_page(pcode: str) -> list[PageAsset]:
    url = _PAGE.format(pcode=pcode)
    try:
        raw = download_bytes(url, timeout=60.0)
    except SourceError as exc:
        logger.warning("Law page %s failed: %s", pcode, exc)
        return []
    html = raw.decode("utf-8", errors="replace")
    assets = parse_law_page(html)
    logger.info("Law page %s: %s attachment link(s)", pcode, len(assets))
    return assets
