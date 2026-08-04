"""Sales module: creates sale orders, confirms them, links to opportunities."""

import logging
import random

from config import RunContext

logger = logging.getLogger(__name__)

# B7 (accounting.py num_bills) is min-1; sale confirmation needs the mirror-image
# fix: was hardcoded to a fixed 5 orders regardless of how many were generated
# (200 orders -> exactly 5 confirmed). A user-facing GUI slider (like hr_timeoff's
# validate_pct) would need a new ModuleSelections field, which is a config-schema
# change gated behind architect approval — deferred; this constant fixes the
# actual defect (confirmations now scale with order count) without touching config.py.
_DEFAULT_CONFIRM_PCT = 65


# ---------------------------------------------------------------------------
# Low-level sale helpers (previously in odoo_actions.py)
# ---------------------------------------------------------------------------

def create_sale_order(client, order_data):
    """Creates a new sale order and returns its ID."""
    logger.info("-> Creating Sale Order...")
    order_id = client.create('sale.order', order_data)
    logger.info(f"   ID: {order_id}")
    return order_id


def link_order_to_opportunity(client, order_id, opportunity_id):
    logger.info(f"-> Linking Order {order_id} to Opportunity {opportunity_id}")
    return client.write('sale.order', [order_id], {"opportunity_id": opportunity_id})


def confirm_sale_orders(client, order_ids):
    logger.info(f"-> Confirming orders: {order_ids}")
    try:
        client.call_method('sale.order', 'action_confirm', ids=order_ids)
        return True
    except Exception:
        for oid in order_ids:
            logger.info(f"-> Confirm order individually: {oid}")
            client.call_method('sale.order', 'action_confirm', ids=[oid])
        return True


# ---------------------------------------------------------------------------
# Module entry point
# ---------------------------------------------------------------------------

def create_sale_data(client, gemini, ctx: RunContext) -> None:
    """Creates sale orders, confirms a subset, and moves linked opportunities to Won."""
    num_orders = ctx.module_selections.sale
    if num_orders <= 0 or not ctx.company_ids:
        return

    logger.info("\n--- SALES: Erstelle Verkaufsaufträge ---")

    # Use only sellable products
    available = client.search_read(
        'product.product',
        [["id", "in", ctx.product_ids], ["sale_ok", "=", True]],
        fields=["id"],
        limit=0,
    )
    sellable_ids = [p["id"] for p in available] or ctx.product_ids
    if not sellable_ids:
        logger.warning("⚠️  Keine verkaufbaren Produkte vorhanden — Sales übersprungen.")
        return
    logger.info(f"-> Verfügbare verkaufbare Produkte: {len(sellable_ids)}")

    order_partner_map = {}  # oid -> partner_id, for B14 partner-matched linking
    for i in range(num_orders):
        cid = ctx.company_ids[i % len(ctx.company_ids)]
        num_lines = random.randint(1, min(5, len(sellable_ids)))
        chosen = random.sample(sellable_ids, k=num_lines)
        lines = [(0, 0, {"product_id": pid, "product_uom_qty": random.randint(1, 5)}) for pid in chosen]
        oid = create_sale_order(client, {"partner_id": cid, "order_line": lines})
        ctx.order_ids.append(oid)
        order_partner_map[oid] = cid

    # Link each order to an opportunity of the SAME partner (B14) — a plain
    # zip(order_ids, opportunity_ids) matched by position, so an order for
    # customer A could end up linked to customer B's opportunity.
    if ctx.opportunity_ids:
        opp_records = client.search_read(
            'crm.lead', [["id", "in", ctx.opportunity_ids]], fields=["partner_id"], limit=0,
        )
        opps_by_partner: dict = {}
        for rec in opp_records:
            partner_val = rec.get("partner_id")
            partner_id = partner_val[0] if isinstance(partner_val, (list, tuple)) else partner_val
            if partner_id:
                opps_by_partner.setdefault(partner_id, []).append(rec["id"])

        for oid in ctx.order_ids:
            cid = order_partner_map.get(oid)
            candidates = opps_by_partner.get(cid)
            if candidates:
                opp_id = candidates.pop(0)  # round-robin: each opportunity linked at most once
                link_order_to_opportunity(client, oid, opp_id)
            # else: no opportunity for this order's partner — leave unlinked
            # rather than linking it to an unrelated customer's opportunity.

    # Confirm a subset of orders — scales with order count (B8), not a fixed 5
    num_to_confirm = max(1, round(len(ctx.order_ids) * _DEFAULT_CONFIRM_PCT / 100))
    orders_to_confirm = ctx.order_ids[:num_to_confirm]
    logger.info(f"-> Bestätige {len(orders_to_confirm)} von {len(ctx.order_ids)} Verkaufsaufträgen")
    confirm_sale_orders(client, orders_to_confirm)

    # Verify confirmation
    confirmed = client.search_read(
        'sale.order',
        [["id", "in", orders_to_confirm], ["state", "in", ["sale", "done"]]],
        fields=["id", "name"],
        limit=0,
    )
    ctx.confirmed_order_ids.extend(o["id"] for o in confirmed)
    if ctx.confirmed_order_ids:
        names = [o.get('name', str(o['id'])) for o in confirmed]
        logger.info(f"-> ✅ {len(ctx.confirmed_order_ids)} Aufträge bestätigt: {names}")
    else:
        logger.warning("-> ⚠️  Keine Aufträge konnten bestätigt werden.")

    # Move linked opportunities to Won
    if 'crm' in ctx.installed_modules and ctx.confirmed_order_ids and ctx.opportunity_ids:
        _move_won_opportunities(client, ctx)

    logger.info(f"✅ {len(ctx.order_ids)} Verkaufsaufträge erstellt.")


def _move_won_opportunities(client, ctx: RunContext) -> None:
    logger.info("--- CRM: Verschiebe Opportunities mit bestätigten Aufträgen auf 'Won' ---")
    all_stages = client.search_read('crm.stage', [], fields=["id", "name"], limit=0)
    won_stage = next((s for s in all_stages if s.get("name", "").lower() == "won"), None)
    if not won_stage:
        return
    won_stage_id = won_stage["id"]
    orders = client.search_read(
        'sale.order', [["id", "in", ctx.confirmed_order_ids]], fields=["opportunity_id"], limit=0
    )
    opp_ids = []
    for order in orders:
        opp = order.get("opportunity_id")
        opp_id = opp[0] if isinstance(opp, (list, tuple)) else opp
        if opp_id:
            opp_ids.append(opp_id)
    if opp_ids:
        client.write('crm.lead', opp_ids, {"stage_id": won_stage_id})
