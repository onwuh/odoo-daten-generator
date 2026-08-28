"""Accounting module: invoices from orders, standalone invoices, vendor bills, bank transactions."""

import logging
import datetime
import random

import odoo_actions
from config import RunContext
from fallback_data import FALLBACK_SUPPLIERS

logger = logging.getLogger(__name__)


def create_customer_invoice(client, partner_id, line_product_ids):
    logger.info(f"-> Creating Invoice for partner {partner_id}")
    lines = [(0, 0, {"product_id": pid, "quantity": 1}) for pid in line_product_ids]
    values = {
        "move_type": "out_invoice",
        "partner_id": partner_id,
        "invoice_line_ids": lines,
        "invoice_date": datetime.date.today().isoformat(),
    }
    return client.create('account.move', values)


def create_invoices_from_orders(client, order_ids):
    """Invoices confirmed orders via Odoo's own sale.advance.payment.inv wizard
    (R8) — the same mechanism the "Create Invoice" button uses — so each
    line's quantity respects its own invoice_policy (delivered vs. ordered)
    and the sale_line_ids reverse link is set natively, instead of this
    codebase re-deriving those rules by hand. Called once per order (not once
    for the whole batch): an order with nothing left to invoice yet (e.g. a
    delivery-policy line with zero qty_delivered) makes the wizard raise for
    that order alone — per-order isolation means one such order doesn't drag
    every other, perfectly invoiceable order into the manual fallback with it.
    """
    logger.info(f"-> Creating invoices from orders: {order_ids}")
    if not order_ids:
        return []
    invoice_ids = []
    failed_order_ids = []
    for oid in order_ids:
        try:
            wizard_id = client.create('sale.advance.payment.inv', {
                'advance_payment_method': 'delivered',
                'sale_order_ids': [(6, 0, [oid])],
            })
            client.call_method(
                'sale.advance.payment.inv', 'create_invoices', ids=[wizard_id],
                context={'active_model': 'sale.order', 'active_ids': [oid]},
            )
            order_rec = client.search_read(
                'sale.order', [['id', '=', oid]], fields=['invoice_ids'], limit=1,
            )
            new_ids = (order_rec[0].get('invoice_ids') or []) if order_rec else []
            if new_ids:
                invoice_ids.extend(new_ids)
            else:
                logger.info(f"-> Auftrag {oid}: nichts zu fakturieren (noch keine "
                            f"gelieferte Menge) — kein Rechnungsversuch nötig.")
        except Exception as e:
            logger.warning(f"-> Wizard-Rechnungserstellung für Auftrag {oid} "
                            f"fehlgeschlagen ({e}) — Fallback auf manuellen Aufbau.")
            failed_order_ids.append(oid)
    if invoice_ids:
        odoo_actions.post_invoices(client, invoice_ids)
    if failed_order_ids:
        invoice_ids.extend(_create_invoices_from_orders_manual(client, failed_order_ids))
    return invoice_ids


def _create_invoices_from_orders_manual(client, order_ids):
    """Fallback for create_invoices_from_orders: manually rebuilds invoice
    lines from order data instead of using the native wizard. Used only when
    the wizard raises for a given order (see create_invoices_from_orders)."""
    if not order_ids:
        return []
    invoice_vals_list = []
    orders = client.search_read(
        'sale.order', [["id", "in", order_ids]],
        fields=["partner_id", "order_line", "name"], limit=0,
    )
    for order in orders:
        try:
            invoice_lines = []
            line_ids = order.get("order_line", [])
            if isinstance(line_ids, list) and line_ids:
                line_data_list = client.search_read(
                    'sale.order.line', [["id", "in", line_ids]],
                    fields=["product_id", "product_uom_qty", "price_unit"], limit=0,
                )
                for ld in line_data_list:
                    prod_id = ld.get("product_id")
                    if isinstance(prod_id, (list, tuple)) and len(prod_id) > 0:
                        prod_id = prod_id[0]
                    elif not prod_id:
                        continue
                    invoice_lines.append((0, 0, {
                        "product_id": prod_id,
                        "quantity": ld.get("product_uom_qty", 1),
                        "price_unit": ld.get("price_unit", 0),
                    }))
            if invoice_lines:
                partner_id = order.get("partner_id")
                if isinstance(partner_id, (list, tuple)) and len(partner_id) > 0:
                    partner_id = partner_id[0]
                invoice_vals_list.append({
                    "move_type": "out_invoice",
                    "partner_id": partner_id,
                    "invoice_line_ids": invoice_lines,
                    "invoice_origin": order.get("name", ""),
                    "invoice_date": datetime.date.today().isoformat(),
                })
        except Exception as e:
            logger.warning(f"-> Failed to build invoice for order {order.get('id')}: {e}")
            continue
    created_invoice_ids = client.create_batch('account.move', invoice_vals_list)
    if created_invoice_ids:
        odoo_actions.post_invoices(client, created_invoice_ids)
    return created_invoice_ids


