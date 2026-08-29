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

- **`ROADMAP.md`** (repo root, renamed from `IMPLEMENTIERUNGSPLAN.md` 2026-08-29) — the current implementation framework: LLM-minimalism redesign, prioritized bug list (B1–B16), architecture work (D1–D8), roadmap (R1–R20), sprint order (S1–S15, S1–S10 done). Read it before starting any implementation work; reference its item IDs in commits and discussions.

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
- `ir.attachment` binary content field is `raw`, **not** `datas` — `datas` doesn't exist as a field on this instance at all (`search_read` raises `Invalid field 'datas' on 'ir.attachment'`), yet `create()` silently accepts and drops a `datas` key instead of raising (200 OK, no content stored, no error). `db_datas` also exists in `fields_get` but writing it directly is silently dropped too (attachments are filestore-backed here, not DB-column-backed). Only `raw` round-trips actual file content on create/read.
- `product.template`/`product.product`: enabling Odoo's native "auto-create Project+Task on order confirmation" needs **three** independent fields, not two — `service_tracking='task_in_project'` (creates the Project+Task), `invoice_policy='delivery'` (invoice delivered not ordered qty), **and** `service_type='timesheet'` (the field that actually makes `sale.order.line.qty_delivered_method` become `'timesheet'`, easy to miss since it doesn't follow from the other two). Do not use the convenience field `service_policy` — it's `store: false` (a UI-only onchange helper) and may be silently dropped by `create()` on this instance, same trap class as `ir.attachment.datas`.
- `account.move.line.sale_line_ids` (m2m → `sale.order.line`) is **readonly** — cannot be set on `account.move`/`account.move.line` `create()`. The writable reverse side is `sale.order.line.invoice_lines` (m2m → `account.move.line`) — but as of Sprint S7/R8, invoicing goes through the `sale.advance.payment.inv` wizard instead, which sets this link server-side natively; no manual write needed either way.
- Invoicing a `sale.order` the way Odoo's own "Create Invoice" button does: create a `sale.advance.payment.inv` record (`advance_payment_method='delivered'`, `sale_order_ids=[(6,0,[order_id])]`), then `call_method('sale.advance.payment.inv', 'create_invoices', ids=[wizard_id])` — this public method (no leading underscore) is callable via JSON2 even though it wraps the private `sale.order._create_invoices()`. Read back the created invoice(s) via `sale.order.invoice_ids` (m2m → `account.move`) afterward — the method's own return value is an `ir.actions.act_window` dict, not usable directly. A single order with nothing currently invoiceable (e.g. a delivery-policy line with `qty_delivered=0`) makes the call raise for that order; call the wizard **per order**, not once for a whole batch, so one such order doesn't drag every other invoiceable order in the batch into a fallback with it — live-confirmed a multi-order batch does NOT raise as a whole when only some orders are empty, but a solo empty-order call does.
- `RunContext.company_ids` (`config.py`) is misleadingly named: despite "company", it holds **`res.partner`** IDs — customer/company contacts `master_data.py` creates via `client.create_batch('res.partner', ...)` — **never** a real `res.company` ID. `sale.py` already uses `ctx.company_ids[i]` correctly as a `sale.order.partner_id`; `mrp.py:282,334` does not (it passes `ctx.company_ids[0]` as `company_id` to `mrp.workcenter`/`get_manufacturing_picking_type_id`, silently degraded behind broad try/except — likely a live, pre-existing bug, not fixed as of S8). Wherever an actual `res.company` ID is needed (e.g. `stock.warehouse`/`purchase.order`/`stock.quant` `company_id`), use `odoo_actions.get_main_company_id(client)` instead (added in S8) — never `ctx.company_ids[0]`.
- Odoo's JSON/2 **error object** is `{"name", "message", "arguments", "timestamp", "context", "debug"}` — `debug` carries a full server-side traceback with `/home/odoo/src/...` paths. `odoo_client._redact_error_body` takes only `message`; nothing else crosses into the log or `self.errors`.
- Odoo SaaS prefixes some error messages with runs of **invisible characters** (zero-width joiners, variation selectors `U+FE00–FE0F`, tag characters `U+E0000–E007F` — a tracing watermark). They make the real message unreadable in a log; `odoo_client._printable` strips them (category `Cf` alone is not enough — variation selectors are `Mn`).
- `requests.Session` has **no** `allow_redirects` attribute (`__attrs__` carries `max_redirects` only), so a session-level assignment is a silent no-op — it must be a per-request kwarg at every `session.post` call site. Also note `raise_for_status()` ignores 3xx: an unfollowed redirect falls through to `response.json()` as an opaque JSONDecodeError unless explicitly rejected (`odoo_client._reject_redirect`).
- **JSON2 payload-format fallback chain — removed.** `odoo_client.py` used to try `/call_kw/<model>/<method>` and `/call/<model>/<method>` as fallback paths for `create`/`write`/`call_method`/`model_method`, plus `args`/`kwargs`/`values` as alternate payload shapes. Neither fallback path has a single recorded success anywhere in this repo's history. Confirmed live, independently, twice: both fake paths 404 with `"Did you mean POST /json/2/<model>/<method>?"` unconditionally — even *before* Odoo's own auth check runs (the direct `/<model>/<method>` form 401s on a bad key; `/call/...`/`/call_kw/...` 404 regardless of the key). JSON-2 exposes exactly one route per model+method and dispatches by matching JSON keys to the target method's own parameter names — there is no positional-args concept. Every real call site already hit the first (only real) format on the happy path. `call_method` no longer takes an `args` parameter (0 of 15 call sites used it) — a caller that still passes it gets a `TypeError` at the call site. Each of the five core methods now sends exactly one request.
- `client.errors` gets **one entry per failed logical operation**, not one per HTTP attempt (`odoo_client._record_failure`) — before the chain removal this meant up to eight entries for one failed call, which is why 8 of 14 errors in a live report turned out to be planned 404 probing, not real failures. `run_journal._first_new_error` (mark-and-diff over `client.errors`) still exists and still works, now mostly relevant for `_post`'s own 401-retry sub-attempts rather than a cross-method chain.
- `POST /json/2/<model>/has_access` with `{"ids": [], "operation": "create"}` (a **named** payload key — `args=[...]` is rejected) answers whether the API-key user may create records on `model`: `true`/`false` for a real model, `404` if the model doesn't exist. `check_access_rights` is gone on saas-19.4; `has_access` is the successor. It answers a *write*-access question that `search_read`/`fields_get` cannot: on `demo-test5`, a **read** probe on `mrp.workcenter` reported `True` while the "Work Orders" setting checkbox was off, so a run started and was guaranteed to fail on its first work-center create. `odoo_client.has_create_access(model)` wraps this as a single direct POST rather than going through `model_method`/`call_method` — those wrap every attempt in `_record_failure`, which always records a failure, and probing is expected to 404 for models that don't exist here; that must not clutter the error report the way a real operation's failure does. Only a real `403`/`404` counts as a definitive "no"; a `429`/`5xx`/timeout must read as "unknown" (default open), or a rate limit hit while probing silently disables a module that was actually fine.
- Odoo refuses `unlink` on a lot of what a full run creates: posted `account.move` and every `res.partner`/`product.product` it references ("restricted audit trail"), confirmed `sale.order` ("must first cancel it"), invoiced `account.analytic.line`, `stock.quant` (no delete group for the API user), `project.task.type` still referenced by tasks. Cancel first where Odoo names it (`sale.order.action_cancel`, `account.move.button_draft`+`button_cancel`); expect the rest to stay.
- **Groq retires models without notice.** `llama-3.3-70b-versatile` 404s as of 2026-08-28; the repo default is now `qwen/qwen3.8-27b`. `openai/gpt-oss-120b` pings fine but truncates the JSON responses `fetch_name_suggestions` needs, so a working ping is not proof a model is usable.
- The live SaaS instance **rate-limits with HTTP 429**: sustained write rate is about **1 req/s**, with a token bucket absorbing a burst on top (~150 requests in ~15s measured 2026-08-28), answered by a bare HTML 429 with no `Retry-After`. This is *the* reason the codebase batches everywhere — `create_batch`, D3, test Pattern 8. **Never answer a 429 by retrying inside a loop that should have been one batched call.** `odoo_client._send` adds a bounded exponential backoff (5 attempts, `Retry-After` honoured when sent) as the safety net for the calls batching cannot remove; without it every module after the ceiling fails with `429 Client Error` in a way that reads like a code defect. Space heavy runs out before blaming the code.
- `purchase.order.action_create_invoice` creates the vendor bill with `invoice_date` unset (`False`) — it's the vendor's own external date, Odoo sets no default. `account.move.action_post` then raises `UserError: "Das Datum der Rechnung/Erstattung ist erforderlich..."` (invoice date required). Write `invoice_date` (e.g. `datetime.date.today().isoformat()`) on the bill before posting — the manual `account.move` rebuild fallback already sets it, only the `action_create_invoice` path needs the extra write.
- The `hr.job.payment_interval` failure carried since S10 Phase A was **misdiagnosed as a field-schema issue**; the real cause is that `hr_recruitment` itself is `state=uninstalled` on `demo-test5` (live-confirmed via `ir.module.module`) — `payment_interval` exists fine in Odoo's own `hr.job` schema, it's just unreachable because the whole recruitment app isn't there, and `hr.applicant` doesn't exist as a model at all on this instance. `orchestrator.py:75` already gated `create_recruiting_data` on `"hr_recruitment" in ctx.installed_modules` before this fix — production code was never actually broken. The bug was two live-integration test files calling the low-level recruiting helpers directly, bypassing that gate: `tests/integration/test_recruiting.py` (steps 1/2/4/7) and `tests/integration/test_documents.py` (its setup's applicant creation + the P2 CV-PDF step) both needed the same `'hr_recruitment' in ctx.installed_modules` skip `test_hr.py` already used for `hr_holidays`/`hr_work_entry` in S10 Phase A — `test_recruiting.py` alone wasn't sufficient, `test_documents.py` has an independent unguarded call site.

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
Sprint S4 aus `ROADMAP.md` abgeschlossen (2026-08-03/04): D1 (Fortschritts-Callback
in `orchestrator.run()`, kein Monkeypatch mehr), D2 (`logging_setup.py` statt `print`/
`sys.stdout`-Umleitung), D3 (`create_batch` an 18 Call-Sites über `modules/*.py`), B11
(`call_method`-Fallback-Guard), B14 (Order↔Opportunity partnerbasiert verknüpft), B15
(`num_workcenters` erlaubt 0) landeten am 2026-08-03/04. B7/B8/B10 hatten zu dem Zeitpunkt
bereits ihren Kernbug behoben; im direkten Folgesprint (2026-08-04) ergänzt: B7
(`ModuleSelections.account_bills`, GUI-Feld "Anzahl Eingangsrechnungen"), B8
(`ModuleSelections.sale_confirm_pct`, GUI-Slider "Bestätigt (%)"), B10 (Architekten-Review
entschied gegen den ursprünglich vorgeschlagenen `RunContext.selected_modules`-Umbau — der
Bug war bereits funktional behoben und kein Modul braucht das Feld; nur dokumentiert, kein
Code-Change). S3 (A1–A3), S2 (B4/B5/B6/B9/B12/B13) und S1 (B1–B3, B16) waren bereits vorher
erledigt.

Sprint S5 aus `ROADMAP.md` (2026-08-04) — **Tier 1 abgeschlossen, Tier 2
zurückgestellt**, nach Peer-Review eines vorab geschriebenen Plans durch einen fremden
Opus-Agenten (Kontext nur Plan + Live-Repo, keine Konversationshistorie): `odoo_actions.
get_server_version()` (Versions-Erkennung, GUI-Statuszeile "Odoo-Version" in Screen 2,
nicht-blockierend) + `odoo_actions.check_field_compatibility()` (`fields_get`-Warnliste
gegen ~16 kuratierte Modell/Feld-Paare, log-only). Beide ohne 🔒-Berührung. Tier 2
(`api_versions/*.json`-Mapping-Dateien + Client-Adapter in `odoo_client.py` 🔒) bewusst
zurückgestellt: die Review deckte auf, dass die ursprüngliche Kanonische-Version-Annahme
(19.2) veraltet war (tatsächlich 19.4, siehe unten) **und** dass zwischen den einzigen zwei
bekannten Versionen (19.2/19.4) kein einziger belegter Feld-/Methoden-Rename existiert, nur
eine belegte Feld-Entfernung (`hr.leave.allocation.allocation_type`), die keine Mapping-Zeile
brauchte. Eine Adapter-Infrastruktur ohne einen einzigen realen Delta-Fall wäre nur gegen
synthetische Testdaten prüfbar — Tier 2 wird gebaut, sobald eine zweite reale Zielversion
einen konkreten Rename liefert. Details, Korrekturen und die volle Review-Zusammenfassung:
`ROADMAP.md` §4 R5-Statusblock. Nebenbefund aus dem ersten Live-Lauf der neuen
Warnliste: neuer Bug B17 (`hr.py:33`, `shortcut_behavior` existiert nicht auf saas-19.4) —
dokumentiert, nicht im S5-Scope gefixt.

Odoo-Zielversion-Korrektur bestätigt durch diesen Sprint: `ir.module.module`/`base`/
`latest_version` liefert live `"saas~19.4.1.3"` (nicht `"saas~19.2.1.0.0"` wie ursprünglich
angenommenes Format — eine Dot-Segment-Anzahl weniger; Parser darf keine feste Segmentzahl
voraussetzen).

S5 Tier 2 bleibt im Backlog bis zum oben genannten Auslöser. (Sprint-Reihenfolge ab hier
umnummeriert — siehe S7-Block unten: S7 wurde zu Prozessketten-Kontinuität/R8, S8 ist jetzt
Purchase+Inventory/R2+R3.)

Sprint S6 (2026-08-04) — **abgeschlossen**, nach Peer-Review eines vorab geschriebenen Plans
durch einen fremden Opus-Agenten (Kontext nur Plan + Live-Repo, keine Konversationshistorie,
gleiches Verfahren wie S5): `pdf_factory.py` (neu, reines Python/`fpdf2`, keine Odoo-/
Netzwerk-Calls — `build_vendor_bill_pdf`/`build_cv_pdf`), `modules/documents.py` (neu,
Pipeline-Schritt P1 Eingangsrechnungs-PDFs an `account.move` ohne neuen LLM-Call, P2
Bewerbungs-CVs an `hr.applicant` mit einem gebündelten LLM-Call `fetch_cv_bullet_points_batch`,
nicht gecacht wie `crm_chatter`), `RunContext.applicant_ids` + `ModuleSelections.documents`
(🔒 `config.py`, rein additiv), ein Voraussetzungs-Fix in `recruiting.py`
(`_create_applicants` gab die `create_batch`-IDs bisher nirgendwo zurück — ohne Fix hätte P2
keine Bewerber-ID zum Anhängen gehabt), `orchestrator.py` (🔒, rein additiv — ein Tupel ans
Ende von `module_order` angehängt, `is_installed=True` hartkodiert statt an
`installed_modules` gekoppelt, da `ir.attachment` kernfunktional ist und "documents" zufällig
auch der technische Name von Odoos echter Documents-App ist), GUI-Anbindung in `gui.py`
(die Peer-Review fand hier einen echten Blocker: `documents` ist kein von Odoo geprüftes Modul,
lief also nie durch das `if key in installed`-Gate in `module_defs` — als eigenständiger,
ungegateter `_module_block`-Aufruf plus eigenständiger Zweig in `_on_generate` und
Sonderfall in Screen 4s `module_order_keys` gelöst, nicht als Eintrag in `module_defs`).

**Live gefundener Bug (nicht vom Plan vorhergesehen):** `ir.attachment`s Inhaltsfeld heißt auf
dieser Instanz `raw`, nicht `datas` — `datas` existiert als Feld auf `ir.attachment` gar nicht
(`search_read` wirft `Invalid field 'datas'`), aber `create()` nimmt einen `datas`-Key
stillschweigend an und verwirft ihn (200 OK, aber kein Inhalt gespeichert, kein Fehler). Auch
`db_datas` (existiert laut `fields_get`) wird beim direkten Schreiben verworfen — Anhänge liegen
auf dieser Instanz im Filestore, nicht in der DB-Spalte. Nur `raw` rundet Inhalt beim
Create/Read korrekt. Erst durch den vollen Live-Suite-Lauf aufgefallen (Unit-Tests mit
gemocktem Client hätten das nie gefangen) — dokumentiert in CLAUDE.md „Verified field
gotchas". GUI-Anbindung zusätzlich headless verifiziert (echte `App`-Instanz, echte
Screen-3-/Screen-4-Widgets, `CTkCheckBox.select()`/`CTkRadioButton.select()` +
`button.cget("command")()`, kein sichtbares Fenster nötig) — kein manueller Klick-Durchlauf
im Browser/Desktop, aber Test deckt exakt den von der Peer-Review gefundenen Blocker ab.
Vollständige Test-Suite (`tests/integration/test_suite.py`): 151/151 Unit- + 61/61
Live-Integration-Schritte grün, inkl. neuer `test_documents_unit.py`/`test_documents.py`.

