# Implement Plan
1. Read the plan file specified by the user
2. Implement each step sequentially, editing all referenced files
3. After each step, validate with this repo's own runner (there is no pytest setup here):
   `cd odoo-daten-generator && python3 tests/unit/unit_suite.py` for the fast offline pass.
   Before declaring a work package done, run the full live suite CLAUDE.md mandates:
   `cd odoo-daten-generator && python3 tests/integration/test_suite.py`
4. Do NOT exit until all steps are implemented and tests pass
5. Summarize what was done and any remaining issues