def _vendor_bill_vals(client, supplier_id, product_ids, description_prefix="Vendor Bill"):
    """Build vals for a single vendor bill (account.move, move_type=in_invoice).

    Pure vals-building, no create/post — shared by create_vendor_bill (single
    record) and the D3 batch path in create_accounting_data.
    """
    products = client.search_read(
        'product.product', [["id", "in", product_ids]],
        fields=["standard_price", "list_price"], limit=0,
    )
    product_price_map = {}
    for p in products:
        prod_id = p.get("id")
        cost = p.get("standard_price", 0)
        if not cost or cost == 0:
            cost = (p.get("list_price", 0) or 50) * 0.6
        product_price_map[prod_id] = cost
    lines = []
    for pid in product_ids:
        price = product_price_map.get(pid, random.uniform(10, 100))
        qty = random.randint(1, 5)
        lines.append((0, 0, {"product_id": pid, "quantity": qty, "price_unit": round(price, 2)}))
    return {
        "move_type": "in_invoice",
        "partner_id": supplier_id,
        "invoice_line_ids": lines,
        "ref": description_prefix,
        "invoice_date": datetime.date.today().isoformat(),
    }


def create_vendor_bill(client, supplier_id, product_ids, description_prefix="Vendor Bill"):
    logger.info(f"-> Creating Vendor Bill for supplier {supplier_id}")
    values = _vendor_bill_vals(client, supplier_id, product_ids, description_prefix)
    bill_id = client.create('account.move', values)
    odoo_actions.post_invoices(client, [bill_id])
    return bill_id


def get_or_create_bank_journal(client):
    """Get or create a bank journal for bank transactions."""
    journals = client.search_read(
        'account.journal', [["type", "=", "bank"]], fields=["id", "name"], limit=1,
    )
    if journals:
        journal_id = journals[0].get("id")
        logger.info(f"-> Using existing bank journal: {journals[0].get('name', 'Bank')} (ID: {journal_id})")
        return journal_id
    logger.info("-> Creating new bank journal...")
    journal_id = client.create('account.journal', {"name": "Bank", "type": "bank", "code": "BNK"})
    logger.info(f"   Created bank journal ID: {journal_id}")
    return journal_id


def _introduce_typo(label: str) -> str:
    """Swap two adjacent chars in the label to simulate a typo."""
    if len(label) < 3:
        return label
    i = random.randint(0, len(label) - 2)
    chars = list(label)
    chars[i], chars[i + 1] = chars[i + 1], chars[i]
    return "".join(chars)


