"""Pluggable answer generation.

Anthropic is used when ANTHROPIC_API_KEY is set. An OpenAI-compatible endpoint
(including local Ollama) is used when OPENAI_API_KEY or OPENAI_BASE_URL is set.
LLM_PROVIDER=anthropic|openai|ollama picks one when both are configured.
With no key, callers stay in retrieval-only mode.
"""

from __future__ import annotations

import os

import httpx

from twbc_rag.rag.chunks import Chunk
from twbc_rag.rag.citations import REFUSAL

_SYSTEM = """你是台灣建築法規問答助理。只根據下方「檢索條文」回答，使用繁體中文。
規則：
1. 每一句涉及法規內容的話，句末加上引用，格式為【法規全名 第N條第M項】。
   法規全名、條號、項次必須出現在檢索條文裡，不可杜撰。
2. 但書、例外、跨條引用要分開寫清楚，不要把但書說成原則。
3. 若檢索條文不足以回答，只輸出這一句，不要補充其他法律常識：
   無法依目前檢索到的條文回答這個問題。
4. 這不是法律意見。不要建議當事人該怎麼訴訟或怎麼送件。"""


def llm_status() -> dict[str, str | bool]:
    provider = selected_provider()
    return {
        "configured": provider is not None,
        "provider": provider or "",
        "model": _model_name(provider),
    }


def selected_provider() -> str | None:
    choice = os.environ.get("LLM_PROVIDER", "").strip().lower()
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_openai = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_BASE_URL"))
    if choice in {"openai", "ollama"} and has_openai:
        return "openai"
    if choice == "anthropic" and has_anthropic:
        return "anthropic"
    if not choice and has_anthropic:
        return "anthropic"
    if not choice and has_openai:
        return "openai"
    return None


def generate_answer(question: str, evidence: list[Chunk]) -> str:
    if not evidence:
        return REFUSAL
    provider = selected_provider()
    if provider is None:
        raise RuntimeError("no LLM configured")
    prompt = _prompt(question, evidence)
    if provider == "anthropic":
        return _anthropic(prompt)
    return _openai(prompt)


def _prompt(question: str, evidence: list[Chunk]) -> str:
    blocks: list[str] = []
    for chunk in evidence[:5]:
        body = "\n".join(
            f"{'[但書] ' if child.is_proviso else ''}{child.clause_path} {child.text}"
            for child in chunk.clauses
        )
        if len(body) > 1800:
            body = body[:1800] + "…"
        note = chunk.effective_note.strip()
        blocks.append(
            "\n".join(
                [
                    f"【法規】{chunk.law_name}",
                    f"【條號】{chunk.article_no}",
                    f"【章節】{chunk.heading_path or '（無）'}",
                    f"【修正日期】{chunk.modified_date}",
                    f"【施行註記】{note or '（無）'}",
                    "【條文】",
                    body,
                ]
            )
        )
    joined = "\n\n".join(blocks)
    return f"{_SYSTEM}\n\n檢索條文：\n{joined}\n\n問題：{question}"


def _model_name(provider: str | None) -> str:
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    if provider == "openai":
        return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    return ""


def _anthropic(prompt: str) -> str:
    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": _model_name("anthropic"),
            "max_tokens": 1200,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    parts = [
        block.get("text", "") for block in payload.get("content", []) if block.get("type") == "text"
    ]
    return "\n".join(parts).strip()


def _openai(prompt: str) -> str:
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    key = os.environ.get("OPENAI_API_KEY", "ollama")
    response = httpx.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
        json={
            "model": _model_name("openai"),
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    return str(payload["choices"][0]["message"]["content"]).strip()
