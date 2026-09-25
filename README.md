# SwasthiQ — EOD Billing & Analytics Agent

SDE intern take-home assignment: a Python REST API that ingests a clinic's daily billing log and produces a deterministic end-of-day (EOD) reconciliation report, basic analytics, and an LLM-grounded narrative summary — plus a React frontend presenting all three (matching the PDF mockups).

## Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLite (stdlib `sqlite3`), ruff |
| LLM | Local **Ollama** (default `llama3.2:3b`), deterministic fallback when unavailable |
| Frontend | React 19, Vite, react-router, Recharts, lucide-react, plain CSS |
| Tests | pytest (97 tests), ESLint, Playwright for visual verification |

## Quick start

```bash
# Backend (seeds the 3 sample days on first boot)
cd backend
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
# → http://127.0.0.1:8000/api/health

# Frontend (proxies /api → :8000; dev server on 5174)
cd frontend
npm install
npm run dev
# → http://localhost:5174/
```

Narrative generation uses **OpenRouter** when `OPENROUTER_API_KEY` is set (recommended — any OpenAI-compatible model), else local **Ollama** (`ollama serve`). With neither available, the API falls back to a deterministic grounded narrative (`source: "fallback"`) — the endpoint never fails closed.

Put secrets in `backend/.env` (gitignored, auto-loaded; real environment variables always win):

```
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=google/gemini-2.0-flash-001
```

### Environment variables

| Var | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `auto` | `auto` (OpenRouter if a key is set, else Ollama), `openrouter`, or `ollama` |
| `OPENROUTER_API_KEY` | — | OpenRouter API key (keep in `backend/.env`) |
| `OPENROUTER_MODEL` | `google/gemini-2.0-flash-001` | Any OpenRouter model slug |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenAI-compatible endpoint override |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `llama3.2:3b` | Ollama chat model |
| `LLM_TIMEOUT_SECONDS` | `90` | Per-request timeout |
| `LLM_MAX_TOKENS` | `400` | Output cap for OpenRouter responses (narratives run ~250) |
| `LLM_MAX_RETRIES` | `1` | Retries on unusable (off-schema/ungrounded) responses |
| `DATABASE_PATH` | `backend/data/billing.db` | SQLite location |
| `SEED_SAMPLE_DATA` | `1` | Seed sample days when DB is empty |
| `ALLOWED_ORIGINS` | `*` | CORS allow-list (comma-separated) |
| `VITE_API_BASE` | `/api` | Frontend API base (set to full Render URL in prod) |

## API contracts

All money is **integer paise**. All routes are under `/api`.

### `GET /health`
`{"status": "ok"}`

### `POST /billing/ingest`
Body: `{ "rows": [...], "date"?: "YYYY-MM-DD", "clinic_id"?: str, "strict"?: bool }`

- **Default (partial):** accept valid rows, reject bad ones with `{index, visit_id, field, error}` per row → `201`.
- **`strict: true`:** all-or-nothing → `400` with the same error shape, nothing stored.
- Empty log (`rows: []`) requires `date` in the body.
- Atomic UPSERT per date: re-ingesting a day replaces it entirely.
- `date` is resolved from row timestamps when omitted.

Response: `{date, clinic_id, accepted, rejected, strict, errors[], message}`

**Row validation rules** (each produces a field-level error, not a generic 400):

- Required fields: `clinic_id`, `visit_id`, `timestamp`, `doctor_id`, `line_items`, `payment_mode`, `amount_paid_paise`, `discount_paise`, `is_refund`. `doctor_id` is required at ingest for parity with the mockup's row shape, but it is never used in report or narrative outputs.
- `amount_paid_paise` must be `>= 0` on a sale and negative on a refund.
- `discount_paise` must be `<= gross line-item total`.
- `amount_paid_paise` must be `<= billed` (`line items − discount`) on a sale — overpayment is rejected with an actionable error so `outstanding = billed − collected` can never go negative.

### `GET /billing/days`
`{days: [{date, clinic_id, rows, rows_rejected, ingested_at}, ...]}` (newest first)

### `GET /billing/day/{date}`
Stored day (rows + ingest errors) or `404`.

### `GET /reports/eod/{date}`
The deterministic EOD report for an ingested day (or `404`):