def create_bank_transactions_for_all_invoices(client, invoice_ids, bill_ids):
    """Create bank transactions for this run's vendor bills and customer invoices.

    Scoped to invoice_ids/bill_ids (created this run) rather than scanning every
    posted move in the DB — otherwise a second generator run re-creates
    transactions for every prior invoice too (B4). The posted-state filter stays
    alongside the id filter: vendor bills can still be in draft if posting failed,
    and those must not be pulled into reconciliation.

    Vendor bills: exact match, negative amounts (outgoing payments).
    Customer invoices: 80% exact, 20% with label typo OR amount deviation (±5-20%).
    """
    logger.info(f"\n--- ACCOUNTING: Erstelle Banktransaktionen für Rechnungen dieses Laufs ---")
    if not invoice_ids and not bill_ids:
        logger.info("-> Keine Rechnungen aus diesem Lauf — keine Banktransaktionen")
        return []
    journal_id = get_or_create_bank_journal(client)

    vendor_bills = client.search_read(
        'account.move',
        [["id", "in", bill_ids], ["move_type", "=", "in_invoice"], ["state", "=", "posted"]],
        fields=["id", "amount_total", "name", "partner_id"], limit=0,
    ) if bill_ids else []
    customer_invoices = client.search_read(
        'account.move',
        [["id", "in", invoice_ids], ["move_type", "=", "out_invoice"], ["state", "=", "posted"]],
        fields=["id", "amount_total", "name", "partner_id"], limit=0,
    ) if invoice_ids else []

    total_invoices = len(vendor_bills) + len(customer_invoices)
    if total_invoices == 0:
        logger.info("-> Keine gebuchten Rechnungen gefunden")
        return []
    logger.info(f"-> Gefunden: {len(vendor_bills)} Eingangsrechnungen, {len(customer_invoices)} Ausgangsrechnungen")

    transactions_to_create = []

    for bill in vendor_bills:
        bill_id = bill.get("id")
        amount_total = bill.get("amount_total", 0)
        label = bill.get("name") or f"BILL-{bill_id}"
        partner_id = bill.get("partner_id")
        if isinstance(partner_id, (list, tuple)) and len(partner_id) > 0:
            partner_id = partner_id[0]
        transactions_to_create.append({
            "type": "in_invoice", "id": bill_id, "amount": -amount_total,
            "label": label, "partner_id": partner_id, "has_deviation": False,
        })

    num_out_invoices = len(customer_invoices)
    num_with_deviation = max(1, int(num_out_invoices * 0.2)) if num_out_invoices > 0 else 0
    deviation_indices = (
        set(random.sample(range(num_out_invoices), num_with_deviation))
        if num_out_invoices > 0 else set()
    )

    for idx, invoice in enumerate(customer_invoices):
        invoice_id = invoice.get("id")
        amount_total = invoice.get("amount_total", 0)
        ref = invoice.get("name") or f"INV-{invoice_id}"
        partner_id = invoice.get("partner_id")
        if isinstance(partner_id, (list, tuple)) and len(partner_id) > 0:
            partner_id = partner_id[0]
        has_deviation = idx in deviation_indices
        if has_deviation:
            deviation_type = random.choice(["label", "amount"])
            if deviation_type == "label":
                transaction_label = _introduce_typo(ref)
                transaction_amount = amount_total
            else:
                transaction_label = ref
                transaction_amount = round(amount_total * random.uniform(0.8, 1.2), 2)
        else:
            transaction_label = ref
            transaction_amount = amount_total
        transactions_to_create.append({
            "type": "out_invoice", "id": invoice_id, "amount": transaction_amount,
            "label": transaction_label, "partner_id": partner_id,
            "has_deviation": has_deviation,
            "original_amount": amount_total if has_deviation else None,
        })

    random.shuffle(transactions_to_create)

    batch_total = round(sum(t["amount"] for t in transactions_to_create), 2)

    statements = client.search_read(
        'account.bank.statement', [["journal_id", "=", journal_id]], fields=["id", "balance_start", "balance_end_real"], limit=1,
    )
    if statements:
        statement_id = statements[0].get("id")
        balance_start = statements[0].get("balance_start", 0.0) or 0.0
        prior_end = statements[0].get("balance_end_real", 0.0) or 0.0
        balance_end_real = round(prior_end + batch_total, 2)
        # balance_start is left untouched — the statement already has lines
        # from a prior run, and resetting it would desync the running balance (B4).
        client.write('account.bank.statement', [statement_id], {
            "balance_end_real": balance_end_real,
        })
        logger.info(f"-> Verwende vorhandenen Bank Statement: {statement_id}")
    else:
        logger.info("-> Erstelle neuen Bank Statement...")
        balance_start = 0.0
        balance_end_real = round(balance_start + batch_total, 2)
        statement_id = client.create('account.bank.statement', {
            "journal_id": journal_id,
            "name": f"Bank Statement {random.randint(1000, 9999)}",
            "balance_start": balance_start,
            "balance_end_real": balance_end_real,
        })
        logger.info(f"   Erstellt: Bank Statement ID {statement_id}")

    logger.info(f"-> Saldo: Anfang {balance_start:.2f} / Ende {balance_end_real:.2f}")

    created_line_ids = []
    num_exact = num_with_dev = 0
    for trans in transactions_to_create:
        line_values = {
            "statement_id": statement_id,
            "journal_id": journal_id,
            "payment_ref": trans["label"],
            "amount": trans["amount"],
            "partner_id": trans["partner_id"],
        }
        try:
            line_id = client.create('account.bank.statement.line', line_values)
            created_line_ids.append(line_id)
            if trans["has_deviation"]:
                num_with_dev += 1
                if trans.get("original_amount") and trans["original_amount"] != trans["amount"]:
                    logger.info(f"-> Banktransaktion {trans['id']}: Abweichung Betrag ({trans['amount']} vs {trans['original_amount']})")
                else:
                    logger.info(f"-> Banktransaktion {trans['id']}: Abweichung Label ({trans['label']})")
            else:
                num_exact += 1
        except Exception as e:
            logger.warning(f"   ⚠️  Fehler beim Erstellen der Banktransaktion für Rechnung {trans['id']}: {e}")

    logger.info(f"✅ {len(created_line_ids)} Banktransaktionen erstellt ({num_exact} exakt, {num_with_dev} mit Abweichung)")
    return created_line_ids


