# CLAUDE.md

Guidance for Claude Code, this repo.

## Running the tool

Web app since S9 (`gui.py` deleted, not kept alongside):

```bash
cd odoo-daten-generator && ODOO_GENERATOR_ACCESS_CODE=choose-a-code python3 -m uvicorn web.app:app --host localhost --port 8000
```

`--host localhost`, not `--host 127.0.0.1`: `localhost` resolves to both `::1` and
`127.0.0.1`, browsers try IPv6 first. IPv4-only bind look healthy to `curl` (silent fallback), dead to Safari.

Or Docker (`cp .env.example .env` first, fill access code):

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
API keys via env vars:
- `GROQ_API_KEY` (primary LLM)
- `GEMINI_API_KEY` (fallback LLM)
- `ODOO_API_KEY`
- `GITHUB_TOKEN` (server-only, feedback → GitHub issue creation — no server_config.py fallback, unlike other three)

## Planning Documents

- **`ROADMAP.md`** (repo root, renamed from `IMPLEMENTIERUNGSPLAN.md` 2026-08-29) — open/planned work only as of 2026-09-02: LLM-minimalism redesign, prioritized bug list (B1–B16), architecture work (D1–D8), roadmap (R1–R20), sprint order (S1–S15, S1–S10 done). Read before any implementation work; reference item IDs in commits/discussions.
- **`ROADMAP_ARCHIVE.md`** (repo root) — completed items pulled from `ROADMAP.md`, keeps that file open-work-only. Same item IDs (B7, D1, R2, …); check here before assuming something still open.
- **`odoo-daten-generator/SPRINT_LOG.md`** — full sprint narrative (peer-review process, live-found bugs, test counts per sprint), pulled from this file's old "Current Sprint" section. Read on demand, not part of per-session load.
- **`odoo-daten-generator/ODOO_GOTCHAS.md`** — live-tested Odoo field/behavior quirks, pulled from "Odoo API Conventions" below. Read before touching any field it covers.

## Architecture

Generates AI-powered demo data, writes to Odoo via JSON 2 REST API.
Single entry point: `web/app.py` (FastAPI; browser UI in `static/` is
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
| `static/` | `index.html` / `app.js` / `app.css` — split apart, CSP forbids inline |
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

(Reordered 2026-08-05, Sprint S7/R8: Accounting moved position 5 → 8 —
confirmed service-line's invoiced quantity delivered-qty-based, computed from
timesheets that must already exist, which in turn need employees/the order's
auto-created task to exist first.)

Critical: `ctx.company_ids` and `ctx.product_ids` single source of truth, all
downstream modules. Seeded by `master_data` + orchestrator fallbacks; when
`skip_master_data` set, caller (GUI) must fill them with existing IDs before
`orchestrator.run()`. `ctx.component_ids` holds purchased parts (MRP) — vendor bills draw
from it, sale orders never do. `ctx.confirmed_order_ids` (populated by `sale.py`) also
read by `project.py`'s `create_timesheet_data` since Sprint S7/R8 (billable order-linked
tasks claim timesheet budget first).

## LLM Layer

- **Leitprinzip (LLM-Minimalismus):** LLM supplies only *atomic creative tokens* —
  names, street names, text bodies (chatter, job summaries). Never request complete import
  structures (nested records, addresses, emails, phones, prices, dates). All structure/
  derivable values assembled deterministically in code. See ROADMAP.md §1.
- **Primary:** Groq (`llama-3.3-70b-versatile`), OpenAI-compatible endpoint
- **Fallback:** Google Gemini
- **Caching:** `seeds/cache/<slug-parts>_<_PROMPT_VERSION>.json` (e.g.
  `it_dienstleistung_german_name_suggestions_v3.json`). Always check cache before LLM
  call. Bump `_PROMPT_VERSION` in `llm_service.py` whenever prompt wording changes —
  else stale cache masks new prompt. Chatter messages deliberately uncached
  (variance wanted).
