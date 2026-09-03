"""Purchase module: purchase orders from suppliers, confirmation, vendor bills (R2).

No LLM calls — pure structure (LLM-minimalism), consistent with sale.py.

Bills are created two ways per confirmed PO: the preferred path calls Odoo's
own purchase.order.action_create_invoice (the same method the "Create Invoice"
button uses — live-verified against demo-pahu-test1.odoo.com), falling back to
a manual account.move rebuild only if that call fails for a given order. This
mirrors accounting.py's create_invoices_from_orders per-order try/fallback
shape post-S7.
"""

import datetime
import logging
import random

import data_factory
import odoo_actions
from config import RunContext
from fallback_data import FALLBACK_SUPPLIERS

logger = logging.getLogger(__name__)


def _unwrap(val):
    """Many2one fields come back as [id, name] — unwrap to just the id."""
    if isinstance(val, (list, tuple)) and val:
        return val[0]
    return val


def _confirm_purchase_orders(client, order_ids):
    """Confirms POs: batch button_confirm -> batch action_confirm -> per-id
    fallback (each id isolated so one failure doesn't abort the rest)."""
    logger.info(f"-> Confirming purchase orders: {order_ids}")
    for method in ('button_confirm', 'action_confirm'):
        try:
            client.call_method('purchase.order', method, ids=order_ids)
            return order_ids
        except Exception as e:
            logger.info(f"-> {method} (batch) failed: {e}")
    confirmed = []
    for oid in order_ids:
        ok = False
        for method in ('button_confirm', 'action_confirm'):
            try:
                client.call_method('purchase.order', method, ids=[oid])
                ok = True
                break
            except Exception as e:
                logger.warning(f"-> Confirm failed for PO {oid} via {method}: {e}")
        if ok:
            confirmed.append(oid)
    return confirmed


def _create_bills_from_pos_manual(client, order_ids):
    """Fallback for _create_bills_from_pos: manually rebuilds a vendor bill
    from PO order lines instead of using action_create_invoice."""
    if not order_ids:
        return []
    orders = client.search_read(
        'purchase.order', [["id", "in", order_ids]],
        fields=["partner_id", "order_line", "name"], limit=0,
    )
    bill_vals_list = []
    for order in orders:
        line_ids = order.get("order_line", [])
        if not line_ids:
            continue
        lines_data = client.search_read(
            'purchase.order.line', [["id", "in", line_ids]],
            fields=["product_id", "product_qty", "price_unit", "name"], limit=0,
        )
        invoice_lines = []
        for ld in lines_data:
            prod_id = _unwrap(ld.get("product_id"))
            if not prod_id:
                continue
            invoice_lines.append((0, 0, {
                "product_id": prod_id,
                "quantity": ld.get("product_qty", 1),
                "price_unit": ld.get("price_unit", 0),
            }))
        if not invoice_lines:
            continue
        bill_vals_list.append({
            "move_type": "in_invoice",
            "partner_id": _unwrap(order.get("partner_id")),
            "invoice_line_ids": invoice_lines,
            "invoice_origin": order.get("name", ""),
            "invoice_date": datetime.date.today().isoformat(),
        })
    return client.create_batch('account.move', bill_vals_list)


def _create_bills_from_pos(client, order_ids):
    """Creates vendor bills from confirmed POs. Preferred path (try first):
    purchase.order.action_create_invoice per order, isolated so one order's
    failure doesn't drag the rest into the manual fallback. Returns
    (bill_ids, counts) where counts = {"preferred": n, "fallback": n}, for
    the module's closing summary log.
    """
    bill_ids = []
    failed_order_ids = []
    today_str = datetime.date.today().isoformat()
    for oid in order_ids:
        try:
            client.call_method('purchase.order', 'action_create_invoice', ids=[oid])
            po = client.search_read(
                'purchase.order', [["id", "=", oid]], fields=["invoice_ids"], limit=1,
            )
            new_ids = (po[0].get("invoice_ids") or []) if po else []
            if new_ids:
                # action_create_invoice leaves invoice_date unset (it's the
                # vendor's own date, external to the PO) — action_post then
                # rejects the bill with "invoice date required" (live-verified
                # against demo-pahu-test1.odoo.com). Stamp it before posting,
                # same as the manual-rebuild fallback already does.
                client.write('account.move', new_ids, {'invoice_date': today_str})
            bill_ids.extend(new_ids)
        except Exception as e:
            logger.warning(f"-> action_create_invoice für PO {oid} fehlgeschlagen "
                            f"({e}) — Fallback auf manuellen Aufbau.")
            failed_order_ids.append(oid)
    counts = {"preferred": len(order_ids) - len(failed_order_ids), "fallback": 0}
    if failed_order_ids:
        fallback_ids = _create_bills_from_pos_manual(client, failed_order_ids)
        bill_ids.extend(fallback_ids)
        counts["fallback"] = len(fallback_ids)
    return bill_ids, counts


# ---------------------------------------------------------------------------
# Module entry point
# ---------------------------------------------------------------------------

