"""Manufacturing module: creates products, BOMs, and BOM lines.

Uses a single Gemini call for all BOM component names (batch) instead of one per product.
"""

import datetime
import random

import odoo_actions  # kept for create_product (shared with master_data module)
from config import RunContext


# ---------------------------------------------------------------------------
# Low-level MRP helpers (previously in odoo_actions.py)
# ---------------------------------------------------------------------------

def get_product_template_id(client, product_id):
    """Return the product template id for a given product variant."""
    record = client.search_read(
        'product.product', [["id", "=", product_id]], fields=["product_tmpl_id"], limit=1,
    )
    if record:
        tmpl = record[0].get("product_tmpl_id")
        if isinstance(tmpl, (list, tuple)) and tmpl:
            return tmpl[0]
        return tmpl
    return None


def create_bom(client, product_tmpl_id, product_id=None, quantity=1.0, code=None, bom_type="normal"):
    """Create a manufacturing BOM for a given product template."""
    values = {"product_tmpl_id": product_tmpl_id, "type": bom_type, "product_qty": quantity}
    if product_id:
        values["product_id"] = product_id
    if code:
        values["code"] = code
    print(f"-> Creating BOM for template {product_tmpl_id} (variant: {product_id})")
    return client.create('mrp.bom', values)


def create_bom_line(client, bom_id, product_id, quantity=1.0):
    """Create a BOM line referencing a component product."""
    values = {"bom_id": bom_id, "product_id": product_id, "product_qty": quantity}
    print(f"->   Adding BOM line: product {product_id} x{quantity}")
    return client.create('mrp.bom.line', values)


def create_workcenter(client, vals: dict) -> int:
    """Creates mrp.workcenter, returns id."""
    print(f"-> Creating Work Center: {vals.get('name')}")
    wc_id = client.create('mrp.workcenter', vals)
    print(f"   ID: {wc_id}")
    return wc_id


def create_bom_operation(client, vals: dict) -> int:
    """Creates mrp.routing.workcenter (BOM operation), returns id."""
    print(f"->   Creating BOM Operation: {vals.get('name')}")
    return client.create('mrp.routing.workcenter', vals)


def create_manufacturing_order(client, vals: dict) -> int:
    """Creates mrp.production, returns id."""
    if "date_start" not in vals:
        vals["date_start"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"-> Creating Manufacturing Order for product {vals.get('product_id')}")
    mo_id = client.create('mrp.production', vals)
    print(f"   ID: {mo_id}")
    return mo_id


def confirm_manufacturing_order(client, mo_id: int) -> bool:
    """Calls action_confirm on mrp.production, returns True on success."""
    try:
        client.call_method('mrp.production', 'action_confirm', ids=[mo_id])
        print(f"   MO {mo_id} bestaetigt.")
        return True
    except Exception as e:
        print(f"   MO {mo_id} konnte nicht bestaetigt werden: {e}")
        return False


def create_quality_point(client, vals: dict) -> int:
    """Creates quality.point, returns id."""
    print(f"-> Creating Quality Point: {vals.get('name')}")
    qp_id = client.create('quality.point', vals)
    print(f"   ID: {qp_id}")
    return qp_id


def get_manufacturing_picking_type_id(client, company_id: int):
    """Finds the Manufacturing operation type for the given company."""
    results = client.search_read(
        'stock.picking.type',
        [["code", "=", "mrp_operation"], ["company_id", "=", company_id]],
        fields=["id"], limit=1,
    )
    return results[0]["id"] if results else None