Sprint S7 (2026-08-05) — **abgeschlossen**, Prozessketten-Kontinuität (R8). Sprint-Reihenfolge
wurde außerhalb dieser Session neu priorisiert: R8 ist Voraussetzung für R2/R3
(Purchase+Inventory), nicht parallel dazu — S7 = R8, Purchase+Inventory rückt auf **S8** (Draft
bereits vorhanden: `/Users/paul/.claude/plans/continue-implementation-with-the-woolly-toast.md`,
vor Umsetzung gegen aktuellen Code-Stand re-verifizieren). Plan wurde **zweimal** von einem
fremden Opus-Agenten peer-reviewed (Kontext nur Plan+Live-Repo, keine Konversationshistorie,
gleiches Verfahren wie S5/S6) — nach der ersten Review erfolgte ein Nutzer-Kurswechsel: der
Erst-Entwurf markierte genau ein "Hero"-Serviceprodukt pro Lauf (5 neue `RunContext`-Felder,
manueller Reverse-Link-Workaround); Nutzer-Feedback "wenn es für ein Produkt funktioniert,
funktioniert es für alle" führte zu einer zweiten, universellen Version (angewendet auf jedes
Serviceprodukt, keine neuen `RunContext`-Felder, `sale.py`/`config.py` unangetastet — kleinerer
Diff als die Hero-Version). Volle Details, Live-Spike-Ergebnisse und das
Datenerzeugungs-Audit (auf Nutzer-Anfrage: alle `create`/`create_batch`-Aufrufstellen der 9
Module gegen native Odoo-Automatiken geprüft, `mrp.py` explizit als kollisionsfrei bestätigt)
in `ROADMAP.md`s R8-Statusblock (§4). Kern: `service_tracking`/`invoice_policy`/
`service_type` auf allen Serviceprodukten (gated auf `project`+`hr_timesheet` installiert) lässt
Odoo bei Auftragsbestätigung selbst Projekt+Aufgabe erzeugen; Zeiterfassung gegen diese
Aufgaben treibt echte Delivered-Qty-Fakturierung über den nativen
`sale.advance.payment.inv`-Wizard statt manuellem `account.move`-Nachbau.
`orchestrator.py`-Reorder (🔒, architekten-freigegeben im Plan): `account` läuft jetzt nach
`hr`/`project`/`hr_timesheet` statt davor. 157/157 Unit- + 65/65 Live-Integration-Schritte
grün, inkl. 7 neuer R8-Schritte über `test_master_data.py`/`test_sale.py`/`test_project.py`/
`test_accounting.py`.