```jsonc
{
  "meta": { "clinic_id", "clinic_name", "clinic_subtitle", "date", "generated_at",
            "rows_ingested", "rows_rejected", "data_quality_warnings": [...] },
  "reconciliation": {
    "total_billed_paise", "total_collected_paise", "total_outstanding_paise",
    "total_discount_paise", "total_refunded_paise", "net_collected_paise",
    "collected_pct_of_billed",  // null when billed == 0
    "visits", "outstanding_visits", "refund_visits",
    "by_mode": { "cash"|"card"|"upi": { billed, collected, outstanding, ... } }
  },
  "analytics": {
    "revenue_by_hour": [{hour, hour_label, revenue_paise, visits}],
    "peak_hour", "peak_hour_label", "peak_hour_range", "peak_hour_revenue_paise",
    "top_medicines_by_quantity": [{rank, drug_name, qty, revenue_paise}],
    "top_medicines_by_revenue": [{rank, drug_name, revenue_paise, qty}]
  }
}
```

### `POST /reports/compute`
Stateless: same report shape computed from posted `rows` without storage. Strict — any invalid row → `400`.

### `POST /narrative/{date}` · `GET /narrative/{date}`
Generates (POST) or returns the cached (GET) WhatsApp-style narrative:

```jsonc
{
  "date", "narrative", "traced_figures": [{figure, report_field, report_field_label}],
  "model", "source": "openrouter"|"ollama"|"fallback", "grounded": bool,
  "generated_at", "llm_error": str|null
}
```

`GET` returns `404` when no narrative exists yet.

### `GET /narrative/{date}/report`
The report used as narrative context (same shape as `/reports/eod/{date}`).

## REST API structure & data consistency on update

Design of the ingestion → report → narrative pipeline:

1. **`POST /billing/ingest`** validates every row (`validation.py`). Valid rows are kept; malformed ones return `{index, visit_id, field, error}` (or reject the whole payload with `strict: true`).
2. **Storage** (`storage.py`, SQLite): one row per clinic-day keyed by `date`. Re-ingesting the same date runs a **single UPSERT inside an implicit transaction** — the day is replaced atomically, never appended, so concurrent writers cannot interleave partial days (WAL mode).
3. **Reports are never stored.** `GET /reports/eod/{date}` and narrative generation **recompute** from the stored rows via pure functions in `reconcile.py`. A report can therefore never drift from its source data; updating a day immediately changes every subsequent report/narrative for that date.
4. **Narratives** are cached per date (`narratives` table) but always derived from the recomputed report at generate time; `POST /narrative/{date}` regenerates and appends a new cached entry (`GET` returns the latest).

In short: **replace the day transactionally, recompute on read** — updates stay consistent without multi-table bookkeeping.

## Metric definitions

Deterministic rules (documented in `backend/app/reconcile.py`):

- **`billed`** = Σ(`qty × unit_price_paise − discount_paise`) over **sale** rows (`is_refund: false`), net of discount — so `outstanding = billed − collected` holds exactly (mock: 42,850 − 38,200 = 4,650).
- **`collected`** = Σ `amount_paid_paise` on sale rows.
- **`refunded`** = Σ `|amount_paid_paise|` on refund rows, reported **separately** (not netted into billed/collected).
- **`outstanding`** = `billed − collected` (per mode and total); `outstanding_visits` = sale visits with a positive remainder. Always ≥ 0: ingest rejects any sale where `amount_paid_paise > billed`.
- **`collected_pct_of_billed`** = `round(100 × collected / billed)`; `null` when `billed == 0` (empty/refunds-only days).
- **Hour revenue** = net billed for rows whose UTC timestamp falls in that hour; hour sums to total billed. Peak = hour with max revenue (ties → earliest hour).
- **Medicine rankings** = gross line totals (`qty × unit_price`, **before** discount), non-refund rows only; top 5 by quantity and, separately, by revenue.
- **Drug names** = exact match (case-sensitive). Known dataset typo `PARACETMOL` vs `PARACETAMOL` is kept distinct and flagged via a `difflib` data-quality warning — never silently merged.

## Data-consistency notes (edge cases in the sample set)

| Day | Behavior |
|---|---|
| **25 Jul 2026** | Refunds-only: no sale visits → billed/collected ₹0, refunds shown separately, warning emitted, empty chart/rankings in UI. |
| **26 Jul 2026** | Empty log (`[]`): zeroed report + "No visits recorded" warning; clinic identity comes from the stored day record (not rows), so meta still shows the clinic name. |
| **27 Jul 2026** | 18 valid rows + 1 malformed (`payment_mode` missing on `V-20260727-019`) → partial ingest accepts 18, rejects 1 with a field-level error; report excludes the rejected row and notes it. |

