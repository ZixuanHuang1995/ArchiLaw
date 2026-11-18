"""Turn a retrieval result into an answer, or stay retrieval-only."""

from __future__ import annotations

import time

from twbc_rag.rag.citations import REFUSAL, check_citations
from twbc_rag.rag.engine import Hit, SearchResult, get_engine
from twbc_rag.rag.generate import generate_answer, llm_status
from twbc_rag.rag.tokenize import tokenize

_QUERY_STOP = {
    "請問",
    "根據",
    "規定",
    "什麼",
    "多少",
    "是否",
    "如何",
    "可以",
    "如果",
    "對於",
    "關於",
    "這個",
    "那個",
    "怎麼",
    "怎樣",
    "有何",
    "主要",
    "哪些",
    "一個",
    "以及",
    "還是",
    "是不是",
}

DISCLAIMER = "這不是法律意見。條文以全國法規資料庫公布的版本為準，施行日期可能因條而異。"
RETRIEVAL_NOTE = "未設定語言模型金鑰，以下只顯示檢索到的條文，不是生成答案，也不是法律意見。"


def answer_question(question: str, *, mode: str = "hybrid", top_k: int = 5) -> dict:
    started = time.perf_counter()
    result = get_engine().search(question, mode=mode, top_k=top_k)
    if result.hits and not _evidence_covers_question(question, result):
        result = SearchResult(
            hits=[],
            expansions=[],
            timings_ms=result.timings_ms,
            rerank_warning=result.rerank_warning,
        )
    status = llm_status()
    evidence = [hit.chunk for hit in result.hits]
    retrieval_only = not status["configured"]
    refused = False
    dropped: list[str] = []
    citations: list[str] = []
    if not evidence:
        answer = REFUSAL
        refused = True
    elif retrieval_only:
        answer = RETRIEVAL_NOTE
    else:
        try:
            raw = generate_answer(question, evidence)
        except Exception as exc:
            answer = f"{RETRIEVAL_NOTE}\n（語言模型呼叫失敗：{exc}）"
            retrieval_only = True
        else:
            answer, citations, dropped = check_citations(raw, evidence)
            if not citations:
                answer = REFUSAL
                refused = True
            elif REFUSAL in answer:
                refused = True
    result.timings_ms["generate"] = round((time.perf_counter() - started) * 1000, 1)
    result.timings_ms["total"] = result.timings_ms["generate"]
    return {
        "question": question,
        "mode": mode,
        "answer": answer,
        "refused": refused,
        "retrieval_only": retrieval_only,
        "citations": citations,
        "dropped_citations": dropped,
        "evidence": [_hit_dict(hit) for hit in result.hits],
        "expansions": [_hit_dict(hit) for hit in result.expansions],
        "timings_ms": result.timings_ms,
        "rerank_warning": result.rerank_warning,
        "llm": status,
        "disclaimer": DISCLAIMER,
    }


def _evidence_covers_question(question: str, result: SearchResult) -> bool:
    """Drop a hit list that never mentions the question's content words.

    Article-number lookup is trusted. Otherwise at least one content token
    (房屋稅, 私設通路, …) must appear in the top evidence, or the answer is a refusal.
    """
    if any("lookup" in hit.channels for hit in result.hits):
        return True
    tokens = [token for token in tokenize(question) if token not in _QUERY_STOP and len(token) >= 2]
    if not tokens:
        return True
    blob = "\n".join(hit.chunk.full_text for hit in result.hits[:5])
    return any(token in blob for token in tokens)


def _hit_dict(hit: Hit) -> dict:
    chunk = hit.chunk
    return {
        "chunk_id": chunk.chunk_id,
        "pcode": chunk.pcode,
        "law_name": chunk.law_name,
        "heading_path": chunk.heading_path,
        "article_no": chunk.article_no,
        "article_no_main": chunk.article_no_main,
        "article_no_sub": chunk.article_no_sub,
        "clause_paths": [child.clause_path for child in chunk.clauses],
        "matched_clause_paths": hit.matched_clause_paths,
        "has_proviso": chunk.has_proviso,
        "has_table": chunk.has_table,
        "full_text": chunk.full_text,
        "clauses": [
            {
                "clause_path": child.clause_path,
                "text": child.text,
                "is_proviso": child.is_proviso,
            }
            for child in chunk.clauses
        ],
        "version_id": chunk.version_id,
        "modified_date": chunk.modified_date,
        "effective_date": chunk.effective_date,
        "effective_note": chunk.effective_note,
        "official_url": chunk.official_url,
        "attachments": [
            {
                "file_name": item.file_name,
                "source_url": item.source_url,
                "media_type": item.media_type,
                "is_fulltext_pdf": item.is_fulltext_pdf,
            }
            for item in chunk.attachments
        ],
        "score": hit.score,
        "channels": hit.channels,
        "expansion": hit.expansion,
        "citation": chunk.citation_label(),
    }