Sprint S8 (2026-08-28) — **abgeschlossen**, Purchase + Inventory (R2/R3). Plan wurde
zweimal peer-reviewed nach dem etablierten S5-S7-Verfahren: ein Plan-Agent verifizierte den
2026-08-04-Erstentwurf komplett neu gegen den aktuellen Code-Stand (Zeilennummern,
Funktionssignaturen, live via odoo-fields MCP nachgeprüfte Odoo-Feldschemata), danach ein
zweiter, fremder Agent (nur Plan-Text + Live-Repo, keine Konversationshistorie) review'te
das Ergebnis kalt. Kern: `modules/purchase.py` (neu) — `purchase.order` → `button_confirm`
→ `action_create_invoice` (mit manuellem `account.move`-Nachbau als Fallback, gleiches
Per-Order-Isolations-Muster wie `create_invoices_from_orders` post-R8) →
`odoo_actions.post_invoices`; `modules/inventory.py` (neu) — `stock.quant` mit
`inventory_quantity` gesät, `action_apply_inventory` angewendet. `odoo_actions.py` erhielt
drei neue Exports (`create_suppliers`, `post_invoices` — beide aus `accounting.py`
herausgezogen, `accounting.py`s fünf Call-Sites entsprechend aktualisiert — sowie
`get_default_warehouse`); `config.py` additiv um `ModuleSelections.purchase`/
`purchase_confirm_pct`/`stock` und `RunContext.supplier_ids` erweitert;
`orchestrator.py`-Anhang (🔒, additiv) nach `hr_recruitment`, vor `documents`.

