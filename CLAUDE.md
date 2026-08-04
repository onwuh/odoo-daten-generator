# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Running the tool

```bash
cd odoo-daten-generator
python3 gui.py
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

## Planning Documents

- **`IMPLEMENTIERUNGSPLAN.md`** (repo root) — the current implementation framework: LLM-minimalism redesign, prioritized bug list (B1–B16), architecture work (D1–D8), roadmap (R1–R5), sprint order (S1–S7). Read it before starting any implementation work; reference its item IDs in commits and discussions.

## Architecture

Generates AI-powered demo data and writes it to Odoo via JSON 2 REST API.
Single entry point: `gui.py` (CustomTkinter wizard, 4 screens).

**Core Modules** (all inside `odoo-daten-generator/`):

| File | Responsibility |
|---|---|
| `gui.py` | 4-screen wizard, background worker thread, log queue |
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
Stammdaten → MRP → CRM → Sales → Accounting → HR → Projects → Timesheets → Recruiting

Critical: `ctx.company_ids` and `ctx.product_ids` are the single source of truth for all
downstream modules. They are seeded by `master_data` + orchestrator fallbacks; when
`skip_master_data` is set, the caller (GUI) must fill them with existing IDs before
`orchestrator.run()`. `ctx.component_ids` holds purchased parts (MRP) — vendor bills draw
from it, sale orders never do.

## LLM Layer

- **Leitprinzip (LLM-Minimalismus):** The LLM supplies only *atomic creative tokens* —
  names, street names, text bodies (chatter, job summaries). Never request complete import
  structures (nested records, addresses, emails, phones, prices, dates). All structure and
  derivable values are assembled deterministically in code. See IMPLEMENTIERUNGSPLAN.md §1.
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
- JSON2 API `create` tries `vals_list` → `args/kwargs` → `values` as fallback formats.
- Bank transactions: 80% exact-match, 20% with amount/label deviations (reconciliation training).
- LLM timeouts use `ThreadPoolExecutor` (cross-platform, Windows-compatible).
- Tests: whenever you add or change behaviour in any module or helper, add or update the corresponding test in `tests/integration/`. Never leave a behaviour change untested.

## Odoo API Conventions (saas-19.4)

- **Client class:** `OdooJson2Client(url, db, api_key)` — there is no `OdooClient`.
- **Datetime fields:** Always use `datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")`. Odoo rejects microseconds produced by `.isoformat()`.
- **Confirmation methods:** The JSON/2 API exposes `action_confirm`, not `button_confirm`. Before assuming any method name, verify via the odoo-fields MCP tool or a live test.

**Verified field gotchas (live-tested):**

- `hr.leave` requires `request_date_from`/`request_date_to` (date) **alongside** `date_from`/`date_to` (datetime) — omitting them defaults to today and causes spurious overlap errors.
- `hr.leave` leave type field is `work_entry_type_id` (→ `hr.work.entry.type`), not `holiday_status_id`.
- `mail.activity.date_deadline` is **required** — always set it.
- `mail.activity.res_model_id` is Many2one → `ir.model` (writable); `res_model` is readonly/computed — never set it manually.
- `message_post` on `crm.lead`: use `client.call_method('crm.lead', 'message_post', ids=[id], kwargs={...})`.
- Leads feature flag: `ir.config_parameter` key `crm.use_lead`, value `'1'` = enabled; missing key = disabled.
- `odoo_actions.get_enabled_features(client, installed_modules)` — **without** the `installed_modules` set, all probes are skipped and the result is `{}`. Always pass the set.
- `hr.job.skill` / `hr.applicant.skill`: `skill_level_id` is **required** and cannot be `False`/omitted — a `hr.skill.type` with no `hr.skill.level` records for it cannot be attached to a job/applicant at all. Skip the skill entirely (don't build the line) when no level exists for it.
- `hr.leave/action_approve` fails with `UserError: "You cannot approve this leave."` when the record is already in `state='validate'` — this happens on creation itself for `hr.work.entry.type` records with certain `leave_validation_type` values (e.g. `'both'`), which auto-validate on create for the API-key user. Check `state` before calling `action_approve` rather than calling it unconditionally.
- `hr.leave.allocation` has **no** `allocation_type` field on saas-19.4 (existed on 19.2, since removed — accrual vs. regular is now implied by `accrual_plan_id` being set or absent). Do not send it.

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
Sprint S3 aus `IMPLEMENTIERUNGSPLAN.md` abgeschlossen (2026-08-03): A1 (`fetch_creative_data`
ersetzt durch `fetch_creative_atoms` + `data_factory.py`/`static_data.py`/`text_utils.py` —
Adressen, Kontakte, Preise, Lieferanten-Adressen jetzt deterministisch im Code, nicht vom LLM),
A2 (Bewerber-E-Mail/Telefon aus Namen abgeleitet statt vom LLM angefragt), A3 (Cache für
`workcenter_data`/`project_stages`/`bom_components`/`creative_atoms`, gemeinsamer
`_cached_llm_call`-Helper, `job_summaries`-Cache-Bug behoben). Landed als 4 separate Commits
(PR1–PR4), je grün gegen die volle Testsuite. S2 (B4, B5, B6, B9, B12, B13) und S1 (B1–B3, B16)
waren bereits vorher erledigt. Nächster Sprint: S4 — Architektur (D1 Fortschritts-Callback,
D2 `logging` statt `print`, D3 Batch-Erstellung; danach B7, B8, B10, B11, B14, B15).

Neuer Backlog-Punkt seit S3-Review: R6 — Multi-Country Customer/Supplier Generation (siehe
§4 Roadmap). `static_data.py` ist bereits länderweise (DE/AT/CH) strukturiert, damit weitere
Märkte eine reine Datenergänzung sind.

## Do Not Touch Without Architect Approval
- Object pipeline execution order in `orchestrator.py`
- JSON2 API fallback logic in `odoo_client.py`
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
