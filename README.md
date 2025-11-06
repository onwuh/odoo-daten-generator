# odoo-daten-generator
Features:
Create Contacts with Subcontacts
Create Products
Create Quotations with customer and Product data

Setup:
- Set environment variables:
  - `GEMINI_API_KEY`
  - `ODOO_API_KEY` (Odoo 19 JSON 2 API key). If not set, you'll be prompted.
- `config.ini` must include `url`, `db`, and `username` in section `[odoo]`.

Runtime prompts:
- The program now asks for Odoo URL, database, username, and the Odoo API key.
- It also asks for the Google Gemini API key.
- If values exist in `config.ini` or environment variables, they are used as defaults or can be overridden interactively.
