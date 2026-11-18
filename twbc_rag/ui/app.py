"""Streamlit demo. Talks only to the FastAPI /query endpoint."""

from __future__ import annotations

import os

import httpx
import streamlit as st

API_URL = os.environ.get("TWBC_API_URL", "http://127.0.0.1:8077").rstrip("/")
DISCLAIMER = (
    "這不是法律意見。條文以全國法規資料庫公布的版本為準；模型答案若與條文不符，以條文為準。"
)


def _render_hits(hits: list[dict], *, expanded: bool) -> None:
    for hit in hits:
        title = f"{hit['law_name']} {hit['article_no']}"
        if hit.get("has_proviso"):
            title += " · 含但書"
        if hit.get("has_table"):
            title += " · 含表格"
        channels = "、".join(hit.get("channels") or [])
        with st.expander(f"{title}  ({channels})", expanded=expanded):
            meta = f"修正 {hit.get('modified_date') or '—'}"
            if hit.get("effective_date"):
                meta += f" · 施行 {hit['effective_date']}"
            else:
                meta += " · 無單一施行日期"
            st.caption(meta)
            if hit.get("heading_path"):
                st.caption(hit["heading_path"])
            if hit.get("effective_note"):
                st.info(hit["effective_note"])
            matched = set(hit.get("matched_clause_paths") or [])
            for clause in hit.get("clauses") or []:
                prefix = "但書 " if clause["is_proviso"] else ""
                mark = "▸ " if clause["clause_path"] in matched else ""
                st.markdown(f"{mark}**{prefix}{clause['clause_path']}** {clause['text']}")
            links = []
            if hit.get("official_url"):
                links.append(f"[全國法規資料庫條文]({hit['official_url']})")
            for attachment in hit.get("attachments") or []:
                label = attachment.get("file_name") or "附件"
                if attachment.get("is_fulltext_pdf"):
                    label = f"完整條文 PDF（表格無法以純文字呈現）：{label}"
                links.append(f"[{label}]({attachment['source_url']})")
            if links:
                st.markdown(" · ".join(links))


st.set_page_config(page_title="台灣建築法規 RAG", layout="wide")
st.markdown(
    """
    <style>
      .disclaimer {
        background:#7f1d1d; color:#fff; padding:0.6rem 0.9rem;
        border-radius:6px; margin-bottom:0.8rem;
      }
      .cite {
        background:#ecfdf5; border-left:4px solid #047857;
        padding:0.35rem 0.6rem; margin:0.2rem 0;
      }
    </style>
    """,
    unsafe_allow_html=True,
)
st.markdown(f'<div class="disclaimer">{DISCLAIMER}</div>', unsafe_allow_html=True)
st.title("台灣建築法規 RAG")
st.caption(
    "建築法、建築技術規則四編、各類場所消防安全設備設置標準。檢索結果附條號，生成答案的引用會跟檢索證據核對。"
)

with st.sidebar:
    st.header("檢索")
    mode = st.radio(
        "模式",
        options=["hybrid", "bm25", "dense", "hybrid_rerank"],
        format_func=lambda value: {
            "hybrid": "混合（條號 + BM25 + 向量，RRF）",
            "bm25": "只用 BM25",
            "dense": "只用向量",
            "hybrid_rerank": "混合 + reranker",
        }[value],
    )
    top_k = st.slider("證據條數", min_value=3, max_value=8, value=5)
    st.caption("Reranker 是 BAAI/bge-reranker-v2-m3，第一次會下載，CPU 上較慢。")

if "question" not in st.session_state:
    st.session_state.question = "設計施工編第110-1條對非防火構造建築物的防火間隔怎麼規定？"

try:
    health = httpx.get(f"{API_URL}/health", timeout=5).json()
except Exception as exc:
    st.error(f"API 還沒有就緒（{API_URL}）：{exc}")
    st.stop()

if not health.get("ready"):
    st.warning("索引還在建立（第一次會下載嵌入模型並寫入 Qdrant）。稍後重新整理。")
    st.stop()

llm = health.get("llm") or {}
if llm.get("configured"):
    st.info(f"生成：{llm.get('provider')} / {llm.get('model')}")
else:
    st.info("目前是檢索模式：沒有設定 Anthropic 或 OpenAI 相容的 API 金鑰，只顯示條文證據。")

examples = [
    "設計施工編第110-1條對非防火構造建築物的防火間隔怎麼規定？",
    "私設通路可以計入法定空地嗎？和道路交叉要不要截角？",
    "台北市房屋稅的稅率是多少？",
]
cols = st.columns(len(examples))
for column, example in zip(cols, examples, strict=True):
    if column.button(example, use_container_width=True):
        st.session_state.question = example

question = st.text_area("問題", key="question", height=80)
if st.button("查詢", type="primary") and question.strip():
    with st.spinner("檢索中…"):
        response = httpx.post(
            f"{API_URL}/query",
            json={"question": question.strip(), "mode": mode, "top_k": top_k},
            timeout=180,
        )
        response.raise_for_status()
        payload = response.json()
    st.session_state["last"] = payload

payload = st.session_state.get("last")
if not payload:
    st.stop()

st.subheader("回答")
if payload.get("refused"):
    st.error(payload.get("answer") or "")
elif payload.get("retrieval_only"):
    st.warning(payload.get("answer") or "")
else:
    st.write(payload.get("answer") or "")

if payload.get("citations"):
    st.markdown("**保留的引用**")
    for citation in payload["citations"]:
        st.markdown(f'<div class="cite">【{citation}】</div>', unsafe_allow_html=True)
if payload.get("dropped_citations"):
    st.markdown("**已剔除（不在檢索證據裡）**")
    for citation in payload["dropped_citations"]:
        st.markdown(f"- {citation}")

timings = payload.get("timings_ms") or {}
timing_text = "  ".join(f"{key} {value} ms" for key, value in timings.items())
st.caption(f"模式 {payload.get('mode')} · {timing_text}")
if payload.get("rerank_warning"):
    st.caption(payload["rerank_warning"])

st.subheader("證據")
_render_hits(payload.get("evidence") or [], expanded=True)
if payload.get("expansions"):
    st.subheader("交叉引用擴展（1-hop，不是直接檢索排名）")
    _render_hits(payload["expansions"], expanded=False)
