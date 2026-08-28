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
cd odoo-daten-generator && ODOO_GENERATOR_ACCESS_CODE=choose-a-code python3 -m uvicorn web.app:app --host localhost --port 8000
```

Then open <http://localhost:8000>.

`--host localhost` rather than `--host 127.0.0.1`: on macOS `localhost` resolves
to both `::1` and `127.0.0.1`, and Safari tries IPv6 first. Bound to IPv4 only,
the browser gets a refused connection and gives up while `curl` silently falls
back to IPv4 — so the server looks healthy from the shell and dead in the
browser. `localhost` binds both loopback families and stays off the network.
Without `ODOO_GENERATOR_ACCESS_CODE` every
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

### Letting colleagues in (Cloudflare quick tunnel)

```bash
docker compose --profile tunnel up -d --build
docker compose logs tunnel | grep trycloudflare
```

The second command prints a random `https://<random>.trycloudflare.com` address —
share that. Real TLS, no router changes, no Cloudflare account, and colleagues
install nothing. The address changes every time the tunnel restarts.

Without `--profile tunnel` nothing is exposed; `docker compose up` stays bound to
loopback as before.

Two things worth knowing before you send the link:

- The address is public. Unguessable, but the shared access code is the only
  thing in front of it — use a real one.
- With beta defaults on, every colleague who leaves the credential fields blank
  runs against *your* target with *your* Odoo key. Add
  `ODOO_GENERATOR_CONFIG_DEFAULTS=off` to `.env` if each of them should bring
  their own instance.

Running uvicorn directly instead of Compose, add `--proxy-headers
--forwarded-allow-ips '*'` so the app sees the real scheme and marks the session
cookie `Secure`.

## What you supply

Per session, in memory only, discarded on expiry (a janitor sweeps abandoned
sessions rather than waiting for someone to touch them again):

- the target Odoo URL, database and API key,
- your own LLM API key and model name.

### Beta: server-side defaults

During the beta a blank field falls back to the value in `config.ini` (or the
environment: `ODOO_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`), so a tester can
click Verbinden without pasting anything. Typing your own value always wins for
that session.

This does mean the server holds credentials of its own, and the shared access
code becomes the only thing in front of them — the opposite of the design that
made self-hosting defensible. Guard A still applies to the configured URL, so the
reach is limited to a throwaway `demo-*.odoo.com` instance. Turn it off with
`ODOO_GENERATOR_CONFIG_DEFAULTS=off` when the beta ends.

`GET /api/defaults` reports only *whether* a key default exists, never the key.

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
- **What reaches the LLM is what this run invented — with one asked-for
  exception.** The industry field is yours to type; it is no longer inferred from
  the company name. Products, amounts, dates and every other field are assembled
  in code and never sent. The exception is the CRM chatter prompt, which carries
  the customer's name and the salesperson's name so the messages address the right
  people. With the run creating its own master data those names are LLM-invented
  anyway; with "Vorhandene Daten einbeziehen" the customer is a real existing
  contact, so the UI asks before that happens. Declining sends "Kunde" and
  "Verkäufer" instead — it is a real switch, not a UI state.

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