**Ein von der Plan-Review selbst gefundener Bug (vor Implementierung gefixt):** der
2026-08-04-Entwurf sah `ModuleSelections.stock_avg_qty: int` vor, gepaart mit
`orchestrator.py`s `module_code="stock"` — da `ModuleSelections.get(module_code)` reines
`getattr(self, module_code)` ist, hätte das Modul auf jedem Lauf still und für immer
übersprungen (kein Attribut namens `stock`). Gefixt durch dict-Form
(`stock: dict = {"avg_qty": int}`), analog zu `mrp`/`documents`.

**Zwei live gefundene Bugs (nicht vom Plan vorhergesehen, erst beim ersten echten
End-to-End-Lauf entdeckt):**
1. `RunContext.company_ids` heißt zwar "company", enthält aber trotz seines Namens
   `res.partner`-IDs (von `master_data.py` erstellte Kunden-/Firmenkontakte — z.B.
   verwendet `sale.py` `ctx.company_ids[i]` direkt als `partner_id` einer Bestellung),
   **niemals** eine echte `res.company`-ID. Der ursprüngliche Plan übernahm unkritisch
   `mrp.py`s vorhandenes Muster (`company_id = ctx.company_ids[0]`, benutzt für
   `mrp.workcenter.company_id`/`get_manufacturing_picking_type_id`) — das ist dort
   vermutlich selbst bereits ein stiller, durch breites try/except maskierter Bug (Work
   Centers/Fertigungsaufträge könnten deshalb schon länger leise übersprungen werden,
   **nicht in diesem Sprint gefixt**, siehe Backlog-Hinweis unten). Für `purchase.py`/
   `inventory.py` wäre dieser Fehler nicht harmlos maskiert gewesen, sondern hätte
   `get_default_warehouse` immer `None` liefern lassen und beide neuen Module bei jedem
   echten Lauf komplett stillschweigend übersprungen. Gefixt durch neuen
   `odoo_actions.get_main_company_id(client)`-Helper (gleiches Fallback-Muster wie
   `get_main_company_name`: erst `id=1`, dann erste gefundene `res.company`), von beiden
   neuen Modulen statt `ctx.company_ids[0]` verwendet.
