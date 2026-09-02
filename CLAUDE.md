# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Running the tool

Web application since S9 (`gui.py` is deleted, not kept alongside):

```bash
cd odoo-daten-generator && ODOO_GENERATOR_ACCESS_CODE=choose-a-code python3 -m uvicorn web.app:app --host localhost --port 8000
```

`--host localhost`, not `--host 127.0.0.1`: `localhost` resolves to both `::1` and
`127.0.0.1`, and browsers try IPv6 first. An IPv4-only bind therefore looks
healthy to `curl` (which silently falls back) and dead to Safari.

Or in Docker (`cp .env.example .env` first, fill in the access code):

```bash
cd odoo-daten-generator && docker compose up -d --build
```

## Setup

```bash
python3 -m venv .venv          # venv lives at repo root
source .venv/bin/activate
pip install -r odoo-daten-generator/requirements.txt
```

Configure `odoo-daten-generator/config.ini` with Odoo connection defaults.
API keys via environment variables:
- `GROQ_API_KEY` (primary LLM)
- `GEMINI_API_KEY` (fallback LLM)
- `ODOO_API_KEY`
- `GITHUB_TOKEN` (server-only, feedback → GitHub issue creation — no server_config.py fallback, unlike the three above)

## Planning Documents

- **`ROADMAP.md`** (repo root, renamed from `IMPLEMENTIERUNGSPLAN.md` 2026-08-29) — open/planned work only as of 2026-09-02: LLM-minimalism redesign, prioritized bug list (B1–B16), architecture work (D1–D8), roadmap (R1–R20), sprint order (S1–S15, S1–S10 done). Read it before starting any implementation work; reference its item IDs in commits and discussions.
- **`ROADMAP_ARCHIVE.md`** (repo root) — completed items pulled out of `ROADMAP.md` to keep that file to open work. Same item IDs (B7, D1, R2, …); check here before assuming something is still open.
- **`odoo-daten-generator/SPRINT_LOG.md`** — full sprint-by-sprint narrative (peer-review process, live-found bugs, test counts per sprint), pulled out of this file's old "Current Sprint" section. Read on demand, not part of the per-session load.
- **`odoo-daten-generator/ODOO_GOTCHAS.md`** — live-tested Odoo field/behavior quirks, pulled out of "Odoo API Conventions" below. Read before touching any field it covers.

## Architecture

Generates AI-powered demo data and writes it to Odoo via JSON 2 REST API.
Single entry point: `web/app.py` (FastAPI; the browser UI in `static/` is a
4-view console — Verbindung, Konfiguration, Prüfen, Generierung).

**Core Modules** (all inside `odoo-daten-generator/`):

| File | Responsibility |
|---|---|
| `web/app.py` | FastAPI routes, CSP/CSRF, static frontend, SSE endpoint |
| `web/security.py` | Guard A (demo-host allowlist) + Guard B (SSRF) |
| `web/session.py` | Shared-access-code auth; credentials memory-only, per session |
| `web/jobs.py` | Worker pool, admission control, run records, progress callbacks |
| `web/sse.py` | Per-run append-only event stream (log/module/status/end) |
| `web/feedback.py` | POST /api/feedback → GitHub issue creation via PAT (`GITHUB_TOKEN`) |
| `connect_service.py` | Connection probe checklist (D4, ex-`gui.py` screen 2) |
| `run_config.py` | Request payload → `DemoCriteria`/`ModuleSelections`; `WANTED_MODULES` |
| `run_journal.py` | D7 run markers (`seeds/runs/<run_id>.json`) + best-effort cleanup |
| `static/` | `index.html` / `app.js` / `app.css` — split apart because CSP forbids inline |
| `config.py` | Dataclasses: DemoCriteria, ModuleSelections, RunContext |
| `llm_service.py` | LLMService: Groq (primary) + Gemini (fallback), seed caching |
| `odoo_client.py` | Low-level HTTP wrapper, JSON2 API, payload-format fallbacks |
| `odoo_actions.py` | Shared cross-module helpers (installed modules, feature flags, company info) |
| `odoo_repository.py` | Batch reference-data lookups (countries, skill levels) — avoids N+1 |
| `fallback_data.py` | Static fallback names when LLM unavailable |
| `orchestrator.py` | Dependency-ordered execution + fallback partner/product seeding |
| `modules/` | One file per domain (master_data, crm, sale, accounting, hr, project, mrp, recruiting) |
| `tests/unit/` | Offline tests (mocks) — run via `unit_suite.py` |
| `tests/integration/` | Live-Odoo tests — run via `test_suite.py` |

