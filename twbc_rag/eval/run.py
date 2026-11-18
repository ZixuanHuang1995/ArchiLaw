"""Recall@5 and MRR for BM25, dense, and hybrid retrieval.

Gold ids are annotated in questions.json. Expansion hits are not counted.
"""

from __future__ import annotations

import json
from pathlib import Path

from twbc_rag.rag.chunks import chunk_id_for
from twbc_rag.rag.engine import get_engine

QUESTIONS = Path(__file__).with_name("questions.json")
MODES = ("bm25", "dense", "hybrid")


def recall_at_k(retrieved: list[str], gold: set[str], k: int = 5) -> float:
    if not gold:
        return 0.0
    return len(set(retrieved[:k]) & gold) / len(gold)


def mrr(retrieved: list[str], gold: set[str]) -> float:
    for rank, doc_id in enumerate(retrieved, start=1):
        if doc_id in gold:
            return 1.0 / rank
    return 0.0


def evaluate(modes: tuple[str, ...] = MODES) -> dict:
    payload = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    engine = get_engine()
    engine.ensure_ready()
    per_question = []
    totals = {mode: {"recall@5": 0.0, "mrr": 0.0} for mode in modes}
    for item in payload["questions"]:
        gold = {chunk_id_for(entry["pcode"], entry["main"], entry["sub"]) for entry in item["gold"]}
        row: dict = {"id": item["id"], "gold": sorted(gold), "modes": {}}
        for mode in modes:
            result = engine.search(item["question"], mode=mode, top_k=5)
            retrieved = [hit.chunk.chunk_id for hit in result.hits]
            scores = {"recall@5": recall_at_k(retrieved, gold), "mrr": mrr(retrieved, gold)}
            row["modes"][mode] = {**scores, "retrieved": retrieved}
            totals[mode]["recall@5"] += scores["recall@5"]
            totals[mode]["mrr"] += scores["mrr"]
        per_question.append(row)
    count = len(payload["questions"])
    summary = {
        mode: {
            "recall@5": round(totals[mode]["recall@5"] / count, 4),
            "mrr": round(totals[mode]["mrr"] / count, 4),
        }
        for mode in modes
    }
    return {
        "source": payload["source"],
        "annotation": payload["annotation"],
        "n": count,
        "summary": summary,
        "questions": per_question,
    }


def format_table(report: dict) -> str:
    modes = list(report["summary"])
    header = "| mode | Recall@5 | MRR |"
    sep = "| --- | ---: | ---: |"
    lines = [header, sep]
    for mode in modes:
        row = report["summary"][mode]
        lines.append(f"| {mode} | {row['recall@5']:.3f} | {row['mrr']:.3f} |")
    return "\n".join(lines)
