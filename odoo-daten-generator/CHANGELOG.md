# Changelog

All notable changes to this project are documented here.
Format: [version] — date — summary.

---

## [Unreleased]

### Sprint S9 — Webserver-Deployment (2026-08-28)

Replaces the CustomTkinter desktop wizard with a web application. The pipeline
itself is untouched: S9 replaces the caller, not `orchestrator.py`.

#### Added
- `web/` — FastAPI app (`app.py`), Guards A+B (`security.py`), memory-only
  session store (`session.py`), worker queue with admission control (`jobs.py`),
  per-run SSE broker (`sse.py`).
- `connect_service.py` / `run_config.py` — the connect checklist and the
  payload→config mapping extracted out of `gui.py` (backlog item **D4**),
  framework-free and shared by the web layer and the tests.
- `run_journal.py` — per-run journal of every `(model, id)` created, plus a
  best-effort `delete_run()` cleanup (backlog item **D7**). Odoo refuses to
  unlink posted documents and their references; each refusal is reported per
  model rather than aborting the rest.
- `static/` — frontend split into `index.html` / `app.js` / `app.css`, a CSP
  precondition rather than a nicety. Dynamic values render via `textContent`
  throughout.
- Pre-flight summary view: target, active modules and per-group record counts,
  derived arithmetically from the config.
- `Dockerfile`, `docker-compose.yml`, `.env.example` — one image, `local` and
  `server` profiles differing only in bind address, TLS, access code and inbound.
- Web-layer tests in `unit_suite.py` (guards matrix, CSRF, session isolation,
  admission control, redaction, cache atomicity, rate-limit backoff, session and
  run expiry) and a live `test_run_journal.py`.
- Janitor task expiring abandoned sessions (and their in-memory credentials),
  finished run records with their event streams, and run journals past
  `ODOO_GENERATOR_LOG_RETENTION_DAYS`. Without it "credentials are discarded on
  expiry" was only true for a session someone touched again.

#### Changed
- `logging_setup.py` — run-scoped logging via a `contextvars` run id plus a
  `RunIdFilter`. Every module logs through `getLogger(__name__)`, so concurrent
  runs could not be separated by logger name and shared one root handler.
- `odoo_client.py` — redirects refused at all three `session.post` call sites
  (`requests.Session` has no `allow_redirects` attribute, so this must be a
  per-request kwarg), and HTTP error bodies reduced to their structured Odoo
  message before they reach the log **or** the durable `self.errors` copy that
  feeds the run summary. Odoo's `debug` traceback field is dropped.
- `llm_service.py` — cache directory env-configurable (the container runs a
  read-only rootfs), cache writes atomic via `os.replace`, new public `ping()`.
- `requirements.txt` pinned; `customtkinter` dropped with the desktop wizard.
- `config.ini.example` — vestigial `username` removed;
  `OdooJson2Client.__init__` never took one.
- `WANTED_MODULES` now includes `purchase` and `stock`, and is shared with the
  integration harness. S8 shipped both modules backend-only and they were
  unreachable from a real run until now.
- Default LLM model → `qwen/qwen3.8-27b`. Groq retired
  `llama-3.3-70b-versatile`; the previous default 404s.
- `odoo_client._send()` retries a 429/503 with bounded exponential backoff. The
  demo SaaS instance sustains roughly 1 req/s; batching stays the primary
  mitigation (this is why `create_batch` and test Pattern 8 exist) and the
  backoff only covers what batching cannot remove.
- `POST /api/runs` rejects `skip_master_data` without `use_existing`. The browser
  already prevented the combination; via the API it produced a run where every
  module hit its Pattern-5 skip path.

#### Fixed
- A failed seed-cache write no longer aborts the run. The cache saves an LLM call
  on a repeat run and nothing else, so an unwritable directory now degrades to
  "no caching" with a single warning. Found on the first real local run:
  `[Errno 30] Read-only file system: '/data'` killed a run that had already paid
  for its LLM data.
- `.env.example` no longer carries the container-only `/data/seeds/...` paths.
  It is the file both profiles copy, so a local `uvicorn web.app:app` inherited
  a directory that exists only inside the container. `docker-compose.yml` sets
  those two variables itself now.
- Startup probes both writable directories and names the offending environment
  variable. Previously a wrong path surfaced minutes into a run as a bare OSError,
  and a wrong journal path disabled cleanup with no message at all.
- Frontend visibility moved from inline `style="display:none"` to an `.is-hidden`
  class. Under `style-src 'self'` the browser ignores parsed style attributes, so
  every panel meant to start hidden rendered — including the Odoo credential form
  before login.
- `.app-shell` constrained to the viewport and `min-height: 0` added to `.main`,
  so `.view-scroll`'s `overflow-y: auto` has something to scroll instead of the
  whole document growing (rail and footer included).

#### Added (beta)
- Operator-supplied connection defaults (`server_config.py`): a blank connect
  field falls back to `config.ini` or the environment, so a beta tester can
  connect without pasting four values. A typed value always wins.
  `GET /api/defaults` reports whether a key default exists, never the key, and
  Guard A validates the configured URL exactly like a typed one. Disable with
  `ODOO_GENERATOR_CONFIG_DEFAULTS=off` — the server holds its own credentials
  while this is on, which inverts the "server holds no secrets" premise.

#### Added (consent)
- Explicit consent before existing database records are included. "Vorhandene
  Daten einbeziehen" now opens an inline Zustimmen/Ablehnen prompt; proceeding
  without an answer is refused by the browser *and* by `POST /api/runs`.
  Declining is not merely a UI state: `crm.py`'s chatter prompt then receives
  "Kunde"/"Verkäufer" instead of the real `res.partner` and `res.users` names.
  That prompt is the only place a value read out of the target database reaches
  an LLM — verified by tracing every `fetch_*` call site; `mrp`, `project`,
  `recruiting` and `documents` all build their prompts from LLM-generated or
  run-created values, and existing products are used as IDs only, never as text.

#### Removed
- `gui.py` and the whole CustomTkinter wizard.
- `LLMService.determine_industry_from_company_name` — it read the company name
  out of the target database and embedded it verbatim in a prompt, the only path
  that sent pre-existing customer content to a third party. The industry field
  stays, user-supplied.

### Added
- Chatter v2: rich email conversations in CRM opportunities
  - Three conversation styles: notes only / mixed / full email
  - Customer ↔ salesperson dialogue (LLM-generated, realistic arc)
  - Employees as message authors (author_id override via API)
  - Messages per opportunity configurable in GUI (2–8)
- Multi-salesperson assignment: opportunities distributed across internal users
- `config.ini.example` template so API keys are never committed

### Changed
- `crm_chatter` field in `ModuleSelections` changed from `bool` to `dict`
  (shape: `{"enabled": bool, "style": str, "messages_per_opp": int}`)
- CRM chatter GUI: replaced simple checkbox with style selector + count slider

### Removed
- CLI entry point (`connect.py`, `cli.py`) — GUI only from here on
- `questionary` dependency

---

## [1.0.0] — 2026-04-29

Initial stable release. Full pipeline operational:
- Stammdaten (partners, products)
- CRM (opportunities, leads, chatter notes, activities)
- Sales orders
- Accounting (invoices, bank transactions)
- HR (employees, time-off)
- Projects + timesheets
- Manufacturing (BOM, work orders, quality points)
- Recruiting (jobs, candidates, skills)