## Object Pipeline

Execution order (respect dependencies, never change without architect approval):
Stammdaten → MRP → CRM → Sales → HR → Projects → Timesheets → Accounting → Recruiting

(Reordered 2026-08-05, Sprint S7/R8: Accounting moved from position 5 to position 8 —
a confirmed service-line's invoiced quantity is delivered-qty-based and computed from
timesheets that must already exist, which in turn need employees/the order's
auto-created task to exist first.)

Critical: `ctx.company_ids` and `ctx.product_ids` are the single source of truth for all
downstream modules. They are seeded by `master_data` + orchestrator fallbacks; when
`skip_master_data` is set, the caller (GUI) must fill them with existing IDs before
`orchestrator.run()`. `ctx.component_ids` holds purchased parts (MRP) — vendor bills draw
from it, sale orders never do. `ctx.confirmed_order_ids` (populated by `sale.py`) is also
read by `project.py`'s `create_timesheet_data` since Sprint S7/R8 (billable order-linked
tasks claim the timesheet budget first).

## LLM Layer

- **Leitprinzip (LLM-Minimalismus):** The LLM supplies only *atomic creative tokens* —
  names, street names, text bodies (chatter, job summaries). Never request complete import
  structures (nested records, addresses, emails, phones, prices, dates). All structure and
  derivable values are assembled deterministically in code. See ROADMAP.md §1.
- **Primary:** Groq (`llama-3.3-70b-versatile`), OpenAI-compatible endpoint
- **Fallback:** Google Gemini
- **Caching:** `seeds/cache/<slug-parts>_<_PROMPT_VERSION>.json` (e.g.
  `it_dienstleistung_german_name_suggestions_v3.json`). Always check cache before an LLM
  call. Bump `_PROMPT_VERSION` in `llm_service.py` whenever prompt wording changes —
  otherwise stale cache masks the new prompt. Chatter messages are deliberately uncached
  (variance wanted).
- **Batching:** Never call the LLM once per record in a loop. Always batch: one call → JSON
  array/object (enforced by test Pattern 8).
- **Format:** All LLM responses expect clean JSON (no markdown fences). `_extract_json`
  strips ```json``` wrappers. Guard every response: `None`, `{}`, `[]` must not crash
  (test Pattern 2).

## Key Conventions

- UI strings: German. Code/function names/comments: English.
- Invalid Odoo fields from LLM (`uom`, `vat`, `vat_id`, `detailed_type`) are filtered before API calls.
- JSON2 API `create` sends `{"vals_list": [values]}` — the one real format, no fallback formats. (Prior versions of this codebase tried `args/kwargs` and `values` as fallbacks; removed — see "JSON2 payload-format fallback chain" below.)
- Bank transactions: 80% exact-match, 20% with amount/label deviations (reconciliation training).
- LLM timeouts use `ThreadPoolExecutor` (cross-platform, Windows-compatible).
- Tests: whenever you add or change behaviour in any module or helper, add or update the corresponding test in `tests/integration/`. Never leave a behaviour change untested.

## Odoo API Conventions (saas-19.4)

- **Client class:** `OdooJson2Client(url, db, api_key)` — there is no `OdooClient`.
- **Datetime fields:** Always use `datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")`. Odoo rejects microseconds produced by `.isoformat()`.
- **Confirmation methods:** The JSON/2 API exposes `action_confirm`, not `button_confirm`. Before assuming any method name, verify via the odoo-fields MCP tool or a live test.

Full live-tested field/behavior gotchas list: `odoo-daten-generator/ODOO_GOTCHAS.md`. Read it before touching any field this list doesn't already cover well.

## Debugging

When fixing bugs, always verify the root cause before implementing a fix. Check for data-related issues (e.g., leftover test data, uniqueness constraints) before assuming code-level problems.

## Odoo-Specific Rules

For Odoo development: Always verify field names against the actual Odoo model schema before using them. Do NOT assume fields like `company_type`, `is_company`, `company_id`, or `country` exist — use the MCP field metadata server or read the model definition first. Target version is Odoo saas-19.4 (updated 2026-08-03; live test instance `demo-pahu-test1.odoo.com` confirmed as saas-19.4, was previously assumed 19.2 — some 19.2-era fields, e.g. `hr.leave.allocation.allocation_type`, no longer exist).

## Workflow Rules

Do NOT exit plan mode until you have explicit user approval. When a task has multiple parts, implement each part fully before moving to the next — do not produce plan files instead of implementations unless explicitly asked.

## Testing

Always run tests after making code changes, especially in Python files. For Odoo modules, validate by running the relevant test suite before considering a task complete.