# ---------------------------------------------------------------------------
# Module entry point
# ---------------------------------------------------------------------------

def create_accounting_data(client, gemini, ctx: RunContext) -> None:
    """Creates customer invoices (from orders or standalone) and vendor bills."""
    num_invoices = ctx.module_selections.account
    if num_invoices <= 0:
        return

    # Create customer invoices from confirmed sale orders when possible
    if 'sale' in ctx.installed_modules and ctx.confirmed_order_ids:
        logger.info(f"\n--- ACCOUNTING: Erstelle Kundenrechnungen aus {len(ctx.confirmed_order_ids)} bestätigten Aufträgen ---")
        ctx.invoice_ids.extend(create_invoices_from_orders(client, ctx.confirmed_order_ids))
    else:
        if not ctx.company_ids or not ctx.product_ids:
            logger.warning("⚠️  Keine Partner/Produkte — Kundenrechnungen übersprungen.")
        else:
            if 'sale' not in ctx.installed_modules:
                logger.info("\n--- ACCOUNTING: Erstelle Kundenrechnungen (Verkauf nicht installiert) ---")
            else:
                logger.info("\n--- ACCOUNTING: Erstelle Kundenrechnungen (keine bestätigten Aufträge) ---")
            invoice_vals_list = []
            for i in range(num_invoices):
                cid = ctx.company_ids[i % len(ctx.company_ids)]
                chosen = random.sample(
                    ctx.product_ids,
                    k=min(len(ctx.product_ids), random.randint(1, min(3, len(ctx.product_ids))))
                )
                lines = [(0, 0, {"product_id": pid, "quantity": 1}) for pid in chosen]
                invoice_vals_list.append({
                    "move_type": "out_invoice",
                    "partner_id": cid,
                    "invoice_line_ids": lines,
                    "invoice_date": datetime.date.today().isoformat(),
                })
            invoice_ids = client.create_batch('account.move', invoice_vals_list)
            if invoice_ids:
                odoo_actions.post_invoices(client, invoice_ids)
            ctx.invoice_ids.extend(invoice_ids)

    # Vendor bills — draw from component_ids (purchased parts) or fall back to product_ids
    purchase_pool = ctx.component_ids or ctx.product_ids
    num_bills = ctx.module_selections.account_bills
    if num_bills is None:
        num_bills = max(1, num_invoices // 2)
    if purchase_pool and num_bills > 0:
        logger.info("\n--- ACCOUNTING: Erstelle Eingangsrechnungen ---")
        supplier_names = ctx.name_banks.get('supplier_names', []) or FALLBACK_SUPPLIERS
        num_suppliers = min(3, len(supplier_names))
        chosen_supplier_names = random.sample(supplier_names, k=num_suppliers)
        supplier_ids = odoo_actions.create_suppliers(client, chosen_supplier_names)
        ctx.supplier_ids.extend(supplier_ids)
        bill_vals_list = []
        for i in range(num_bills):
            supplier_id = supplier_ids[i % len(supplier_ids)]
            chosen = random.sample(
                purchase_pool,
                k=min(len(purchase_pool), random.randint(1, min(3, len(purchase_pool))))
            )
            bill_vals_list.append(
                _vendor_bill_vals(client, supplier_id, chosen, description_prefix=f"Vendor Bill {i+1}")
            )
        bill_ids = client.create_batch('account.move', bill_vals_list)
        ctx.bill_ids.extend(bill_ids)
        if bill_ids:
            odoo_actions.post_invoices(client, bill_ids)

    # Bank transactions (if requested)
    if ctx.module_selections.create_bank_transactions:
        create_bank_transactions_for_all_invoices(client, ctx.invoice_ids, ctx.bill_ids)