- **Batching:** Never call LLM once per record in loop. Always batch: one call → JSON
  array/object (enforced by test Pattern 8).
- **Format:** All LLM responses expect clean JSON (no markdown fences). `_extract_json`
  strips ```json``` wrappers. Guard every response: `None`, `{}`, `[]` must not crash
  (test Pattern 2).

## Key Conventions

- UI strings: German. Code/function names/comments: English.
- Invalid Odoo fields from LLM (`uom`, `vat`, `vat_id`, `detailed_type`) filtered before API calls.
- JSON2 API `create` sends `{"vals_list": [values]}` — one real format, no fallback. (Prior codebase versions tried `args/kwargs` and `values` as fallbacks; removed — see "JSON2 payload-format fallback chain" below.)
- Bank transactions: 80% exact-match, 20% with amount/label deviations (reconciliation training).
- LLM timeouts use `ThreadPoolExecutor` (cross-platform, Windows-compatible).
- Tests: any behaviour added/changed in module or helper → add/update matching test in `tests/integration/`. Never leave behaviour change untested.

## Odoo API Conventions (saas-19.4)

- **Client class:** `OdooJson2Client(url, db, api_key)` — no `OdooClient`.
- **Datetime fields:** Always `datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")`. Odoo rejects microseconds from `.isoformat()`.
- **Confirmation methods:** JSON/2 API exposes `action_confirm`, not `button_confirm`. Verify any method name via odoo-fields MCP tool or live test first.

Full live-tested field/behavior gotchas list: `odoo-daten-generator/ODOO_GOTCHAS.md`. Read before touching any field not already covered there.

## Debugging

Fixing bugs: verify root cause before fix. Check data issues (leftover test data, uniqueness constraints) before blaming code.

## Odoo-Specific Rules

Odoo dev: verify field names against actual Odoo model schema before use. Don't assume fields like `company_type`, `is_company`, `company_id`, `country` exist — use MCP field metadata server or read model definition first. Target: Odoo saas-19.4 (updated 2026-08-03; live test instance `demo-pahu-test1.odoo.com` confirmed saas-19.4, previously assumed 19.2 — some 19.2-era fields, e.g. `hr.leave.allocation.allocation_type`, gone now).

## Workflow Rules

Don't exit plan mode without explicit user approval. Multi-part task: implement each part fully before next — no plan files instead of implementations unless asked.

## Testing

Run tests after every code change, especially Python files. Odoo modules: validate via relevant test suite before calling task done.

After every code change, run full suite against live Odoo instance:

```bash
cd odoo-daten-generator
python3 tests/integration/test_suite.py   # runs unit suite first, then live integration
```

Config: `tests/test_config.ini` (gitignored) used if present, else `config.ini`.
Task done only when integration tests pass. Mock-based unit tests (`unittest.mock`) OK
for behaviour unverifiable without side effects, but don't replace live integration tests.

## Current Sprint
<!-- Architect updates this before each Claude Code session -->
Sprint-für-Sprint-Narrativ (S1-S10, abgeschlossen) ausgelagert nach `odoo-daten-generator/SPRINT_LOG.md` (2026-09-02) — Kontext, Peer-Review-Ergebnisse und live gefundene Bugs pro Sprint stehen dort. Aktueller Stand: S1-S12 abgeschlossen (2026-09-02, Branch `s11-api-version-compat-d9`, noch nicht in main gemerged) — S11: R5 WP1/WP2/WP4/WP5 + D9 umgesetzt, WP3 (Übersetzungs-Registry) nach Cold-Review zurückgestellt (siehe `ROADMAP_ARCHIVE.md`s R5/D9-Statusblöcken). S12: R11 (Lost Opportunities) + R19 (Expenses) vollständig umgesetzt und archiviert, R16 Produkt-Ebene (Barcode) umgesetzt (Location-Ebene bleibt in S13 offen) — siehe `ROADMAP.md`s "S12 — WP-Sequenz" für den Ablauf, `ROADMAP_ARCHIVE.md`s R11/R19-Statusblöcken für Details. Beide Sprints peer-reviewed vor Merge (S5-S11-Verfahren), bereit für PR nach `main`. Nächster Sprint: noch nicht festgelegt, siehe `ROADMAP.md` §5 für offene Kandidaten (S13+).

