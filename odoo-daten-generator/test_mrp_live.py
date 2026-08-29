"""Live test for MRP extensions: Work Centers, BOM Operations, Manufacturing Orders.
Delete this file after all steps pass.
"""
import configparser
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from odoo_client import OdooJson2Client
import odoo_actions  # kept for create_product
from modules.mrp import create_workcenter, create_bom_operation, create_manufacturing_order, confirm_manufacturing_order
from web.security import derive_database_name

cfg = configparser.ConfigParser()
cfg.read("config.ini")
url     = cfg["odoo"]["url"]
# S10/R10 (F2): "db" is optional now that the web console derives it from the URL.
db      = cfg["odoo"].get("db") or derive_database_name(url)
api_key = cfg["odoo"]["api_key"]
client = OdooJson2Client(url, db, api_key)

results = []
wc_id = None
mo_id = None

# Step 1 — Work Center
try:
    wc_id = create_workcenter(client, {
        "name": "TEST-WC-DELETE",
        "code": "TESTWC",
        "sequence": 999,
        "costs_hour": 75.0,
        "time_efficiency": 90.0,
        "time_start": 10.0,
        "time_stop": 5.0,
    })
    rec = client.search_read('mrp.workcenter', [["id", "=", wc_id]], fields=["name", "costs_hour"], limit=1)
    assert rec and rec[0]["name"] == "TEST-WC-DELETE" and rec[0]["costs_hour"] == 75.0
    results.append(("Work Center create + read-back", True, wc_id))
except Exception as e:
    results.append(("Work Center create + read-back", False, str(e)))
    wc_id = None

# Step 2 — BOM Operation
try:
    boms = client.search_read('mrp.bom', [], fields=["id"], limit=1)
    assert boms, "No BOMs found in Odoo — run main tool first"
    bom_id = boms[0]["id"]
    assert wc_id, "No work center created in Step 1"
    op_id = create_bom_operation(client, {
        "name": "TEST-OP-DELETE",
        "bom_id": bom_id,
        "workcenter_id": wc_id,
        "sequence": 10,
        "time_cycle_manual": 30.0,
    })
    op = client.search_read('mrp.routing.workcenter', [["id", "=", op_id]], fields=["workcenter_id"], limit=1)
    assert op and op[0]["workcenter_id"][0] == wc_id
    results.append(("BOM Operation create", True, op_id))
except Exception as e:
    results.append(("BOM Operation create", False, str(e)))

# Step 3 — Manufacturing Order
try:
    boms = client.search_read('mrp.bom', [], fields=["id", "product_id"], limit=1)
    assert boms, "No BOMs found in Odoo"
    bom = boms[0]
    product_id = bom["product_id"][0]
    mo_id = create_manufacturing_order(client, {
        "product_id": product_id,
        "product_qty": 1.0,
        "bom_id": bom["id"],
    })
    assert mo_id
    results.append(("Manufacturing Order create", True, mo_id))
except Exception as e:
    results.append(("Manufacturing Order create", False, str(e)))
    mo_id = None

# Step 4 — Confirm MO
try:
    assert mo_id, "No MO to confirm"
    ok = confirm_manufacturing_order(client, mo_id)
    assert ok
    results.append(("Manufacturing Order confirm", True, None))
except Exception as e:
    results.append(("Manufacturing Order confirm", False, str(e)))

# Report
print("\n--- Live Test Results ---")
all_ok = True
for label, ok, detail in results:
    status = "[OK]" if ok else "[FAIL]"
    print(f"  {status} {label}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        all_ok = False
sys.exit(0 if all_ok else 1)
