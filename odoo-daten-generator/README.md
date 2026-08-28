# odoo-daten-generator

Generates AI-assisted demo data and writes it into a throwaway Odoo demo
instance over the JSON/2 API — contacts, products, opportunities, orders,
employees, projects, timesheets, invoices, purchase orders, stock and PDF
attachments, wired together so the records form one coherent process chain
rather than unrelated rows.

Since S9 the tool is a **web application**. The CustomTkinter desktop wizard
(`gui.py`) is retired.

## Running it locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r odoo-daten-generator/requirements.txt
```

```bash
cd odoo-daten-generator && ODOO_GENERATOR_ACCESS_CODE=choose-a-code python3 -m uvicorn web.app:app --host 127.0.0.1 --port 8000
```

Then open <http://127.0.0.1:8000>. Without `ODOO_GENERATOR_ACCESS_CODE` every
login is refused — the app never falls open.

## Running it in Docker

```bash
cp odoo-daten-generator/.env.example odoo-daten-generator/.env
```

Fill in `ODOO_GENERATOR_ACCESS_CODE`, then:

```bash
cd odoo-daten-generator && docker compose up -d --build
```

One image, two profiles (`local` / `server`). They differ only in bind address,
TLS, access code and inbound reachability — guards, queue, workers and the
pipeline are identical, so moving from a laptop to the target host is a config
change rather than a migration. See `.env.example` for every setting.

## What you supply

Nothing is stored server-side. Per session, in memory only, discarded on expiry:

- the target Odoo URL, database and API key,
- your own LLM API key and model name.

The server holds no credentials of its own, which is what makes self-hosting it
defensible.

## Guard rails

- **Only `demo-*.odoo.com` targets.** An Odoo API key carries its creator's
  rights, and consultants do have write access to customer production — the key
  alone is no protection against demo data landing in a real customer database.
  The browser checks this for fast feedback; the server validates independently
  and is the only authority.
- **The server makes the requests**, so on top of the host check: https only, no
  embedded credentials in the URL, no port override, redirects refused rather
  than followed, and HTTP error bodies reduced to their structured Odoo message
  before they reach a log or the run summary.
- **No content from the target database reaches the LLM.** The industry field is
  yours to type; it is no longer inferred from the company name.

## A run

`POST /api/runs` answers `202 {run_id}` and the work happens on a worker thread —
a full run takes 2–5 minutes, past every default gateway timeout. Progress and
the live log arrive over Server-Sent Events; the page may be reloaded and the
stream replays from where it left off.

Every record the run creates is written to a run journal
(`seeds/runs/<run_id>.json`), so "delete this run" is available afterwards.
Cleanup is best-effort by nature: Odoo refuses to unlink posted invoices, their
partners and products, invoiced timesheets and anything else under its audit
trail, and reports each refusal per model instead of aborting.

## LLM providers

Groq (primary, OpenAI-compatible endpoint) and Google Gemini (fallback). Groq
retires models without notice — `llama-3.3-70b-versatile` was gone by
2026-08-28. If the connect screen reports an empty LLM response, check
<https://console.groq.com/docs/models> and put a current model in the field.

## Tests

```bash
cd odoo-daten-generator && python3 tests/integration/test_suite.py
```

Runs the offline unit suite first, then the live integration suite against the
instance in `tests/test_config.ini` (or `config.ini`). Offline only:

```bash
cd odoo-daten-generator && python3 tests/unit/unit_suite.py
```
