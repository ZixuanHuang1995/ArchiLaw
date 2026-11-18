# twbc-rag

Taiwan building-code corpus for a citation-first RAG system.
台灣建築法規語料庫，用來做「回答必須引用條文」的 RAG。

Week 1 loads six central statutes from the Ministry of Justice Laws & Regulations Database (全國法規資料庫) into PostgreSQL. Later weeks add figures, hybrid retrieval, a cited answer demo, and an eval set. This is a learning and portfolio project. It is not legal advice.

## English

### Goal

Answer questions about Taiwanese building law with a mandatory citation to the article (and, when it matters, the 項 / 款 / 目 or 但書) the sentence came from. The corpus has to preserve structure the official plain text makes easy to lose: chapter hierarchy, paragraphs, provisos, cross-references, and the URLs of figures the API cannot draw.

The default corpus is config-driven (`config/laws.yaml`):

| pcode | instrument | kind |
| --- | --- | --- |
| D0070109 | 建築法 Building Act | law |
| D0070114 | 建築技術規則總則編 | order |
| D0070115 | 建築技術規則建築設計施工編 | order |
| D0070116 | 建築技術規則建築構造編 | order |
| D0070117 | 建築技術規則建築設備編 | order |
| D0120029 | 各類場所消防安全設備設置標準 | order |

Add another statute by appending a pcode. Aliases are the names citations actually use (`設計施工編`, `總則編`). `parent_act_*` is what 「本法」 means inside that instrument. Inside the building technical rules, 本法 is the Building Act. Inside the fire standard, 本法 is the Fire Services Act (消防法), which is not in this corpus yet.

### Architecture

```mermaid
flowchart LR
  subgraph sources [Sources]
    MOJ["MOJ bulk JSON zip<br/>/api/ch/law/json and /api/ch/order/json"]
    HF["Fallback: Hugging Face<br/>lianghsun/tw-law"]
    PAGE["LawAll.aspx<br/>attachment URLs only"]
  end
  CLI["twbc ingest"]
  DAG["Airflow DAG or cron<br/>dags/twbc_weekly_law_sync.py"]
  PARSE["編/章/節, 項/款/目, 但書,<br/>Chinese numerals, cross-refs"]
  PG[("PostgreSQL")]
  JSONL["data/processed<br/>articles.jsonl + clauses.jsonl"]
  DAG --> CLI
  MOJ --> CLI
  HF --> CLI
  PAGE --> CLI
  CLI --> PARSE
  PARSE --> PG
  PARSE --> JSONL
```

Qdrant, the API, and the UI are commented in `docker-compose.yml` for later weeks. Week 1 is Postgres plus a CLI.

### Setup

