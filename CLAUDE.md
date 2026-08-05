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
- `ir.attachment` binary content field is `raw`, **not** `datas` — `datas` doesn't exist as a field on this instance at all (`search_read` raises `Invalid field 'datas' on 'ir.attachment'`), yet `create()` silently accepts and drops a `datas` key instead of raising (200 OK, no content stored, no error). `db_datas` also exists in `fields_get` but writing it directly is silently dropped too (attachments are filestore-backed here, not DB-column-backed). Only `raw` round-trips actual file content on create/read.
- `product.template`/`product.product`: enabling Odoo's native "auto-create Project+Task on order confirmation" needs **three** independent fields, not two — `service_tracking='task_in_project'` (creates the Project+Task), `invoice_policy='delivery'` (invoice delivered not ordered qty), **and** `service_type='timesheet'` (the field that actually makes `sale.order.line.qty_delivered_method` become `'timesheet'`, easy to miss since it doesn't follow from the other two). Do not use the convenience field `service_policy` — it's `store: false` (a UI-only onchange helper) and may be silently dropped by `create()` on this instance, same trap class as `ir.attachment.datas`.
- `account.move.line.sale_line_ids` (m2m → `sale.order.line`) is **readonly** — cannot be set on `account.move`/`account.move.line` `create()`. The writable reverse side is `sale.order.line.invoice_lines` (m2m → `account.move.line`) — but as of Sprint S7/R8, invoicing goes through the `sale.advance.payment.inv` wizard instead, which sets this link server-side natively; no manual write needed either way.
- Invoicing a `sale.order` the way Odoo's own "Create Invoice" button does: create a `sale.advance.payment.inv` record (`advance_payment_method='delivered'`, `sale_order_ids=[(6,0,[order_id])]`), then `call_method('sale.advance.payment.inv', 'create_invoices', ids=[wizard_id])` — this public method (no leading underscore) is callable via JSON2 even though it wraps the private `sale.order._create_invoices()`. Read back the created invoice(s) via `sale.order.invoice_ids` (m2m → `account.move`) afterward — the method's own return value is an `ir.actions.act_window` dict, not usable directly. A single order with nothing currently invoiceable (e.g. a delivery-policy line with `qty_delivered=0`) makes the call raise for that order; call the wizard **per order**, not once for a whole batch, so one such order doesn't drag every other invoiceable order in the batch into a fallback with it — live-confirmed a multi-order batch does NOT raise as a whole when only some orders are empty, but a solo empty-order call does.

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
Sprint S4 aus `IMPLEMENTIERUNGSPLAN.md` abgeschlossen (2026-08-03/04): D1 (Fortschritts-Callback
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

Sprint S5 aus `IMPLEMENTIERUNGSPLAN.md` (2026-08-04) — **Tier 1 abgeschlossen, Tier 2
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
`IMPLEMENTIERUNGSPLAN.md` §4 R5-Statusblock. Nebenbefund aus dem ersten Live-Lauf der neuen
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
in `IMPLEMENTIERUNGSPLAN.md`s R8-Statusblock (§4). Kern: `service_tracking`/`invoice_policy`/
`service_type` auf allen Serviceprodukten (gated auf `project`+`hr_timesheet` installiert) lässt
Odoo bei Auftragsbestätigung selbst Projekt+Aufgabe erzeugen; Zeiterfassung gegen diese
Aufgaben treibt echte Delivered-Qty-Fakturierung über den nativen
`sale.advance.payment.inv`-Wizard statt manuellem `account.move`-Nachbau.
`orchestrator.py`-Reorder (🔒, architekten-freigegeben im Plan): `account` läuft jetzt nach
`hr`/`project`/`hr_timesheet` statt davor. 157/157 Unit- + 65/65 Live-Integration-Schritte
grün, inkl. 7 neuer R8-Schritte über `test_master_data.py`/`test_sale.py`/`test_project.py`/
`test_accounting.py`.

Nächster Sprint: S8 — Purchase + Inventory (R2/R3), siehe §4/§5 Roadmap in
`IMPLEMENTIERUNGSPLAN.md`.

**Prozess-Hinweis (2026-08-04):** Dieser Abschnitt lag zeitweise eine ganze Session hinter dem
tatsächlichen Code-Stand zurück — D1/D2/D3/B11/B14/B15 waren bereits implementiert und getestet,
aber hier noch als "nächster Sprint" gelistet, und `IMPLEMENTIERUNGSPLAN.md` zitierte `gui.py`-
Zeilennummern, die nicht mehr existierten. Vor dem Vertrauen auf die Sprint-Status-Prosa hier:
gegen den tatsächlichen Datei-Inhalt/Zeilennummern verifizieren, nicht nur gegen diesen Text.

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
