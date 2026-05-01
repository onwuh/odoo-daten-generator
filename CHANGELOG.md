# Changelog

All notable changes to this project are documented here.
Format: [version] — date — summary.

---

## [Unreleased]

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
