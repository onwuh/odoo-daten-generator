"""Inventory module: seeds on-hand stock via stock.quant for storable products
and MRP components (R3), plus S13's warehouse-depth features — a second
stock.warehouse (R14, quant-share only, see ROADMAP.md's R14 scope-split
note), sub-locations under the first warehouse with location-level barcodes
(R15/R16), and lot/serial tracking (R13) — and S14's R12 replenishment
rules (stock.warehouse.orderpoint), independent of quant seeding.

No LLM calls — pure structure. Independent of purchase.py's receipts (no
stock.picking validation this sprint — see ROADMAP.md R3/S8): a
purchase.order confirmation already shows up in Odoo's Inventory app as a
pending receipt natively, so no double-counting risk against the quants
seeded here.
"""

import datetime
import logging
import random

import data_factory
import odoo_actions
from config import RunContext

logger = logging.getLogger(__name__)

# S13/R13: a run-wide safety valve, not exposed in the GUI — analogous to
# data_factory.assign_barcodes' hardcoded max_attempts. Without this, a card
# with many storables + a high serial percentage + a high
# tracking_serial_max could ask for thousands of individual stock.lot +
# stock.quant pairs in one run; this bounds it regardless of the per-product
# knobs. Soft cap: once spent, every further serial product still gets the
# minimum valid serial representation (1 quant, qty 1, 1 lot) rather than 0
# — see the tracking=='serial' branch below.
_MAX_SERIAL_RECORDS_PER_RUN = 500


