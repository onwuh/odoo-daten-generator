"""Sales module: creates sale orders, confirms them, links to opportunities."""

import random

from config import RunContext


# ---------------------------------------------------------------------------
# Low-level sale helpers (previously in odoo_actions.py)
# ---------------------------------------------------------------------------

def create_sale_order(client, order_data):
    """Creates a new sale order and returns its ID."""
    print("-> Creating Sale Order...")
    order_id = client.create('sale.order', order_data)
    print(f"   ID: {order_id}")
    return order_id


def link_order_to_opportunity(client, order_id, opportunity_id):
    print(f"-> Linking Order {order_id} to Opportunity {opportunity_id}")
    return client.write('sale.order', [order_id], {"opportunity_id": opportunity_id})


def confirm_sale_orders(client, order_ids):
    print(f"-> Confirming orders: {order_ids}")
    try:
        client.call_method('sale.order', 'action_confirm', ids=order_ids)
        return True
    except Exception:
        for oid in order_ids:
            print(f"-> Confirm order individually: {oid}")
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

    print("\n--- SALES: Erstelle Verkaufsaufträge ---")

    # Use only sellable products
    available = client.search_read(
        'product.product',
        [["id", "in", ctx.product_ids], ["sale_ok", "=", True]],
        fields=["id"],
        limit=0,
    )
    sellable_ids = [p["id"] for p in available] or ctx.product_ids
    if not sellable_ids:
        print("⚠️  Keine verkaufbaren Produkte vorhanden — Sales übersprungen.")
        return
    print(f"-> Verfügbare verkaufbare Produkte: {len(sellable_ids)}")

    for i in range(num_orders):
        cid = ctx.company_ids[i % len(ctx.company_ids)]
        num_lines = random.randint(1, min(5, len(sellable_ids)))
        chosen = random.sample(sellable_ids, k=num_lines)
        lines = [(0, 0, {"product_id": pid, "product_uom_qty": random.randint(1, 5)}) for pid in chosen]
        oid = create_sale_order(client, {"partner_id": cid, "order_line": lines})
        ctx.order_ids.append(oid)

    # Link first N opportunities to first N orders
    for oid, opp_id in zip(ctx.order_ids, ctx.opportunity_ids):
        link_order_to_opportunity(client, oid, opp_id)

    # Confirm a subset of orders
    orders_to_confirm = ctx.order_ids[:max(1, min(5, len(ctx.order_ids)))]
    print(f"-> Bestätige {len(orders_to_confirm)} von {len(ctx.order_ids)} Verkaufsaufträgen")
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
        print(f"-> ✅ {len(ctx.confirmed_order_ids)} Aufträge bestätigt: {names}")
    else:
        print("-> ⚠️  Keine Aufträge konnten bestätigt werden.")

    # Move linked opportunities to Won
    if 'crm' in ctx.installed_modules and ctx.confirmed_order_ids and ctx.opportunity_ids:
        _move_won_opportunities(client, ctx)

    print(f"✅ {len(ctx.order_ids)} Verkaufsaufträge erstellt.")


def _move_won_opportunities(client, ctx: RunContext) -> None:
    print("--- CRM: Verschiebe Opportunities mit bestätigten Aufträgen auf 'Won' ---")
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