def create_purchase_data(client, gemini, ctx: RunContext) -> None:
    """Creates purchase orders from suppliers, confirms a subset, and bills them."""
    num_orders = ctx.module_selections.purchase
    if num_orders <= 0:
        return
    if not ctx.component_ids or not ctx.company_ids:
        logger.info("-> Keine Komponenten/Firmen vorhanden — Purchase übersprungen")
        return

    logger.info("\n--- PURCHASE: Erstelle Bestellungen ---")

    # NOT ctx.company_ids[0] — despite the name, RunContext.company_ids holds
    # res.partner ids (customer/company contacts), never a real res.company id.
    company_id = odoo_actions.get_main_company_id(client)
    if not company_id:
        logger.warning("⚠️  Keine Firma (res.company) gefunden — Purchase übersprungen.")
        return
    warehouse = odoo_actions.get_default_warehouse(client, company_id)
    if not warehouse:
        logger.warning("⚠️  Kein Warehouse gefunden — Purchase übersprungen.")
        return

    if ctx.supplier_ids:
        supplier_ids = ctx.supplier_ids
    else:
        supplier_names = ctx.name_banks.get('supplier_names', []) or FALLBACK_SUPPLIERS
        num_suppliers = min(3, len(supplier_names))
        chosen_supplier_names = random.sample(supplier_names, k=num_suppliers)
        supplier_ids = odoo_actions.create_suppliers(client, chosen_supplier_names)
        ctx.supplier_ids.extend(supplier_ids)

    if not supplier_ids or not ctx.component_ids:
        logger.info("-> Keine Lieferanten/Komponenten — Purchase übersprungen")
        return

    company = client.search_read(
        'res.company', [["id", "=", company_id]], fields=["currency_id"], limit=1,
    )
    currency_id = _unwrap(company[0].get("currency_id")) if company else None
    if not currency_id:
        logger.warning("⚠️  Keine Währung gefunden — Purchase übersprungen.")
        return

    products = client.search_read(
        'product.product', [["id", "in", ctx.component_ids]],
        fields=["name", "standard_price", "list_price"], limit=0,
    )
    product_price_map = {}
    for p in products:
        cost = p.get("standard_price", 0)
        if not cost or cost == 0:
            cost = (p.get("list_price", 0) or 50) * 0.6
        product_price_map[p["id"]] = cost

    po_vals_list = []
    # S15/R20: flat, separate from the (0,0,dict) tuples nested in each
    # order's own "lines" below — purchase.py builds line vals per-order,
    # never as one standalone list the way expenses.py does. Collecting the
    # same dict objects here (by reference, not copy) lets
    # assign_analytic_distribution mutate them once, after the loop, and
    # have that mutation show up inside the nested tuples too.
    po_line_vals_list = []
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for i in range(num_orders):
        supplier_id = supplier_ids[i % len(supplier_ids)]
        num_lines = random.randint(1, min(3, len(ctx.component_ids)))
        chosen = random.sample(ctx.component_ids, k=num_lines)
        lines = []
        for pid in chosen:
            price = product_price_map.get(pid, random.uniform(10, 100))
            qty = random.randint(1, 5)
            line_vals = {
                "name": f"PO line {pid}",
                "product_id": pid,
                "product_qty": qty,
                "price_unit": round(price, 2),
            }
            lines.append((0, 0, line_vals))
            po_line_vals_list.append(line_vals)
        po_vals_list.append({
            "partner_id": supplier_id,
            "company_id": company_id,
            "currency_id": currency_id,
            "date_order": now_str,
            "document_tax_mode": "tax_excluded",
            "picking_type_id": warehouse["incoming_picking_type_id"],
            "order_line": lines,
        })

    analytic_sel = ctx.module_selections.analytic
    purchase_pct = int(analytic_sel.get("purchase_pct", 0)) if analytic_sel.get("enabled") else 0
    if purchase_pct > 0:
        account_ids = odoo_actions.get_or_create_analytic_accounts(client, ctx)
        data_factory.assign_analytic_distribution(po_line_vals_list, purchase_pct, account_ids)

    order_ids = client.create_batch('purchase.order', po_vals_list)
    if not order_ids:
        return

    num_confirm = round(len(order_ids) * ctx.module_selections.purchase_confirm_pct / 100)
    to_confirm = random.sample(order_ids, k=min(num_confirm, len(order_ids)))
    confirmed_ids = _confirm_purchase_orders(client, to_confirm) if to_confirm else []

    bill_ids, counts = [], {"preferred": 0, "fallback": 0}
    if confirmed_ids:
        bill_ids, counts = _create_bills_from_pos(client, confirmed_ids)
        if bill_ids:
            odoo_actions.post_invoices(client, bill_ids)
            ctx.bill_ids.extend(bill_ids)

    logger.info(
        f"✅ {len(order_ids)} Bestellungen erstellt, {len(confirmed_ids)} bestätigt, "
        f"{len(bill_ids)} Eingangsrechnungen gebucht "
        f"(action_create_invoice: {counts['preferred']}, manueller Aufbau: {counts['fallback']})."
    )