def create_inventory_data(client, gemini, ctx: RunContext) -> None:
    """Seeds stock.quant on-hand quantities for storable products/components,
    plus S13's optional second warehouse, sub-locations, and lot/serial
    tracking, and S14's optional replenishment rules (orderpoints)."""
    stock_config = ctx.module_selections.stock
    if not isinstance(stock_config, dict) or not stock_config:
        return
    avg_qty = max(0, int(stock_config.get("avg_qty", 0)))
    sub_locations = max(0, int(stock_config.get("sub_locations", 0)))
    second_warehouse = bool(stock_config.get("second_warehouse", False))
    orderpoints_pct = max(0, min(100, int(stock_config.get("orderpoints_pct", 0))))
    if (avg_qty <= 0 and sub_locations <= 0 and not second_warehouse
            and orderpoints_pct <= 0):
        return

    logger.info("\n--- INVENTORY: Seede Lagerbestände ---")

    # NOT ctx.company_ids[0] — despite the name, RunContext.company_ids holds
    # res.partner ids (customer/company contacts), never a real res.company id.
    company_id = odoo_actions.get_main_company_id(
        client, company_id=(ctx.res_company_ids[0] if ctx.res_company_ids else None))
    if not company_id:
        logger.warning("⚠️  Keine Firma (res.company) gefunden — Inventory übersprungen.")
        return

    # S13/R14: second warehouse first, before the default-warehouse lookup —
    # it only needs company_id, not warehouse 1, so a missing/broken default
    # warehouse (handled below) doesn't cost it (live-verified S13/WP1: a
    # minimal {name, code, company_id} create() is enough, Odoo derives the
    # rest server-side).
    location_pool = []
    if second_warehouse:
        try:
            wh2 = odoo_actions.create_second_warehouse(client, company_id)
            if wh2:
                location_pool.append(wh2["stock_location_id"])
                logger.info(f"✅ Zweites Warehouse erstellt (ID: {wh2['warehouse_id']}).")
        except Exception as e:
            logger.warning(f"⚠️  Zweites Warehouse konnte nicht erstellt werden ({e}).")

    warehouse = odoo_actions.get_default_warehouse(client, company_id)
    if not warehouse:
        logger.warning("⚠️  Kein Warehouse gefunden — Bestands-Seeding übersprungen.")
        return
    location_pool.insert(0, warehouse["stock_location_id"])

    # S13/R15+R16: sub-locations under the default warehouse's stock location.
    if sub_locations > 0:
        try:
            sub_location_ids = _create_sub_locations(client, warehouse["stock_location_id"], sub_locations)
            location_pool.extend(sub_location_ids)
            if sub_location_ids and not ctx.feature_flags.get('stock_multi_locations', True):
                logger.info(
                    "ℹ️  Hinweis: Die Odoo-Einstellung \"Lager > Konfiguration > Einstellungen > "
                    "Lagerorte\" ist deaktiviert — die erzeugten Lagerplätze sind angelegt, aber "
                    "in der Odoo-UI erst nach Aktivieren dieser Einstellung sichtbar.")
        except Exception as e:
            logger.warning(f"⚠️  Lagerplätze konnten nicht erstellt werden ({e}).")

    # S14/Befund 6: this guard is a Pattern-5 proxy for the quant/tracking
    # branch only ("does this run have any customer contacts at all") — it
    # is not a real prerequisite for orderpoints, which never read
    # ctx.company_ids (company_id comes from get_main_company_id above).
    # Must not be a `return`: an orderpoint-only run (avg_qty=0 or empty
    # company_ids, orderpoints_pct>0) must still reach the loop below.
    seed_quants = avg_qty > 0 and bool(ctx.company_ids)
    if avg_qty > 0 and not ctx.company_ids:
        logger.info("-> Keine Firmen vorhanden — Bestands-Seeding übersprungen")
    if not seed_quants and orderpoints_pct <= 0:
        return

    candidate_ids = ctx.product_ids + ctx.component_ids
    if not candidate_ids:
        logger.info("-> Keine Produkte/Komponenten vorhanden — Seeding übersprungen")
        return
    storable = client.search_read(
        'product.product',
        [["id", "in", candidate_ids], ["is_storable", "=", True]],
        fields=["id", "tracking"], limit=0,
    )
    if not storable:
        logger.info("-> Keine lagerfähigen Produkte vorhanden — Seeding übersprungen")
        return

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_ids = set(ctx.new_product_ids)
    tracking_serial_max = max(1, int(stock_config.get("tracking_serial_max", 10) or 10))
    serial_budget = _MAX_SERIAL_RECORDS_PER_RUN

    # S14/R12: orderpoints target products this run provably just created —
    # ctx.new_product_ids (master_data.py) and ctx.component_ids (mrp.py,
    # never pre-seeded from existing data, see run_config.build_context) —
    # never a use_existing/prior-run product, which could already carry a
    # stock.warehouse.orderpoint on the same (product, warehouse, location)
    # triple (live-confirmed unique constraint, S14/Befund 3).
    orderpoint_ids = new_ids | set(ctx.component_ids)
    orderpoint_min_qty = max(1, int(stock_config.get("orderpoint_min_qty", 5)))
    orderpoint_max_qty = max(1, int(stock_config.get("orderpoint_max_qty", 20)))

    quant_vals_list = []
    lot_vals_list = []
    # Groups of stock.lot vals awaiting the batch-create's returned ids,
    # keyed by how many consecutive lot_vals_list entries belong to them —
    # in the same order they were appended, so a single walk over the
    # returned ids re-associates them correctly (Pattern 8: one
    # create_batch call for lots, one for quants, regardless of N).
    groups = []
    orderpoint_vals_list = []

    for idx, p in enumerate(storable):
        pid = p["id"]
        location_id = location_pool[idx % len(location_pool)]

        if seed_quants:
            # S13/Befund 4: only products master_data.py created THIS run are
            # ever tracking-branched — pre-existing (use_existing) customer
            # products and MRP components/finished goods keep today's
            # untouched bulk-quant behaviour regardless of their real
            # tracking value.
            tracking = p.get("tracking", "none") if pid in new_ids else "none"

            if tracking == "lot":
                lot_vals_list.append({"product_id": pid, "name": f"LOT-{pid}-0000", "company_id": company_id})
                groups.append({"pid": pid, "location_id": location_id, "kind": "lot", "n": 1})
            elif tracking == "serial":
                n = random.randint(1, tracking_serial_max)
                n = min(n, max(serial_budget, 0)) or 1  # never fully skip a serial product
                serial_budget -= n
                for i in range(n):
                    lot_vals_list.append({"product_id": pid, "name": f"LOT-{pid}-{i:04d}", "company_id": company_id})
                groups.append({"pid": pid, "location_id": location_id, "kind": "serial", "n": n})
            else:
                qty = random.randint(round(avg_qty * 0.5), round(avg_qty * 1.5))
                quant_vals_list.append({
                    "product_id": pid, "location_id": location_id, "company_id": company_id,
                    "inventory_quantity": qty, "in_date": now_str,
                })

        # S14/R12: independent of quant/tracking seeding above — an
        # orderpoint-only run (seed_quants=False) still reaches this.
        if (orderpoints_pct > 0 and pid in orderpoint_ids
                and random.uniform(0, 100) < orderpoints_pct):
            orderpoint_vals_list.append({
                # warehouse_id deliberately omitted — live-confirmed Odoo
                # derives it from location_id (S14/Befund 8); no source for
                # it exists in this codebase (location_pool carries only
                # location ids, WH2's own warehouse_id never survives past
                # its own log line).
                "location_id": location_id, "product_id": pid,
                "product_min_qty": orderpoint_min_qty,
                "product_max_qty": orderpoint_max_qty,
                "trigger": "manual", "company_id": company_id,
                # name deliberately omitted — live-confirmed Odoo auto-fills
                # it via sequence (required+readonly).
            })

    if lot_vals_list and not ctx.feature_flags.get('stock_lots', True):
        logger.info(
            "ℹ️  Hinweis: Die Odoo-Einstellung \"Lager > Konfiguration > Einstellungen > Los-/"
            "Seriennummern\" ist deaktiviert — die erzeugten Chargen/Seriennummern sind angelegt, "
            "aber in der Odoo-UI erst nach Aktivieren dieser Einstellung sichtbar.")

    # S13/WP5-review: a blocked stock.lot create (ACL, untested beyond
    # demo-test5) must degrade to plain untracked bulk quants for the
    # affected products, not take down the already-assembled quant_vals_list
    # (plain "none"-tracking products queued above) along with it.
    lot_ids = None
    if lot_vals_list:
        try:
            lot_ids = client.create_batch('stock.lot', lot_vals_list)
        except Exception as e:
            logger.warning(
                f"⚠️  stock.lot-Erstellung fehlgeschlagen ({e}) — "
                f"betroffene Produkte fallen auf normale Bestände zurück.")

    cursor = 0
    for g in groups:
        if lot_ids is None:
            qty = random.randint(round(avg_qty * 0.5), round(avg_qty * 1.5))
            quant_vals_list.append({
                "product_id": g["pid"], "location_id": g["location_id"], "company_id": company_id,
                "inventory_quantity": qty, "in_date": now_str,
            })
        elif g["kind"] == "lot":
            lot_id = lot_ids[cursor]
            cursor += 1
            qty = random.randint(round(avg_qty * 0.5), round(avg_qty * 1.5))
            quant_vals_list.append({
                "product_id": g["pid"], "location_id": g["location_id"], "company_id": company_id,
                "inventory_quantity": qty, "in_date": now_str, "lot_id": lot_id,
            })
        else:  # serial: n quants of qty 1, each with its own lot
            for _ in range(g["n"]):
                lot_id = lot_ids[cursor]
                cursor += 1
                quant_vals_list.append({
                    "product_id": g["pid"], "location_id": g["location_id"], "company_id": company_id,
                    "inventory_quantity": 1, "in_date": now_str, "lot_id": lot_id,
                })

    # S14/Befund 5: quant/lot tail and orderpoint batch are two independent,
    # equally-ranked blocks — neither gates the other. The `if
    # quant_vals_list else []` guard matters even though create_batch itself
    # already no-ops on an empty list (Pattern 1): it's what makes "no call
    # at all" observable on an orderpoint-only run, not just "a call with an
    # empty result".
    quant_ids = client.create_batch('stock.quant', quant_vals_list) if quant_vals_list else []
    if quant_ids:
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

    # S14/Befund 3 (Mechanismus-Korrektur): create_batch is not atomic — a
    # 404/422 falls back to sequential per-record creates, so a genuine
    # failure here (outside the collision-proof product selection above)
    # must not propagate and retroactively taint the quants already created
    # in the block above (or vice versa).
    if orderpoint_vals_list:
        try:
            op_ids = client.create_batch('stock.warehouse.orderpoint', orderpoint_vals_list)
            logger.info(f"✅ {len(op_ids)} Nachbestellregeln erstellt.")
        except Exception as e:
            logger.warning(f"⚠️  Nachbestellregeln konnten nicht erstellt werden ({e}).")


def _create_sub_locations(client, parent_location_id, count):
    """Creates `count` internal stock.location child nodes under
    parent_location_id, each with a unique EAN-13 barcode (R16). Barcode
    dedup reads existing stock.location barcodes only — live-verified
    (S13/WP1) that stock.location and product.product barcodes are separate
    namespaces, so no cross-model dedup is needed here."""
    existing = client.search_read(
        'stock.location', [["barcode", "!=", False]], fields=["barcode"], limit=0,
    )
    existing_barcodes = {rec["barcode"] for rec in existing if rec.get("barcode")}
    vals_list = []
    for i in range(count):
        vals_list.append({
            "name": f"Regal {chr(65 + i // 10)}/Fach {i % 10 + 1}",
            "usage": "internal",
            "location_id": parent_location_id,
        })
    data_factory.assign_barcodes(vals_list, existing_barcodes)
    ids = client.create_batch('stock.location', vals_list)
    logger.info(f"✅ {len(ids)} Lagerplätze erstellt.")
    return ids