2. `purchase.order.action_create_invoice` lässt `account.move.invoice_date` unbelegt
   (`False`) — das Datum ist das externe Lieferantendatum, Odoo setzt hier keinen
   Default. `action_post` scheitert dann mit "Das Datum der Rechnung/Erstattung ist
   erforderlich" (live bestätigt gegen `demo-pahu-test1.odoo.com`). Gefixt: `purchase.py`
   schreibt `invoice_date` (heute) auf die per `action_create_invoice` erzeugten Bills,
   bevor `post_invoices` aufgerufen wird — der manuelle Fallback-Pfad setzte es schon immer
   korrekt.

Alle drei live-unsicheren Methodennamen bestätigt: `purchase.order.button_confirm` (nicht
`action_confirm` nötig — Odoo-Core-Name, stabil, im Gegensatz zu `sale.order`s
`action_confirm`; dual-try trotzdem implementiert als billige Absicherung),
`purchase.order.action_create_invoice` (öffentlich, liefert die Bill über die Reverse-Link
`invoice_ids`), `stock.quant.action_apply_inventory`. `stock.quant.company_id`/`in_date`
sind laut `fields_get` als `readonly` markiert, runden aber trotzdem korrekt beim `create()`
durch (kein `ir.attachment.datas`-artiger Silent-Drop — live verifiziert).