After every code change, run the full suite against the live Odoo instance:

```bash
cd odoo-daten-generator
python3 tests/integration/test_suite.py   # runs unit suite first, then live integration
```

Config: `tests/test_config.ini` (gitignored) is used if present, otherwise `config.ini`.
A task is only complete once the integration tests pass. Mock-based unit tests (using
`unittest.mock`) are acceptable for behaviour that cannot be verified without side effects,
but do not replace live integration tests.

## Current Sprint
<!-- Architect updates this before each Claude Code session -->
Sprint-für-Sprint-Narrativ (S1-S10, abgeschlossen) ausgelagert nach `odoo-daten-generator/SPRINT_LOG.md` (2026-09-02) — Kontext, Peer-Review-Ergebnisse und live gefundene Bugs pro Sprint stehen dort. Aktueller Stand: S1-S10 abgeschlossen, nächster Sprint offen. Siehe `ROADMAP.md` §5 (Umsetzungsreihenfolge) für Backlog-Kandidaten.

## Do Not Touch Without Architect Approval
- Object pipeline execution order in `orchestrator.py`
- JSON2 API request format in `odoo_client.py` — one format per operation since the payload-format fallback chain was removed (evidence: see "JSON2 payload-format fallback chain" below). Do not reintroduce a speculative path/payload variant without live evidence it's needed.
- Config schema (dataclasses in `config.py`)
- Seed cache file naming convention

## Testing Design Patterns

These patterns are **mandatory** — any behavior added/changed must include the corresponding test type.

### Pattern 1: Empty-Pool Guard
Any `random.choice(pool)`, `random.sample(pool, k)`, or `pool[i % len(pool)]` call MUST
have a guard test for `pool=[]`. Test must verify: no exception raised, function returns
early with a warning print (not silently).
```python
# Required unit test shape:
result = fn_under_test(client=mock, pool=[])
assert result == [] or result is None  # graceful skip
mock_client.create.assert_not_called()
```

### Pattern 2: LLM None-Response Guard
Every function that calls LLM (directly or via `gemini.*`) MUST test:
- LLM returns `None` → no AttributeError on caller
- LLM returns `{}` or `[]` → fallback used or step skipped gracefully
```python
mock_gemini.fetch_xyz.return_value = None
# must not raise
```

### Pattern 3: Feature-Flag Skip
Any code path gated on `feature_flags` or a `ModuleSelections` bool/dict MUST have a test
asserting that when the flag is False/empty, **no Odoo API calls are made**:
```python
mock_client.create.assert_not_called()
mock_client.write.assert_not_called()
```

### Pattern 4: Read-Back Validation
Every integration test step that creates a record MUST immediately read it back and assert
at least one non-trivial field (not just `id > 0`). Catches field name errors early.
```python
# Required integration test shape:
rec_id = client.create('model', vals)
assert isinstance(rec_id, int) and rec_id > 0
rec = client.search_read('model', [['id','=',rec_id]], fields=['field_a', 'field_b'], limit=1)
assert rec[0]['field_a'] == expected_value
```

### Pattern 5: Module Skip-on-Missing-Prerequisites
Every module test MUST include a step that passes empty prerequisite lists and asserts
graceful skip (no crash, informative result entry):
```python
ctx.partner_ids = []
ok, results = test_module.run(client, ctx)
assert any("SKIP" in label for label, _, _ in results)
```

### Pattern 6: Many2one Tuple Unpacking
After creating any record with a Many2one relation, the read-back test MUST handle both
`[id, name]` tuple and plain `int` return shapes:
```python
val = rec[0]["partner_id"]
pid = val[0] if isinstance(val, (list, tuple)) else val
assert pid == expected_id
```

### Pattern 7: Distribution / Statistical Tests
For any function that randomizes output across buckets (percentage splits, past/today/future,
80/20 deviation), use `random.seed(N)` + N>=100 samples and assert **all** buckets non-empty:
```python
random.seed(42)
results = [fn(past_pct=50, today_pct=20) for _ in range(200)]
assert any(r < today for r in results), "no past dates"
assert any(r == today for r in results), "no today dates"
assert any(r > today for r in results), "no future dates"
```

### Pattern 8: Batch LLM Call Enforcement
For any module that generates multiple records from a single LLM call, assert call count == 1:
```python
mock_llm = MagicMock()
mock_llm.fetch_xyz.return_value = [...]  # N items
module.create_xyz(mock_client, mock_llm, ctx)
assert mock_llm.fetch_xyz.call_count == 1, "LLM called in loop instead of batch"
```