def create_mrp_data(client, gemini, ctx: RunContext) -> None:
    """Creates manufacturing products with BOMs and component lines."""
    mrp_config = ctx.module_selections.mrp
    if not isinstance(mrp_config, dict):
        return
    num_mrp_products = max(0, int(mrp_config.get("num_products", 0)))
    components_per_bom = max(1, int(mrp_config.get("components_per_bom", 1)))
    sub_boms_per_product = max(0, int(mrp_config.get("sub_boms_per_product", 0)))
    num_workcenters = max(1, int(mrp_config.get("num_workcenters", 3)))
    num_manufacturing_orders = max(0, int(mrp_config.get("num_manufacturing_orders", 0)))
    create_quality_points = bool(mrp_config.get("create_quality_points", False))
    if sub_boms_per_product > components_per_bom:
        sub_boms_per_product = components_per_bom
    if num_mrp_products <= 0:
        return

    print("\n--- MANUFACTURING: Erstelle Fertigungsprodukte und Stücklisten ---")
    product_name_bank = list(ctx.name_banks.get('product_names', []))
    industry = ctx.industry
    component_count = max(components_per_bom, sub_boms_per_product or 0, 1)

    # Build list of (product_name, list_price) for each MRP product
    main_products = []
    for idx in range(num_mrp_products):
        if product_name_bank:
            base_name = product_name_bank.pop(random.randrange(len(product_name_bank)))
        else:
            base_name = f"{industry} Baugruppe {idx + 1}"
        main_products.append(base_name)

    # Single Gemini call for all component names
    bom_components_map = {}
    if gemini and main_products:
        products_request = {name: component_count for name in main_products}
        bom_components_map = gemini.fetch_all_bom_components(
            products_request, industry, ctx.language_name
        )

    created_bom_ids = []
    bom_product_map = {}  # bom_id -> product_id, for MO creation

    for idx, main_product_name in enumerate(main_products):
        list_price = round(random.uniform(250, 1200), 2)
        standard_price = round(list_price * random.uniform(0.35, 0.65), 2)
        main_product_id = odoo_actions.create_product(client, {
            "name": main_product_name,
            "sale_ok": True,
            "purchase_ok": False,  # manufactured in-house, not purchased
            "list_price": list_price,
            "standard_price": standard_price,
            "tracking": "none",
        })
        ctx.product_ids.append(main_product_id)

        tmpl_id = get_product_template_id(client, main_product_id)
        if not tmpl_id:
            print(f"⚠️  Konnte Template für Produkt {main_product_id} nicht ermitteln — BOM übersprungen.")
            continue

        bom_id = create_bom(
            client, tmpl_id,
            product_id=main_product_id,
            quantity=1.0,
            code=f"BOM-{main_product_id}",
        )
        created_bom_ids.append(bom_id)
        bom_product_map[bom_id] = main_product_id

        # Use Gemini component names or generate fallbacks
        component_names = list(bom_components_map.get(main_product_name, []))
        while len(component_names) < component_count:
            component_names.append(f"{main_product_name} Modul {len(component_names) + 1}")

        for comp_idx, component_name in enumerate(component_names[:component_count]):
            comp_list_price = round(random.uniform(80, list_price * 0.6), 2)
            comp_standard_price = round(comp_list_price * random.uniform(0.4, 0.7), 2)
            comp_id = odoo_actions.create_product(client, {
                "name": component_name,
                "sale_ok": False,
                "purchase_ok": True,
                "list_price": comp_list_price,
                "standard_price": comp_standard_price,
                "tracking": "none",
            })
            ctx.component_ids.append(comp_id)
            create_bom_line(client, bom_id, comp_id, quantity=max(1, random.randint(1, 4)))

            # Sub-BOM for the first N components
            if comp_idx < sub_boms_per_product:
                comp_tmpl_id = get_product_template_id(client, comp_id)
                if not comp_tmpl_id:
                    continue
                sub_bom_id = create_bom(
                    client, comp_tmpl_id,
                    product_id=comp_id,
                    quantity=1.0,
                    code=f"SUB-{bom_id}-{comp_idx + 1}",
                )
                created_bom_ids.append(sub_bom_id)
                bom_product_map[sub_bom_id] = comp_id
                raw_count = max(2, min(4, components_per_bom // 2 + 1))
                for raw_idx in range(raw_count):
                    raw_name = f"{component_name} Rohteil {raw_idx + 1}"
                    raw_list = round(max(15, comp_standard_price * random.uniform(0.4, 0.9)), 2)
                    raw_std = round(raw_list * random.uniform(0.5, 0.85), 2)
                    raw_id = odoo_actions.create_product(client, {
                        "name": raw_name,
                        "sale_ok": False,
                        "purchase_ok": True,
                        "list_price": raw_list,
                        "standard_price": raw_std,
                        "tracking": "none",
                    })
                    ctx.component_ids.append(raw_id)
                    create_bom_line(
                        client, sub_bom_id, raw_id,
                        quantity=round(random.uniform(1.0, 3.0), 2)
                    )

    print(f"✅ {len(created_bom_ids)} Stücklisten für {num_mrp_products} Fertigungsprodukte erstellt.")

    # --- SECTION A: Work Centers ---
    workcenter_name_to_id: dict = {}
    wc_data: dict = {}
    mrp_routings_ok = ctx.feature_flags.get('mrp_routings', True)
    if mrp_routings_ok and num_workcenters > 0:
        try:
            print("\n--- MANUFACTURING: Erstelle Arbeitszentren ---")
            if gemini:
                wc_data = gemini.fetch_workcenter_data(industry, ctx.language_name, num_workcenters)
            if not wc_data:
                wc_data = {
                    f"{industry} Station {i+1}": {
                        "description": f"Fertigungsstation {i+1}",
                        "operations": ["Vorbereitung", "Bearbeitung", "Qualitaetskontrolle"]
                    }
                    for i in range(num_workcenters)
                }
            company_id = ctx.company_ids[0] if ctx.company_ids else None
            for seq, (wc_name, wc_info) in enumerate(list(wc_data.items())[:num_workcenters], start=1):
                slug = "".join(c for c in wc_name.upper() if c.isalnum())[:8]
                wc_vals = {
                    "name": wc_name,
                    "code": slug,
                    "sequence": seq * 10,
                    "costs_hour": round(random.uniform(45, 180), 2),
                    "time_efficiency": round(random.uniform(75, 100), 2),
                    "time_start": round(random.uniform(5, 15), 2),
                    "time_stop": round(random.uniform(5, 10), 2),
                }
                if company_id:
                    wc_vals["company_id"] = company_id
                wc_id = create_workcenter(client, wc_vals)
                workcenter_name_to_id[wc_name] = wc_id
                ctx.workcenter_ids.append(wc_id)
            print(f"Arbeitszentren erstellt: {len(workcenter_name_to_id)}")
        except Exception as e:
            print(f"Arbeitszentren konnten nicht erstellt werden: {e}")
    else:
        print("ℹ️  Work Orders nicht aktiviert — Arbeitszentren und Arbeitsgänge übersprungen.")

    # --- SECTION B: BOM Operations ---
    if workcenter_name_to_id and created_bom_ids:
        try:
            print("\n--- MANUFACTURING: Verknuepfe Arbeitsgaenge mit Stuecklisten ---")
            wc_names = list(workcenter_name_to_id.keys())
            op_count = 0
            for bom_id in created_bom_ids:
                num_ops = random.randint(2, 3)
                chosen_wcs = random.choices(wc_names, k=num_ops)
                for op_seq, wc_name in enumerate(chosen_wcs, start=1):
                    wc_id = workcenter_name_to_id[wc_name]
                    ops_list = wc_data.get(wc_name, {}).get("operations", ["Bearbeitung"])
                    op_name = random.choice(ops_list)
                    create_bom_operation(client, {
                        "name": op_name,
                        "bom_id": bom_id,
                        "workcenter_id": wc_id,
                        "sequence": op_seq * 10,
                        "time_cycle_manual": round(random.uniform(15, 90), 2),
                    })
                    op_count += 1
            print(f"Arbeitsgaenge erstellt: {op_count}")
        except Exception as e:
            print(f"Arbeitsgaenge konnten nicht erstellt werden: {e}")

    # --- SECTION C: Manufacturing Orders ---
    if num_manufacturing_orders > 0 and created_bom_ids:
        try:
            print("\n--- MANUFACTURING: Erstelle Fertigungsauftraege ---")
            company_id = ctx.company_ids[0] if ctx.company_ids else None
            picking_type_id = (
                get_manufacturing_picking_type_id(client, company_id)
                if company_id else None
            )
            if not picking_type_id:
                print("Kein Fertigungs-Vorgangstyp gefunden - Fertigungsauftraege uebersprungen.")
            else:
                confirmed_mo_ids = []
                created_mo_count = 0
                # Pre-fetch quality references once (only if needed)
                qp_team_id = None
                qp_test_type_id = None
                if create_quality_points and ctx.feature_flags.get('quality', False):
                    try:
                        teams = client.search_read('quality.alert.team', [], fields=["id"], limit=1)
                        qp_team_id = teams[0]["id"] if teams else None
                        test_types = client.search_read('quality.point.test_type', [], fields=["id"], limit=1)
                        qp_test_type_id = test_types[0]["id"] if test_types else None
                    except Exception:
                        pass  # quality module likely not installed

                for _ in range(num_manufacturing_orders):
                    bom_id = random.choice(created_bom_ids)
                    product_id = bom_product_map.get(bom_id)
                    if not product_id:
                        continue
                    try:
                        mo_id = create_manufacturing_order(client, {
                            "product_id": product_id,
                            "product_qty": float(random.randint(5, 50)),
                            "bom_id": bom_id,
                            "company_id": company_id,
                        })
                        created_mo_count += 1
                        if random.random() < 0.7:
                            if confirm_manufacturing_order(client, mo_id):
                                confirmed_mo_ids.append(mo_id)
                    except Exception as mo_e:
                        print(f"Fertigungsauftrag uebersprungen: {mo_e}")

                print(f"Fertigungsauftraege: {created_mo_count} erstellt, {len(confirmed_mo_ids)} bestaetigt.")

                # Quality Points (per BOM)
                if create_quality_points and qp_team_id and qp_test_type_id:
                    try:
                        qp_count = 0
                        for bom_id in created_bom_ids:
                            create_quality_point(client, {
                                "name": f"QP-BOM-{bom_id}",
                                "team_id": qp_team_id,
                                "picking_type_ids": [(4, picking_type_id)],
                                "company_id": company_id,
                                "test_type_id": qp_test_type_id,
                                "test_report_type": "none",
                                "bom_id": bom_id,
                            })
                            qp_count += 1
                        print(f"Qualitaetspruefpunkte erstellt: {qp_count}")
                    except Exception as qp_e:
                        print(f"Qualitaetspruefpunkte konnten nicht erstellt werden: {qp_e}")
        except Exception as e:
            print(f"Fertigungsauftraege konnten nicht erstellt werden: {e}")
