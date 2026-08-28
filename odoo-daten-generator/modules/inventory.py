"""Inventory module: seeds on-hand stock via stock.quant for storable products
and MRP components (R3).

No LLM calls — pure structure. Independent of purchase.py's receipts (no
stock.picking validation this sprint — see IMPLEMENTIERUNGSPLAN.md R3/S8): a
purchase.order confirmation already shows up in Odoo's Inventory app as a
pending receipt natively, so no double-counting risk against the quants
seeded here.
"""

import datetime
import logging
import random

import odoo_actions
from config import RunContext

logger = logging.getLogger(__name__)


def create_inventory_data(client, gemini, ctx: RunContext) -> None:
    """Seeds stock.quant on-hand quantities for storable products/components."""
    stock_config = ctx.module_selections.stock
    if not isinstance(stock_config, dict) or not stock_config:
        return
    avg_qty = max(0, int(stock_config.get("avg_qty", 0)))
    if avg_qty <= 0:
        return
    if not ctx.company_ids:
        logger.info("-> Keine Firmen vorhanden — Inventory übersprungen")
        return

    logger.info("\n--- INVENTORY: Seede Lagerbestände ---")

    # NOT ctx.company_ids[0] — despite the name, RunContext.company_ids holds
    # res.partner ids (customer/company contacts), never a real res.company id.
    company_id = odoo_actions.get_main_company_id(client)
    if not company_id:
        logger.warning("⚠️  Keine Firma (res.company) gefunden — Inventory übersprungen.")
        return
    warehouse = odoo_actions.get_default_warehouse(client, company_id)
    if not warehouse:
        logger.warning("⚠️  Kein Warehouse gefunden — Inventory übersprungen.")
        return

    candidate_ids = ctx.product_ids + ctx.component_ids
    if not candidate_ids:
        logger.info("-> Keine Produkte/Komponenten vorhanden — Inventory übersprungen")
        return
    storable = client.search_read(
        'product.product',
        [["id", "in", candidate_ids], ["is_storable", "=", True]],
        fields=["id"], limit=0,
    )
    storable_ids = [p["id"] for p in storable]
    if not storable_ids:
        logger.info("-> Keine lagerfähigen Produkte vorhanden — Inventory übersprungen")
        return

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    quant_vals_list = []
    for pid in storable_ids:
        qty = random.randint(round(avg_qty * 0.5), round(avg_qty * 1.5))
        quant_vals_list.append({
            "product_id": pid,
            "location_id": warehouse["stock_location_id"],
            "company_id": company_id,
            "inventory_quantity": qty,
            "in_date": now_str,
        })

    quant_ids = client.create_batch('stock.quant', quant_vals_list)
    if not quant_ids:
        return

    applied = False
    try:
        client.call_method('stock.quant', 'action_apply_inventory', ids=quant_ids)
        applied = True
    except Exception as e:
        logger.warning(f"⚠️  action_apply_inventory fehlgeschlagen ({e}) — "
                        f"Quants angelegt, aber nicht angewendet.")

    logger.info(
        f"✅ {len(quant_ids)} Lagerbestände erstellt, "
        f"{'angewendet' if applied else 'NICHT angewendet'}."
    )