## Do Not Touch Without Architect Approval
- Object pipeline execution order in `orchestrator.py`
- JSON2 API request format in `odoo_client.py` — one format per operation since payload-format fallback chain removed (evidence: see "JSON2 payload-format fallback chain" below). Don't reintroduce speculative path/payload variant without live evidence needed.
- Config schema (dataclasses in `config.py`)
- Seed cache file naming convention

## Testing Design Patterns

Patterns **mandatory** — any behavior added/changed must include matching test type.

### Pattern 1: Empty-Pool Guard
Any `random.choice(pool)`, `random.sample(pool, k)`, or `pool[i % len(pool)]` call MUST
have guard test for `pool=[]`. Test verify: no exception raised, function returns
early with warning print (not silent).
```python
# Required unit test shape:
result = fn_under_test(client=mock, pool=[])
assert result == [] or result is None  # graceful skip
mock_client.create.assert_not_called()
```

### Pattern 2: LLM None-Response Guard
Every function calling LLM (directly or via `gemini.*`) MUST test:
- LLM returns `None` → no AttributeError on caller
- LLM returns `{}` or `[]` → fallback used or step skipped gracefully
```python
mock_gemini.fetch_xyz.return_value = None
# must not raise
```

### Pattern 3: Feature-Flag Skip
Any code path gated on `feature_flags` or `ModuleSelections` bool/dict MUST have test
asserting when flag False/empty, **no Odoo API calls made**:
```python
mock_client.create.assert_not_called()
mock_client.write.assert_not_called()
```

### Pattern 4: Read-Back Validation
Every integration test step creating a record MUST immediately read it back, assert
at least one non-trivial field (not just `id > 0`). Catches field name errors early.
```python
# Required integration test shape:
rec_id = client.create('model', vals)
assert isinstance(rec_id, int) and rec_id > 0
rec = client.search_read('model', [['id','=',rec_id]], fields=['field_a', 'field_b'], limit=1)
assert rec[0]['field_a'] == expected_value
```

### Pattern 5: Module Skip-on-Missing-Prerequisites
Every module test MUST include step passing empty prerequisite lists, assert
graceful skip (no crash, informative result entry):
```python
ctx.partner_ids = []
ok, results = test_module.run(client, ctx)
assert any("SKIP" in label for label, _, _ in results)
```

### Pattern 6: Many2one Tuple Unpacking
After creating record with Many2one relation, read-back test MUST handle both
`[id, name]` tuple and plain `int` return shapes:
```python
val = rec[0]["partner_id"]
pid = val[0] if isinstance(val, (list, tuple)) else val
assert pid == expected_id
```

### Pattern 7: Distribution / Statistical Tests
For any function randomizing output across buckets (percentage splits, past/today/future,
80/20 deviation), use `random.seed(N)` + N>=100 samples, assert **all** buckets non-empty:
```python
random.seed(42)
results = [fn(past_pct=50, today_pct=20) for _ in range(200)]
assert any(r < today for r in results), "no past dates"
assert any(r == today for r in results), "no today dates"
assert any(r > today for r in results), "no future dates"
```

### Pattern 8: Batch LLM Call Enforcement
For any module generating multiple records from single LLM call, assert call count == 1:
```python
mock_llm = MagicMock()
mock_llm.fetch_xyz.return_value = [...]  # N items
module.create_xyz(mock_client, mock_llm, ctx)
assert mock_llm.fetch_xyz.call_count == 1, "LLM called in loop instead of batch"
```