Requires Python 3.11+ (developed on 3.12), [uv](https://docs.astral.sh/uv/), and Docker.

```bash
cp .env.example .env
docker compose up -d
uv sync --extra dev
uv run alembic upgrade head
uv run pytest
uv run ruff check .
```

Postgres is published on **host port 55432** so it does not collide with a local server on 5432.

```
postgresql+psycopg://twbc:twbc@localhost:55432/twbc
```

`docker/postgres/init.sql` also creates `twbc_test` for pytest. Tests replay a saved slice of the real API payload and do not call the network.

### Ingestion

```bash
uv run twbc ingest
# the same entry point:
python -m twbc_rag ingest
python -m twbc_rag.ingest --pcodes D0070109,D0070115
```

What one run does:

1. Download the two MOJ zip files (Chinese laws, Chinese orders). They are cached under `data/raw/`. `--refresh` forces a new download. If a zip fails (TLS, HTTP 5xx, rate limit), the loader retries, then retries once without certificate verification, then falls back to the Hugging Face JSONL for any pcode still missing.
2. Keep only the pcodes in `config/laws.yaml` (or `--pcodes`).
3. Fetch each `LawAll.aspx` page and merge `LawGetFile.ashx` links with the API's `LawAttachements` list. URLs are stored. Files are not downloaded.
4. Insert a `law_version` keyed by `(pcode, modified_date)`. The same date on a second run is a no-op. A new `LawModifiedDate` inserts another version and leaves the old one. `--force` reparses the current date in place.
5. Rebuild cross-references for every stored version when at least one version was inserted, so a newly added law can satisfy cites that were unresolved. Targets point at the **latest** version of the cited law.
6. Write `data/processed/articles.jsonl`, `clauses.jsonl`, and `ingest_report.json`.

`uv run twbc stats` prints the same counts from the database.

Weekly refresh without Airflow:

```cron
0 18 * * 5  cd /opt/twbc-rag && uv run twbc ingest --refresh
```

`dags/twbc_weekly_law_sync.py` calls `ingest_configured(refresh=True)`. Airflow 2.x is not installed in this repo and is not required for tests. The DAG is skipped cleanly when Airflow is absent.

### Data model

```mermaid
erDiagram
  law ||--o{ law_version : has
  law_version ||--o{ article : contains
  article ||--o{ clause : splits
  clause ||--o{ cross_ref : cites
  article ||--o{ cross_ref : "to_article"
  clause ||--o{ cross_ref : "to_clause"
  law_version ||--o{ figure_table : attaches
  article ||--o{ figure_table : illustrates
  interpretation ||--o{ interpretation_article : links
  article ||--o{ interpretation_article : cited_by
```

`interpretation` and `interpretation_article` are created empty for Week 2 (國土署 / 營建署函釋).

Adjustments relative to the sketch ERD, and why:

- **No `pcode` field in the MOJ JSON.** It is the `pcode=` query parameter on `LawURL`.
- **`law_version.effective_date_raw`.** The API writes `99991231` when there is no single effective date. That sentinel is not stored as a date. The human note stays in `effective_note`. `histories` and `foreword` are kept so a later chunker can show amendment context. `content_hash` detects a silent text change that did not bump `LawModifiedDate` (we warn and do not overwrite; `--force` reparses).
- **`article.article_no_main` / `article_no_sub`.** `第 110-1 條`, `第一百十條之一`, and `第一一四條` all need one integer key. Sub-number `0` means there is no 之一.
- **`clause.clause_path`.** Slash-separated integers: `1` is 第1項, `1/2` is 第1項第2款, `1/9/3` is 第1項第9款第3目. A 但書 keeps its parent path and sets `is_proviso`, so the proviso can be cited without losing which 款 it qualifies.
- **`cross_ref.to_clause_id`.** 「前項」「前款」「第N款」 are not articles. `to_article_id` is set whenever the article is known. A row with a null `to_article_id` is unresolved. `target_law_name` keeps the name we parsed (`消防法`, `都市計畫法`) when that statute is not loaded.
- **`figure_table.version_id`, nullable `article_id`.** Some files are not tied to one article. `file_name` and `source_kind` (`api`, `page`, `api+page`) record where the URL came from. `storage_uri` and `caption_or_ocr` stay null except for the filename and, when the HTML says so, the "layout cannot be rendered" warning. Week 2 fills the file and the OCR.

Parsing rules that matter:

- 項 breaks on non-empty lines. A line starting with `一、` / `十一、` is a 款. A line starting with `（一）` is a 目.
- Box-drawing lines (`┌ │ └`) stay inside the clause they follow. Table cells contain `（一）` and must not become 目.
- A sentence that starts after `。` or `；` with `但` is a proviso clause.
- Numerals: `十一` = 11, `一百十四` = 114, and the digit style MOJ uses above 100, `一一三` = 113, `一○一` = 101 (the zero is U+25CB).
- 「本法」 follows an in-text `（以下簡稱本法）` when the instrument defines it, otherwise the catalog parent. 「本編」 is the current pcode. Named aliases such as `建築設計施工編` resolve across the six books.

### Data sources and licensing

Primary source, verified live on 2026-11-18:

- `https://law.moj.gov.tw/api/ch/law/json`
- `https://law.moj.gov.tw/api/ch/order/json`

Each URL returns a zip with one JSON document, `{"UpdateDate", "Laws"}`. Both files in this run carried `UpdateDate` `2026/11/6 上午 12:00:00`. The official schema spells the attachment array `LawAttachements`.

Fallback: [lianghsun/tw-law](https://huggingface.co/datasets/lianghsun/tw-law), a weekly JSONL mirror. It works (a real constitution record is in `tests/fixtures/hf_sample.jsonl`), but the dataset card we checked was still on UpdateDate 2026-04-24, `ch_law_articles` alone is about 173 MB, and chapter-only rows are dropped. Use it when the MOJ zip cannot be fetched. Prefer the MOJ zip when both succeed.

Per-statute HTML `https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=` repeats the same `LawGetFile` ids as the API and adds the sentence 「因條文排版無法完整呈現內容」 next to tables. This run stores that warning on the attachment row.

**Copyright.** Copyright Act (著作權法) Article 9, paragraph 1: the constitution, statutes, orders, and official documents are not subject matter of copyright. These six instruments are statutes or orders. Attribute the text to the Ministry of Justice Laws & Regulations Database: <https://law.moj.gov.tw/>. The application code in this repository is MIT. Do not treat a model answer as an official consolidation, especially for articles whose figures live only in the PDF.

### Disclaimer

This project is for study and for demonstrating an LLM engineering workflow. It is not a legal opinion, not a substitute for the consolidated text on law.moj.gov.tw, and not a substitute for a licensed architect or attorney. Effective dates can differ by article. When the HTML says the article cannot be rendered as text, the database is incomplete on purpose until Week 2.

### Week 1 snapshot

Ingested 2026-11-18 from the MOJ zip (`UpdateDate` 2026-11-06). A second `twbc ingest` skipped all six pcodes. Counts are the latest version of each law.

| pcode | articles | clauses | provisos | cross-refs resolved | unresolved | attachment URLs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| D0070109 建築法 | 123 | 360 | 15 | 130 | 1 | 0 |
| D0070114 總則編 | 12 | 39 | 6 | 15 | 0 | 0 |
| D0070115 設計施工編 | 389 | 1658 | 137 | 371 | 2 | 53 |
| D0070116 構造編 | 622 | 941 | 17 | 50 | 1 | 1 |
| D0070117 設備編 | 159 | 536 | 9 | 55 | 2 | 0 |
| D0120029 消防安全設備設置標準 | 266 | 1728 | 171 | 540 | 1 | 20 |
| **Total** | **1571** | **5262** | **355** | **1161** | **7** | **74** |

Resolved means `cross_ref.to_article_id` is not null. JSONL line counts match: 1571 articles, 5262 clauses.

Attachment URLs by kind:

- 設計施工編: 34 PDF, 18 DOC, 1 JPG. Twenty-four PDFs plus the DOC/JPG files are 補充圖例. Ten PDFs are `完整條文` files whose HTML says the table cannot be rendered (for example 第 116-2 條, 第 170 條, 第 312 條).
- 構造編: one PDF, 第 17 條完整條文.
- 消防標準: 20 PDFs, all `完整條文`, and all 20 carry the layout warning.
- 建築法 and 總則編 and 設備編: no files. The live law page returned the same FileId set as the API for every pcode in this run.

Other facts worth keeping:

- **478 articles are only `（刪除）`**, 369 of them in 構造編. They stay in the database so a cite to that number still resolves. A later chunker should not embed them as normative text.
- **68 articles contain box-drawing tables.** The characters are kept. Column alignment is not a real table.
- **設計施工編 `LawEffectiveDate` is `99991231`.** The note says (1) article 301, as amended on 93-03-10 (2004-03-10), still has its effective date marked 另定, and (2) the 115-02-23 (2026-02-23) amendments to articles 140–142 and 144, plus the deleted section titles in chapter 6, took effect on 115-07-01 (2026-07-01). As of this ingest date that July date has passed; article 301's date has not been fixed. The whole-law effective date is therefore null, and the note is stored verbatim.
- 構造編's 112-05-10 (2023-05-10) amendment took effect 113-01-01 (2024-01-01). The fire standard's 113-04-24 amendment took effect 113-07-01 (2024-07-01). Both dates are stored.
- Cross-book cites that resolved include 總則編第1條 → 建築法第97條, 設計施工編第1條 → 建築法第11條, and 設備編第42條 → 設計施工編第114條 (`第一一四條`).
- The seven unresolved spans are outside the corpus or ambiguous: `不受土地法第二十五條`, `不包括都市計畫法第三十二條`, `山坡地保育利用條例第三條`, `地質法第八條第一項`, `消防法…第六條`, and in 設備編第138-1條 the phrases `同編第一百八十二條` and `第二百五十九條`. That `同編` points back at 設計施工編, which was named earlier in the same sentence, not at 設備編. Week 1 does not do that anaphora. The leading words `不受` / `不包括` are also still stuck on two external titles; the cite is unresolved either way.

### Demo

`docker compose up -d --build` starts Postgres, Qdrant, the FastAPI service, and Streamlit.

| service | URL |
| --- | --- |
| Streamlit UI | http://127.0.0.1:8571 |
| API `POST /query` | http://127.0.0.1:8077/query |
| Qdrant | http://127.0.0.1:6333 |
| Postgres | `localhost:55432` |

The first API start loads `intfloat/multilingual-e5-small` and, if the Qdrant collection is empty, embeds the clause index (about 1,093 articles and 4,784 clauses after dropping `（刪除）`). A collection that already has 4,784 vectors is reused.

Without `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` / `OPENAI_BASE_URL`, `/query` stays in retrieval-only mode and still returns ranked evidence. Generation, when a key is set, asks for citations shaped like 【建築技術規則建築設計施工編 第55條第1項】 and drops any citation that is not in the retrieved articles. If the evidence does not contain the question's content words, the API refuses.

```bash
# local Ollama
LLM_PROVIDER=ollama OPENAI_BASE_URL=http://host.docker.internal:11434/v1 \
  OPENAI_API_KEY=ollama OPENAI_MODEL=qwen2.5 docker compose up -d --build
```

Dense search uses **multilingual-e5-small**, not BGE-M3. This machine has 4 CPUs and roughly 5–8 GB free. BGE-M3 is about 2.3 GB of weights; stacking it with `bge-reranker-v2-m3` does not leave room for Postgres, Qdrant, and the API. E5-small is 384-dimensional and uses the required `query:` / `passage:` prefixes. The reranker is implemented as mode `hybrid_rerank` and was **not** scored: the extra model was not loaded in this eval.

Retrieval is parent-child. BM25 (jieba plus `config/jieba_building.txt`) and the dense index run on clauses, then scores roll up to the article. Hybrid mode also runs article-number lookup (`設計施工編第110-1條`, Chinese numerals) and fuses the three lists with RRF (k=60). Cross-references from the top articles are returned separately and marked `expansion`; they are not part of Recall@5.

Screenshots from the VM, retrieval-only (no LLM key):

![Article 110-1 lookup](docs/screenshots/lookup-110-1.png)

![Private road and proviso evidence](docs/screenshots/private-road.png)

![House-tax question refused](docs/screenshots/out-of-scope-refusal.png)

### Retrieval eval

Fifteen questions from [YuJunWang/taiwan-building-code-rag-benchmark](https://github.com/YuJunWang/taiwan-building-code-rag-benchmark) `docs/qa_reference.md` (MIT). That file publishes answers, not article ids. Gold ids in `twbc_rag/eval/questions.json` are this project's annotation against the ingested MOJ text. Recall@5 is the fraction of those gold articles inside the top 5. MRR uses the rank of the first gold hit. Expansion hits are excluded. Run: `uv run twbc eval` on 2026-11-18.

| mode | Recall@5 | MRR |
| --- | ---: | ---: |
| bm25 | 0.601 | 0.652 |
| dense | 0.729 | 0.719 |
| hybrid | 0.851 | 0.756 |

The open summary question (Q11, high-rise principles) scored 0 on every mode: the annotated articles (96, 97, 107, 181) were not in the top 5; 第259條 was. That is reported as a miss, not relabeled.

### Roadmap

- **Week 2.** Download and parse the 補充圖例 PDFs/JPGs and the 31 `完整條文` PDFs. OCR or table-extract them into `figure_table.caption_or_ocr` and `storage_uri`. Load 國土署 / 內政部營建署函釋 into `interpretation`. Split effective dates down to the article, especially 設計施工編第301條 versus 第140–142、144條.
- **Week 3.** Chunk clauses (skip pure `（刪除）` rows) and hybrid retrieval: BM25 + BGE-M3 + a reranker, plus a direct article-number lookup on `article_no_main` / `article_no_sub`.
- **Week 4.** Generation with a citation check, behind FastAPI and a Streamlit demo.
- **Week 5.** An eval set of about 100 questions: the 15-question [YuJunWang/taiwan-building-code-rag-benchmark](https://huggingface.co/datasets/YuJunWang/taiwan-building-code-rag-benchmark) plus Ministry of Examination 營建法規 items, with an ablation.
- **Week 6.** Polish: error analysis, prompts, and the write-up.

### Tests

```bash
uv run pytest
uv run ruff check .
```

Unit tests cover numeral styles, 項/款/目 splitting, proviso detection, table lines, and cross-reference resolution, using short copied sentences. The integration test loads `tests/fixtures/moj_sample.json` (a real, trimmed MOJ payload: 建築法 articles that 總則編 cites, plus the whole 總則編) into `twbc_test`, checks clause rows against the parser, checks that 總則編第1條 resolves to 建築法第97條, checks that a second run inserts nothing, and checks that a new `modified_date` opens a second version.

## 繁體中文

### 目標

這個專案要回答台灣建築法規問題，而且回答必須引用到條文；必要時要能指到項、款、目或但書。語料本身得保住全國法規資料庫純文字容易弄丟的結構：編章節、項款目、但書、條文互相引用，以及圖表的網址。

預設收這六部，清單在 `config/laws.yaml`，之後加法規只要補一個 pcode。`aliases` 是條文裡真正出現的簡稱（例如「設計施工編」）。`parent_act_*` 決定「本法」指誰：建築技術規則裡的本法是建築法；消防設備設置標準裡的本法是消防法，消防法這輪還沒有收進來。

### 架構

上方英文區的 mermaid 圖同樣適用。Week 1 只有 PostgreSQL 和 CLI。Qdrant、API、介面先在 `docker-compose.yml` 裡留註解。

### 安裝與執行

需要 Python 3.11 以上、uv、Docker。

```bash
cp .env.example .env
docker compose up -d
uv sync --extra dev
uv run alembic upgrade head
uv run twbc ingest
uv run pytest
uv run ruff check .
```

Postgres 對應到本機 **55432** port，避免跟已經佔用 5432 的服務打架。連線字串：

```
postgresql+psycopg://twbc:twbc@localhost:55432/twbc
```

pytest 用的是 `twbc_test`，吃的是存下來的真實 API 片段，不會打外網。

### 入庫

```bash
uv run twbc ingest
python -m twbc_rag.ingest --pcodes D0070109,D0070115
uv run twbc stats
```

流程：

1. 下載法務部兩包 zip（中文法律、中文命令），快取在 `data/raw/`。`--refresh` 會重抓。TLS 或 5xx 會重試，必要時暫時關掉憑證驗證再試一次，還是失敗才改走 Hugging Face。
2. 只留下設定檔（或 `--pcodes`）點名的法規。
3. 讀 `LawAll.aspx`，把頁面上的 `LawGetFile.ashx` 跟 API 的 `LawAttachements` 合併。這輪只存網址，不下載檔案。
4. 以 `(pcode, modified_date)` 當版本。日期沒變就什麼都不寫；日期變了就新增一個 `law_version`，舊版留著。`--force` 會把同一個日期拆掉重解析。
5. 這次有新增版本時，整庫的交叉引用會重算，目標指到被引法規的**最新版本**。
6. 輸出 `data/processed/articles.jsonl`、`clauses.jsonl`、`ingest_report.json`。

沒有 Airflow 時，每週五可以用 cron：

```cron
0 18 * * 5  cd /opt/twbc-rag && uv run twbc ingest --refresh
```

`dags/twbc_weekly_law_sync.py` 呼叫同一個函式。本機沒裝 Airflow 也能匯入這個檔，測試不會因此失敗。

### 資料模型

ER 圖見英文區。`interpretation` 兩張表是空的，留給 Week 2 的函釋。

和最初草圖不一樣的地方：

- MOJ JSON **沒有 pcode 欄位**，pcode 在 `LawURL` 的查詢參數裡。
- `effective_date_raw` 保留 `99991231` 這種「沒有單一施行日期」的哨兵值，不把它寫進 `date`。沿革和前言一起留下。`content_hash` 用來發現「內文變了但修正日期沒變」；預設只警告，不覆寫。
- `article_no_main` / `article_no_sub` 把「第 110-1 條」「第一百十條之一」「第一一四條」收成同一組整數。沒有「之一」時 sub 是 0。
- `clause_path` 用斜線：`1` 是第1項，`1/2` 是第1項第2款，`1/9/3` 是第1項第9款第3目。但書沿用同一條路徑，另外把 `is_proviso` 設為 true。
- `cross_ref.to_clause_id` 用來接「前項」「前款」。找得到條才算 resolved；找不到的條（例如還沒收的消防法）`to_article_id` 是 null，法名留在 `target_law_name`。
- 附件一定掛在 `version_id` 上，`article_id` 可以是空的，避免整部法規的附件被丟掉。`storage_uri` 這輪是空的。

解析規則：項看非空行；行首「一、」「十一、」是款；行首「（一）」是目。表格框線（`┌ │ └`）黏在前一個款上，格子裡的「（一）」不會被當成目。`。但` 或 `；但` 開頭的句子拆成但書。數字同時吃「十一」「一百十四」和法務部在一百條以後常用的逐字寫法「一一三」「一○一」（那個圈是 U+25CB）。「本法」優先採用條文裡的「以下簡稱本法」，沒有定義才用設定檔的母法。

### 資料來源與授權

這次實際打到的來源：

- `https://law.moj.gov.tw/api/ch/law/json`
- `https://law.moj.gov.tw/api/ch/order/json`

兩包的 `UpdateDate` 都是 `2026/11/6 上午 12:00:00`。附件陣列的官方鍵名拼成 `LawAttachements`。

備援是 Hugging Face 的 [lianghsun/tw-law](https://huggingface.co/datasets/lianghsun/tw-law)。格式可以用（測試夾了一筆真正的憲法條文），但我們看到的資料卡仍停在 2026-04-24，而且少了章節列。MOJ 抓得到就用 MOJ。

**著作權。** 著作權法第 9 條第 1 項規定，憲法、法律、命令及公文不得為著作權之標的。這六部是法律或命令。引用時請註明來源：法務部全國法規資料庫 <https://law.moj.gov.tw/>。這個儲存庫裡的程式碼是 MIT。模型產生的答案不是官方條文，尤其是網頁已寫明「排版無法完整呈現」的條，資料庫裡的文字是不完整的。

### 免責

這是學習和作品集用的系統，不是法律意見，也不能取代全國法規資料庫或執業的建築師、律師。同一部法規裡，不同條的施行日期可能不一樣。

### Week 1 實際入庫結果

2026-11-18 從 MOJ zip 入庫（`UpdateDate` 2026-11-06）。再跑一次 `twbc ingest`，六部全部是 skipped。數字是各法最新一個版本。

| pcode | 條文 | 項款目 | 但書 | 引用已對上 | 引用未對上 | 附件網址 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| D0070109 建築法 | 123 | 360 | 15 | 130 | 1 | 0 |
| D0070114 總則編 | 12 | 39 | 6 | 15 | 0 | 0 |
| D0070115 設計施工編 | 389 | 1658 | 137 | 371 | 2 | 53 |
| D0070116 構造編 | 622 | 941 | 17 | 50 | 1 | 1 |
| D0070117 設備編 | 159 | 536 | 9 | 55 | 2 | 0 |
| D0120029 消防安全設備設置標準 | 266 | 1728 | 171 | 540 | 1 | 20 |
| **合計** | **1571** | **5262** | **355** | **1161** | **7** | **74** |

「已對上」是 `to_article_id` 不是 null。JSONL 行數與上表相同：1571 條、5262 個項款目。

附件：

- 設計施工編 53 個：PDF 34、DOC 18、JPG 1。其中 10 個 PDF 是「完整條文」，網頁寫明表格排不出來（例如第 116-2、170、312 條）。其餘多是補充圖例。
- 構造編 1 個：第 17 條完整條文 PDF。
- 消防標準 20 個全是完整條文 PDF，20 個都有排版警告。
- 建築法、總則編、設備編沒有附件。這次六部法規的網頁 FileId 和 API 附件清單一致。

其他資料品質：

- **478 條只寫「（刪除）」**，構造編就佔 369 條。條號留著，之後做 chunk 時不該把它們當規範內容。
- **68 條含有表格框線。** 字元有留，但那不是真正的表格。
- **設計施工編的施行日期是哨兵值 `99991231`。** 註記寫著：第 301 條（九十三年三月十日修正）施行日期另定；一百十五年二月二十三日修正的第 140 至 142、144 條，以及刪除的第六章第一、二節節名，自一百十五年七月一日施行。以這次入庫日（2026-11-18）來看，七月一日已過，第 301 條的日期仍然未定。所以整部法規的 `effective_date` 是空的，註記原文有留。
- 構造編一百十二年五月十日修正，自一百十三年一月一日施行。消防標準一百十三年四月二十四日修正，自一百十三年七月一日施行。
- 跨法規對得上的例子：總則編第 1 條到建築法第 97 條、設計施工編第 1 條到建築法第 11 條、設備編第 42 條到設計施工編第 114 條（原文寫「第一一四條」）。
- 七筆沒對上的是：土地法第 25 條、都市計畫法第 32 條、山坡地保育利用條例第 3 條、地質法第 8 條、消防法第 6 條，以及設備編第 138-1 條裡的「同編第一百八十二條」「第二百五十九條」。這裡的「同編」是沿用同一句前面的設計施工編，不是設備編自己。Week 1 沒有做這種指代。另外「不受」「不包括」有時還黏在外部法名前面，但不影響「未收錄所以 unresolved」的結論。

### Demo

一行啟動 Postgres、Qdrant、FastAPI、Streamlit：

```bash
docker compose up -d --build
```

| 服務 | 位址 |
| --- | --- |
| Streamlit | http://127.0.0.1:8571 |
| API `POST /query` | http://127.0.0.1:8077/query |
| Qdrant | http://127.0.0.1:6333 |
| Postgres | `localhost:55432` |

API 第一次啟動會載入 `intfloat/multilingual-e5-small`。Qdrant 裡若已有 4,784 個向量就直接用，否則會把去掉「（刪除）」之後的約 1,093 條、4,784 個項款目寫進去。

沒有 `ANTHROPIC_API_KEY`，也沒有 `OPENAI_API_KEY` 或 `OPENAI_BASE_URL` 時，介面仍可查詢，只顯示排序後的條文（檢索模式）。有金鑰時，提示詞要求引用寫成【建築技術規則建築設計施工編 第55條第1項】，不在檢索證據裡的引用會被拿掉。問題裡的實詞若完全沒有出現在證據中，API 會拒絕回答。

本機 Ollama 範例：

```bash
LLM_PROVIDER=ollama OPENAI_BASE_URL=http://host.docker.internal:11434/v1 \
  OPENAI_API_KEY=ollama OPENAI_MODEL=qwen2.5 docker compose up -d --build
```

向量模型用 **multilingual-e5-small**，不是 BGE-M3。這台機器 4 個 CPU、剩餘記憶體大約 5 到 8 GB。BGE-M3 權重約 2.3 GB，再加 `bge-reranker-v2-m3` 就放不下 Postgres、Qdrant 和 API。E5-small 是 384 維，查詢和文章分別要加 `query:`、`passage:` 前綴。`hybrid_rerank` 模式有接 reranker，但這次評測沒有載入那個模型，所以沒有 rerank 數字。

檢索是 parent-child：BM25（jieba，詞典在 `config/jieba_building.txt`）和向量都對項款目打分，再彙總到條。混合模式另外做條號查詢（「設計施工編第110-1條」、中文數字），三路用 RRF（k=60）合併。排名靠前的條會沿交叉引用再帶出最多 4 條，標成 expansion，不計入 Recall@5。

下面三張圖是這台 VM 的檢索模式（沒有語言模型金鑰）：

![第110-1條直接查詢](docs/screenshots/lookup-110-1.png)

![私設通路與但書](docs/screenshots/private-road.png)

![房屋稅問題被拒絕](docs/screenshots/out-of-scope-refusal.png)

### 檢索評測

15 題來自 [YuJunWang/taiwan-building-code-rag-benchmark](https://github.com/YuJunWang/taiwan-building-code-rag-benchmark) 的 `docs/qa_reference.md`（MIT）。原檔只有標準答案，沒有條號。`twbc_rag/eval/questions.json` 的 gold 是對照這次入庫的 MOJ 原文標的。Recall@5 是 gold 條文有多少落在前 5 名；MRR 是第一個 gold 的排名倒數。expansion 不算。2026-11-18 執行 `uv run twbc eval`。

| 模式 | Recall@5 | MRR |
| --- | ---: | ---: |
| bm25 | 0.601 | 0.652 |
| dense | 0.729 | 0.719 |
| hybrid | 0.851 | 0.756 |

開放式的 Q11（高層建築物防災原則）三種模式都是 0：標注的第 96、97、107、181 條沒有進前 5，檢索到的是第 259 條。這個 0 照實留下，沒有改標成檢索結果。

### 後續

- **Week 2。** 下載並解析補充圖例與那 31 份完整條文 PDF，把 OCR / 表格寫進 `figure_table`。收國土署、營建署函釋。把施行日期拆到條，特別是設計施工編第 301 條和第 140 至 142、144 條。
- **Week 3。** 以項款目做 chunk（純「（刪除）」不要送去嵌入），混合檢索：BM25 + BGE-M3 + reranker，再加上條號直接查詢。
- **Week 4。** 生成答案並檢查引用，用 FastAPI 和 Streamlit 做 demo。
- **Week 5。** 大約 100 題的評測：YuJunWang/taiwan-building-code-rag-benchmark 的 15 題，加上考選部營建法規試題，並做 ablation。
- **Week 6。** 收尾：錯誤分析、prompt、文件。

### 測試

```bash
uv run pytest
uv run ruff check .
```

單元測試涵蓋中文數字、項款目、但書、表格框線、交叉引用。整合測試把 `tests/fixtures/moj_sample.json`（真實 API 裁過的建築法相關條文，加上整部總則編）寫進 `twbc_test`，核對資料庫裡的項款目和解析器一致、總則編第 1 條對到建築法第 97 條、重跑不會新增版本、修正日期變了才會再開一個版本。