Re-ingesting a date is an atomic UPSERT — the day is replaced, never appended.

## LLM grounding

1. Pre-format a **FACTS sheet** + full report JSON for the model (no free-form number lookup).
2. Extract numeric tokens from the response; every token must map to a report field (`traced_figures`), else the response is rejected as ungrounded.
3. **Label ↔ amount check:** a figure that exists in the report but is bound to the wrong word (e.g. `Billed ₹3,172` when billed is ₹3,190 and ₹3,172 is collected) is rejected as mislabelled — as is `99% of billed` written without a nearby `collected`.
4. Off-schema/ungrounded/mislabelled responses, and sales-day narratives missing a required structure line (busiest hour or either ranking), get **1 retry** with corrective feedback, then a **deterministic grounded fallback** (`source: "fallback"`) so the endpoint never fails closed.
5. Transport errors (Ollama down/timeout) fall back immediately without retrying.

The UI shows `grounded: true` as "Every figure grounded ✓", the model name, and a `FALLBACK` badge with the LLM note when applicable.

## Frontend

Three screens matching the PDF mockups (`screen1-reconciliation.png`, `screen2-analytics.png`, `screen3-narrative.png` in the repo root):

1. **EOD Reconciliation** — stat cards (billed/collected/outstanding/refunds), payment-mode table, data-quality warnings.
2. **Analytics** — revenue-by-hour bar chart (peak highlighted), top-5 medicines by quantity and by revenue.
3. **AI Narrative Summary** — WhatsApp-style bubble + traced-figures panel with generate/regenerate.

Shared date picker (populated from `/billing/days`, badges rejected-row counts), refresh, skip-link, `:focus-visible` states, `aria-live` on generate status, `prefers-reduced-motion` support, `tabular-nums` for figures, and `Intl.NumberFormat("en-IN")` for currency/dates.

## Tests & lint

```bash
cd backend
uv run pytest -q        # 97 passed
uv run ruff check .
uv run ruff format --check .

cd ../frontend
npm run lint            # ESLint (react-hooks, import rules)
npm run build           # production build
```

## Deployment (Vercel + Render)

**Backend → Render**

- Root: `backend/`, build: `uv sync`, start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- Env: `OLLAMA_BASE_URL` (reachable from Render — local Ollama won't work; point at a hosted Ollama or leave default for fallback), `OLLAMA_MODEL`, `ALLOWED_ORIGINS=https://<your-vercel-app>`, `DATABASE_PATH` (persistent disk), `SEED_SAMPLE_DATA=1` for first boot.

**Frontend → Vercel**

- Root: `frontend/`, build: `npm run build`, output: `dist`.
- Env: `VITE_API_BASE=https://<render-service>.onrender.com/api`.

The Vite dev server is pinned to **port 5174** (5173 was occupied) with `/api` proxied to `127.0.0.1:8000`.

## Repository layout

```
backend/
  app/           # config, validation, reconcile, storage, figures, llm, narrative, routes, main
  tests/         # 97 pytest tests (validation, reconcile, API, narrative/grounding/labels)
  sample_data/   # 3 clinic-days + README (canonical copies of the provided dataset)
frontend/
  src/           # App, pages (Reconciliation, Analytics, Narrative), components, api, format, styles
screen*.png      # visual verification screenshots (3 main screens + edge-day analytics)
```

## Verification (local)

- Backend: `pytest` 97 passed; `ruff check` + `ruff format --check` clean.
- Frontend: `eslint` 0 errors; `vite build` succeeds (code-split Reconciliation/Analytics/Narrative).
- API smoke: banana dates → 400; `strict:"false"` coerced; discount > gross → 400; amount_paid > billed → 400 with a field-level overpayment error; missing narrative → 404; narrative generation returns grounded figures with correct billed/collected labels (mislabelled LLM output is retried once, then falls back).
- UI (Playwright): all three screens match mockups; edge days (25 refunds-only, 26/28 empty) show correct empty states; rapid date-switching settles cleanly; 0 console errors.

> **Re-seed note:** `POST /billing/ingest` with `rows: []` for a date **replaces that day** (atomic UPSERT). To restore sample days after an empty ingest, re-POST the full row set from `backend/sample_data/*.json` for that date.
