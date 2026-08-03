"""Accounting module: invoices from orders, standalone invoices, vendor bills, bank transactions."""

import datetime
import random

from config import RunContext
from fallback_data import FALLBACK_SUPPLIERS


# ---------------------------------------------------------------------------
# Low-level accounting helpers (previously in odoo_actions.py)
# ---------------------------------------------------------------------------

def post_invoices(client, move_ids):
    print(f"-> Posting invoices: {move_ids}")
    try:
        client.call_method('account.move', 'action_post', ids=move_ids)
        return True
    except Exception as e:
        print(f"[post_invoices] Batch post failed ({e}), retrying individually...")
        success_count = 0
        for mid in move_ids:
            try:
                client.call_method('account.move', 'action_post', ids=[mid])
                success_count += 1
            except Exception as e2:
                print(f"[post_invoices] Failed to post move {mid}: {e2}")
        return success_count > 0


def create_customer_invoice(client, partner_id, line_product_ids):
    print(f"-> Creating Invoice for partner {partner_id}")
    lines = [(0, 0, {"product_id": pid, "quantity": 1}) for pid in line_product_ids]
    values = {
        "move_type": "out_invoice",
        "partner_id": partner_id,
        "invoice_line_ids": lines,
        "invoice_date": datetime.date.today().isoformat(),
    }
    return client.create('account.move', values)


def create_invoices_from_orders(client, order_ids):
    print(f"-> Creating invoices from orders: {order_ids}")
    if not order_ids:
        return []
    created_invoice_ids = []
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
                invoice_vals = {
                    "move_type": "out_invoice",
                    "partner_id": partner_id,
                    "invoice_line_ids": invoice_lines,
                    "invoice_origin": order.get("name", ""),
                    "invoice_date": datetime.date.today().isoformat(),
                }
                inv_id = client.create('account.move', invoice_vals)
                created_invoice_ids.append(inv_id)
        except Exception as e:
            print(f"-> Failed to create invoice for order {order.get('id')}: {e}")
            continue
    if created_invoice_ids:
        post_invoices(client, created_invoice_ids)
    return created_invoice_ids


def create_vendor_bill(client, supplier_id, product_ids, description_prefix="Vendor Bill"):
    print(f"-> Creating Vendor Bill for supplier {supplier_id}")
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
    values = {
        "move_type": "in_invoice",
        "partner_id": supplier_id,
        "invoice_line_ids": lines,
        "ref": description_prefix,
        "invoice_date": datetime.date.today().isoformat(),
    }
    bill_id = client.create('account.move', values)
    post_invoices(client, [bill_id])
    return bill_id


def get_or_create_bank_journal(client):
    """Get or create a bank journal for bank transactions."""
    journals = client.search_read(
        'account.journal', [["type", "=", "bank"]], fields=["id", "name"], limit=1,
    )
    if journals:
        journal_id = journals[0].get("id")
        print(f"-> Using existing bank journal: {journals[0].get('name', 'Bank')} (ID: {journal_id})")
        return journal_id
    print("-> Creating new bank journal...")
    journal_id = client.create('account.journal', {"name": "Bank", "type": "bank", "code": "BNK"})
    print(f"   Created bank journal ID: {journal_id}")
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
    print(f"\n--- ACCOUNTING: Erstelle Banktransaktionen für Rechnungen dieses Laufs ---")
    if not invoice_ids and not bill_ids:
        print("-> Keine Rechnungen aus diesem Lauf — keine Banktransaktionen")
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
        print("-> Keine gebuchten Rechnungen gefunden")
        return []
    print(f"-> Gefunden: {len(vendor_bills)} Eingangsrechnungen, {len(customer_invoices)} Ausgangsrechnungen")

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
        print(f"-> Verwende vorhandenen Bank Statement: {statement_id}")
    else:
        print("-> Erstelle neuen Bank Statement...")
        balance_start = 0.0
        balance_end_real = round(balance_start + batch_total, 2)
        statement_id = client.create('account.bank.statement', {
            "journal_id": journal_id,
            "name": f"Bank Statement {random.randint(1000, 9999)}",
            "balance_start": balance_start,
            "balance_end_real": balance_end_real,
        })
        print(f"   Erstellt: Bank Statement ID {statement_id}")

    print(f"-> Saldo: Anfang {balance_start:.2f} / Ende {balance_end_real:.2f}")

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
                    print(f"-> Banktransaktion {trans['id']}: Abweichung Betrag ({trans['amount']} vs {trans['original_amount']})")
                else:
                    print(f"-> Banktransaktion {trans['id']}: Abweichung Label ({trans['label']})")
            else:
                num_exact += 1
        except Exception as e:
            print(f"   ⚠️  Fehler beim Erstellen der Banktransaktion für Rechnung {trans['id']}: {e}")

    print(f"✅ {len(created_line_ids)} Banktransaktionen erstellt ({num_exact} exakt, {num_with_dev} mit Abweichung)")
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
        print(f"\n--- ACCOUNTING: Erstelle Kundenrechnungen aus {len(ctx.confirmed_order_ids)} bestätigten Aufträgen ---")
        ctx.invoice_ids.extend(create_invoices_from_orders(client, ctx.confirmed_order_ids))
    else:
        if not ctx.company_ids or not ctx.product_ids:
            print("⚠️  Keine Partner/Produkte — Kundenrechnungen übersprungen.")
        else:
            if 'sale' not in ctx.installed_modules:
                print("\n--- ACCOUNTING: Erstelle Kundenrechnungen (Verkauf nicht installiert) ---")
            else:
                print("\n--- ACCOUNTING: Erstelle Kundenrechnungen (keine bestätigten Aufträge) ---")
            invoice_ids = []
            for i in range(num_invoices):
                cid = ctx.company_ids[i % len(ctx.company_ids)]
                chosen = random.sample(
                    ctx.product_ids,
                    k=min(len(ctx.product_ids), random.randint(1, min(3, len(ctx.product_ids))))
                )
                inv_id = create_customer_invoice(client, cid, chosen)
                invoice_ids.append(inv_id)
            post_invoices(client, invoice_ids)
            ctx.invoice_ids.extend(invoice_ids)

    # Vendor bills — draw from component_ids (purchased parts) or fall back to product_ids
    purchase_pool = ctx.component_ids or ctx.product_ids
    if purchase_pool:
        print("\n--- ACCOUNTING: Erstelle Eingangsrechnungen ---")
        supplier_names = ctx.name_banks.get('supplier_names', []) or FALLBACK_SUPPLIERS
        num_suppliers = min(3, len(supplier_names))
        chosen_supplier_names = random.sample(supplier_names, k=num_suppliers)
        supplier_ids = []
        for sname in chosen_supplier_names:
            sid = client.create('res.partner', {"name": sname, "supplier_rank": 1})
            supplier_ids.append(sid)
        num_bills = max(10, num_invoices // 2)
        for i in range(num_bills):
            supplier_id = supplier_ids[i % len(supplier_ids)]
            chosen = random.sample(
                purchase_pool,
                k=min(len(purchase_pool), random.randint(1, min(3, len(purchase_pool))))
            )
            bill_id = create_vendor_bill(client, supplier_id, chosen, description_prefix=f"Vendor Bill {i+1}")
            ctx.bill_ids.append(bill_id)

    # Bank transactions (if requested)
    if ctx.module_selections.create_bank_transactions:
        create_bank_transactions_for_all_invoices(client, ctx.invoice_ids, ctx.bill_ids)