176/176 Unit- + 70/70 Live-Integration-Schritte grün, inkl. 19 neuer S8-Schritte über
`test_purchase_unit.py`/`test_inventory_unit.py`/`test_purchase.py`/`test_inventory.py` plus
einer neuen `is_storable`-Assertion in `test_mrp_batch_unit.py`. GUI-Anbindung bewusst auf S9
verschoben (siehe Plan — `gui.py`s `WANTED_MODULES` kennt `purchase`/`stock` noch nicht,
S9-Implementierer muss das beim HTML-Frontend-Umbau nachziehen, nicht nur die
Test-Infrastruktur-`_WANTED`-Liste).

**Bekannter, nicht in diesem Sprint gefixter Backlog-Punkt:** `mrp.py:282,334` verwendet
denselben fehlerhaften `ctx.company_ids[0]`-als-`res.company`-ID-Zugriff für
`mrp.workcenter.company_id` und `get_manufacturing_picking_type_id` — durch breites
try/except maskiert (kein Crash, nur eine leise Log-Zeile), potenziell seit Einführung
bereits fehlerhaft. Nicht Teil des S8-Scopes; als möglicher Folge-Task vorgemerkt.

Sprint S9 (2026-08-28) — **abgeschlossen**, Webserver-Deployment (neues Roadmap-Item **R9**,
zusammen mit **D4** und **D7**). `gui.py` ist gelöscht, nicht parallel weitergepflegt. Der
Ausgangspunkt war ein vom Nutzer geliefertes HTML-Mockup (`demodatenkonsole.html`), das die
API-Oberfläche festlegte; das Backend wurde daraus abgeleitet. **S9 ersetzt den Aufrufer,
nicht die Pipeline** — `orchestrator.py` wurde nicht angefasst (kein `mode`-Parameter, 🔒
unberührt), weil `orchestrator.run()` seit D1 bereits GUI-frei ist und
`ModuleSelections`/`RunContext` reine Dataclasses ohne GUI-Kopplung sind.

Neu: `web/` (FastAPI-App, Guards A+B, Session-Store, Worker-Queue mit Admission Control,
SSE-Broker), `connect_service.py` + `run_config.py` (D4-Extraktion, framework-frei),
`run_journal.py` (D7), `static/index.html`+`app.js`+`app.css`, `Dockerfile`/
`docker-compose.yml`/`.env.example`. Geändert: `logging_setup.py` (lauf-gebundenes Logging
über `contextvars` + `RunIdFilter` — jedes Modul loggt über `getLogger(__name__)`, Läufe
sind also **nicht** über den Logger-Namen trennbar, und `configure_logging()` hängt beim
Import von `orchestrator.py` genau einen Handler an den **Root**-Logger),
`odoo_client.py` 🔒 (Weiterleitungen an allen drei `session.post`-Stellen abgelehnt,
Fehlerkörper an **beiden** Kopien redigiert — Log **und** `self.errors`, das die
Lauf-Zusammenfassungs-API speist), `llm_service.py` (Cache-Pfad per Env, atomarer
Cache-Write, neues öffentliches `ping()`), `requirements.txt` (gepinnt, `customtkinter`
entfernt), `config.ini.example` (vestigiales `username` entfernt).

**Bewusst gestrichen (Produktentscheidung nach Peer-Review):** Datensatz-Vorschau und
-Bearbeitung samt Backend-Gerüst — vollständige Begründung im R9-Statusblock von
`ROADMAP.md` §4. Sie wird erfahrungsgemäß erneut vorgeschlagen; die Kurzfassung
lautet: Odoo ist der bessere Datensatz-Browser, das Ziel ist eine Wegwerf-`demo-*`-DB, und
seit S7/R8 entstehen die interessantesten Datensätze nativ in Odoo und wären in einer
Vorschau **nie** sichtbar gewesen.

**S8-Carry-overs geschlossen:** `WANTED_MODULES` (jetzt in `run_config.py`, geteilt mit
`tests/integration/test_suite.py` statt dupliziert) enthält `purchase` und `stock`;
`/api/connect` liefert `feature_flags` — ohne sie wären alle MRP-Arbeitszentren,
BOM-Vorgänge und Qualitätsprüfpunkte still nie wieder erzeugt worden (B1-Fehlerklasse).

**LLM-Datenfluss — vollständig nachgezogen (2026-08-28, nach Nutzerfrage).** Jede
`fetch_*`-Aufrufstelle der neun Module wurde daraufhin geprüft, ob ein aus Odoo *gelesener*
Wert in einen Prompt gelangt. Ergebnis: **genau ein Prompt**, `modules/crm.py`s
Chatter-Prompt, und dort zwei Felder — `customer` (ein `res.partner`-Name) und `salesperson`
(ein `res.users`-Name). Alles andere ist unkritisch und soll nicht erneut untersucht werden:
`mrp.fetch_all_bom_components` baut auf `ctx.name_banks` (LLM-erzeugt),
`project.fetch_all_project_stages` auf gerade selbst angelegten Projektnamen,
`recruiting.fetch_job_summaries_batch` auf LLM-Stellentiteln,
`documents.fetch_cv_bullet_points_batch` auf Bewerbern **dieses** Laufs. Vorhandene Produkte
werden ausschließlich als IDs verwendet, nie als Text.

Kritisch wird der Chatter-Prompt erst mit `use_existing`: dann ist `customer` ein *echter
bestehender* Kontakt statt eines vom Lauf erfundenen. Der `salesperson`-Name stammt
**immer** aus `res.users` der Zielinstanz, unabhängig von der Option.

Umgesetzt: eine ausdrückliche Einwilligung in Screen 02. Ohne Antwort weisen sowohl der
Browser als auch `POST /api/runs` den Lauf ab. Ablehnen ist **kein** reiner UI-Zustand —
`ModuleSelections.crm_chatter["use_db_names"]` wird `False` und `crm.py` sendet dann
„Kunde"/„Verkäufer" statt der echten Namen. Die frühere Behauptung „genau ein Pfad, nämlich
die Branchen-Vorbefüllung" war falsch; `determine_industry_from_company_name` ist trotzdem
entfernt, weil sie als Einzige *vorhandene* Kundendaten ungefragt las.

Die allgemeine Invariante („kein Wert aus einem Datensatz, den dieser Lauf nicht selbst
erzeugt hat, erreicht einen Prompt") bleibt als automatischer Check offen — sie bräuchte
Provenienz-Verfolgung. Praktisch ist sie jetzt durch die Einwilligung plus die obige
Aufstellung abgedeckt.

237/237 Unit-Schritte grün (von 176), inkl. 4 neuer Testdateien
(`test_web_security_unit.py`, `test_web_api_unit.py`, `test_run_config_unit.py`,
`test_run_journal_unit.py`) plus neu geschriebenem `test_logging_setup.py` (die alte
Idempotenz-Prüfung zählte Root-Handler und testete damit genau das ersetzte Design).
Live-Integration um `tests/integration/test_run_journal.py` erweitert.

Sprint S10 (2026-08-29) — **abgeschlossen**, Live-Testphase-Feedback (neues Roadmap-Item
**R10**). Plan zweimal peer-reviewed, einmal pro Phase (fremder Opus-Agent, Plan+Live-Repo,
keine Konversationshistorie — gleiches Verfahren wie S5–S8; Phase A 10 Blocker + 11
Should-fix, Phase B 6 Blocker + 9 Should-fix, jeweils vor Umsetzung eingearbeitet).
Vollständiger Statusblock in `ROADMAP.md`s R10-Abschnitt; Kurzfassung hier:

`odoo_client.py` 🔒 — Fehleraufzeichnung wandert von `_post` in einen `_record_failure`-
Frame-Stack, der die öffentlichen Methoden umschließt: ein `errors`-Eintrag pro gescheiterter
*logischer Operation* statt einer pro HTTP-Versuch (die Kettenreihenfolge selbst bleibt
unverändert — siehe die zwei neuen Gotchas oben zu `has_access` und `/call/`+`/call_kw/`).
Neue `has_create_access(model)`-Methode. F8 (Payload-Form merken) bewusst **nicht** gebaut —
hätte die 🔒-Kettenreihenfolge geändert, Nutzen unbelegt; siehe R10-Statusblock.
`odoo_actions.py` — `probe_model_access`/`MODEL_ACCESS_PROBES`/`PRIMARY_MODEL_PER_MODULE`,
`get_enabled_features`s `mrp_routings`/`quality` jetzt `has_create_access`-gestützt,
`check_field_compatibility` auf `installed_modules` gegatet (spart die Mehrheit der
Connect-Anfragen). `run_config.py` — `effective_installed_modules()` filtert
`ctx.installed_modules` auf schreibbare Module, eine Funktion für Server und Frontend;
`GATE_ONLY_MODULES` (`hr_holidays`/`hr_work_entry` — probiert, beschriftet, nie eine
Fortschrittszeile). `modules/hr.py`s `create_leave_data` und `modules/documents.py`s
`create_documents` gaten zusätzlich auf `ctx.model_access`; `modules/mrp.py`s
`ctx.company_ids`-Bug (Backlog seit S8) gefixt — `get_main_company_id(client)` statt
`ctx.company_ids[0]`. `config.py` 🔒 additiv: `RunContext.model_access`/`skipped_modules`.
`web/jobs.py` neuer Modulstatus `MODULE_SKIPPED` (additiv). Frontend: neuer Checklistenschritt
„Schreibrechte", Modul-Karten zeigen „keine Schreibrechte" getrennt von „nicht installiert".

**Phase A — live bestätigt (2026-08-29, `demo-test5`, mit frischem API-Schlüssel nach
zwischenzeitlichem Ablauf des vorherigen):** `hr_holidays`/`hr_work_entry` sind auf dieser
Instanz tatsächlich `state=uninstalled` — genau der Fall, den F6 vermutete. Die neue
Sonde/das neue Gate greifen korrekt: `tests/integration/test_hr.py`s Urlaubs-Schritte
melden sauber SKIP statt eines 404-Fehlschlags (die Datei rief die low-level Helfer bisher
ungegatet auf und musste dafür selbst ein `ctx.installed_modules`-Gate bekommen). Gemerged
als [PR #12](https://github.com/pahuodoo/odoo-daten-generator/pull/12).

**Phase B** (F1–F5: Datenbankfeld weg, Weiter/Nav-Gate, Ansicht „Prüfen" streichen,
Einstiegs-Tutorial, PDF-Varianten) — Plan vor Umsetzung peer-reviewed; Kernkorrekturen: das
Gate sperrt auf `data.ok` (Odoo+LLM erreichbar), nicht auf „alle Checklistenschritte grün" —
sonst würde ein einzelner nicht-fataler roter Schritt (z. B. ein blockiertes Modul aus
Phase A) die gesamte Konsole schwärzen statt nur dieses eine Modul zu deaktivieren, was
Phase As eigenem Design widerspräche. Das Gate ist zudem ein **Latch**
(`state.everConnected`), nicht der Live-Verbindungsstatus — sonst würde ein fehlgeschlagener
Re-Connect während eines laufenden Laufs die Generierungsansicht und „Diesen Lauf löschen"
aussperren. `config.ini.example` behält `db` als optionalen Wert, weil
`tests/integration/test_suite.py`/`test_mrp_live.py` ihn bisher als hartes Dict-Subscript
lasen und sonst `KeyError` geworfen hätten. Die PDF-Varianten-Determinismus nutzt einen
lokalen `random.Random(zlib.crc32(...))`, nie `random.seed()` — Letzteres hätte den
globalen Zufallsgenerator kontaminiert und damit auch die im selben Modul später
laufende CV-PDF-Erzeugung unbeabsichtigt deterministisch gemacht (per Test verifiziert, dass
die globale `random`-Sequenz durch einen Rechnungs-PDF-Aufruf unverändert bleibt).

301/301 Unit- (von 294), 71/76 Live-Integrationsschritte grün — dieselben 5 vorbestehenden
Fehlschläge wie in Phase A (`hr.job.payment_interval` existiert auf `demo-test5` nicht,
`modules/recruiting.py` unverändert seit vor S10; als eigene Aufgabe ausgelagert, nicht Teil
von S10), keine neuen. Live per Browser verifiziert: kompletter Verbindungs→Konfiguration→
Lauf-Zyklus inkl. Live-Zusammenfassung, Nav-Sperre, Tutorial-Overlay (erscheint einmalig,
persistiert über `localStorage`, per „?"-Knopf erneut aufrufbar) und PDF-Varianten
(zwei Layouts als PDF gerendert und visuell verglichen — sichtbar unterschiedlich, gleicher
Lieferant zweimal ergab dasselbe Layout).

Nächster Sprint: offen. Backlog-Kandidaten: R6 (Multi-Country), R7 (JSON-Demo-Plan),
S5 Tier 2, Provenienz-Invariante (§R9), F8 (Payload-Form merken, siehe oben).

**`hr.job.payment_interval`-Bug — behoben (2026-08-29).** Ursprüngliche Diagnose
(Feldschema-Mismatch) war falsch — korrigierte Diagnose und Fix-Umfang siehe „Verified field
gotchas" oben. 306/306 Unit-, 79/79 Live-Integrationsschritte grün (von 71/76 — die 3
zusätzlichen Schritte sind `test_documents.py`s P1/P2/Pattern-5-Schritte, die im kaputten
Zustand durch einen frühen `return` in der Setup-Exception nie gezählt wurden). Separat,
zeitgleich in einer parallelen Session gemerged: CI-Lint-Infrastruktur (`ruff.toml`,
`.github/workflows/ci.yml`) — eigener Commit, nicht Teil dieses Fixes.

**Prozess-Hinweis (2026-08-04):** Dieser Abschnitt lag zeitweise eine ganze Session hinter dem
tatsächlichen Code-Stand zurück — D1/D2/D3/B11/B14/B15 waren bereits implementiert und getestet,
aber hier noch als "nächster Sprint" gelistet, und `ROADMAP.md` zitierte `gui.py`-
Zeilennummern, die nicht mehr existierten. Vor dem Vertrauen auf die Sprint-Status-Prosa hier:
gegen den tatsächlichen Datei-Inhalt/Zeilennummern verifizieren, nicht nur gegen diesen Text.

Neuer Backlog-Punkt seit S3-Review: R6 — Multi-Country Customer/Supplier Generation (siehe
§4 Roadmap). `static_data.py` ist bereits länderweise (DE/AT/CH) strukturiert, damit weitere
Märkte eine reine Datenergänzung sind.

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
