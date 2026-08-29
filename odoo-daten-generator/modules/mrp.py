"""Manufacturing module: creates products, BOMs, and BOM lines.

Uses a single Gemini call for all BOM component names (batch) instead of one per product.
"""

import logging
import datetime
import random

import odoo_actions  # kept for create_product (shared with master_data module)
from config import RunContext

logger = logging.getLogger(__name__)


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
    logger.info(f"-> Creating BOM for template {product_tmpl_id} (variant: {product_id})")
    return client.create('mrp.bom', values)


def create_bom_line(client, bom_id, product_id, quantity=1.0):
    """Create a BOM line referencing a component product."""
    values = {"bom_id": bom_id, "product_id": product_id, "product_qty": quantity}
    logger.info(f"->   Adding BOM line: product {product_id} x{quantity}")
    return client.create('mrp.bom.line', values)


def create_workcenter(client, vals: dict) -> int:
    """Creates mrp.workcenter, returns id."""
    logger.info(f"-> Creating Work Center: {vals.get('name')}")
    wc_id = client.create('mrp.workcenter', vals)
    logger.info(f"   ID: {wc_id}")
    return wc_id


def create_bom_operation(client, vals: dict) -> int:
    """Creates mrp.routing.workcenter (BOM operation), returns id."""
    logger.info(f"->   Creating BOM Operation: {vals.get('name')}")
    return client.create('mrp.routing.workcenter', vals)


def create_manufacturing_order(client, vals: dict) -> int:
    """Creates mrp.production, returns id."""
    if "date_start" not in vals:
        vals["date_start"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"-> Creating Manufacturing Order for product {vals.get('product_id')}")
    mo_id = client.create('mrp.production', vals)
    logger.info(f"   ID: {mo_id}")
    return mo_id


def confirm_manufacturing_order(client, mo_id: int) -> bool:
    """Calls action_confirm on mrp.production, returns True on success."""
    try:
        client.call_method('mrp.production', 'action_confirm', ids=[mo_id])
        logger.info(f"   MO {mo_id} bestaetigt.")
        return True
    except Exception as e:
        logger.warning(f"   MO {mo_id} konnte nicht bestaetigt werden: {e}")
        return False


def create_quality_point(client, vals: dict) -> int:
    """Creates quality.point, returns id."""
    logger.info(f"-> Creating Quality Point: {vals.get('name')}")
    qp_id = client.create('quality.point', vals)
    logger.info(f"   ID: {qp_id}")
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
    num_workcenters = max(0, int(mrp_config.get("num_workcenters", 3)))
    num_manufacturing_orders = max(0, int(mrp_config.get("num_manufacturing_orders", 0)))
    create_quality_points = bool(mrp_config.get("create_quality_points", False))
    if sub_boms_per_product > components_per_bom:
        sub_boms_per_product = components_per_bom
    if num_mrp_products <= 0:
        return

    # A6/R10: lazy + memoized. ctx.company_ids holds res.partner ids (customer
    # contacts from master_data.py), never a real res.company id — the
    # long-masked bug both work-center and manufacturing-order creation used
    # to have (a broad try/except turned a wrong id into a quiet skip, not a
    # crash). get_main_company_id(client) is the real thing, but it's still one
    # extra request: lazy so a products/BOMs-only run (mrp_routings off, no
    # manufacturing orders requested) never pays for it, memoized so a run
    # that needs it for both sections below only pays once.
    _company_id_cache = []

    def _get_company_id():
        if not _company_id_cache:
            _company_id_cache.append(odoo_actions.get_main_company_id(client))
        return _company_id_cache[0]

    logger.info("\n--- MANUFACTURING: Erstelle Fertigungsprodukte und Stücklisten ---")
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

    # D3: components created first (batched), then parent/sub BOMs with
    # bom_line_ids inlined — see IMPLEMENTIERUNGSPLAN.md D3. Sub-BOMs need their
    # component's product_tmpl_id, which only exists once the component itself
    # has been created, so the ordering below is load-bearing, not cosmetic:
    # main products -> main components -> raw materials (for sub-BOMs) -> all BOMs.

    # --- Step 1: batch-create all main (finished-good) products ---
    main_product_vals = []
    for name in main_products:
        list_price = round(random.uniform(250, 1200), 2)
        standard_price = round(list_price * random.uniform(0.35, 0.65), 2)
        main_product_vals.append({
            "name": name, "sale_ok": True, "purchase_ok": False,
            "list_price": list_price, "standard_price": standard_price, "tracking": "none",
        })
    main_product_ids = client.create_batch('product.product', main_product_vals)
    ctx.product_ids.extend(main_product_ids)
    main_list_price = {pid: vals["list_price"] for pid, vals in zip(main_product_ids, main_product_vals)}

    # Template ids + component name lists per main product (reads — not batchable creates)
    main_tmpl_id = {}
    main_component_names = {}
    for pid, name in zip(main_product_ids, main_products):
        tmpl_id = get_product_template_id(client, pid)
        if not tmpl_id:
            logger.warning(f"⚠️  Konnte Template für Produkt {pid} nicht ermitteln — BOM übersprungen.")
            continue
        main_tmpl_id[pid] = tmpl_id
        component_names = list(bom_components_map.get(name, []))
        while len(component_names) < component_count:
            component_names.append(f"{name} Modul {len(component_names) + 1}")
        main_component_names[pid] = component_names[:component_count]

    # --- Step 2: batch-create ALL components (across all products) in one call ---
    component_meta = []  # [{"main_pid", "name", "standard_price"}, ...], order == component_vals_list
    component_vals_list = []
    for pid, comp_names in main_component_names.items():
        lp = main_list_price[pid]
        for cname in comp_names:
            comp_list_price = round(random.uniform(80, lp * 0.6), 2)
            comp_standard_price = round(comp_list_price * random.uniform(0.4, 0.7), 2)
            component_vals_list.append({
                "name": cname, "sale_ok": False, "purchase_ok": True, "is_storable": True,
                "list_price": comp_list_price, "standard_price": comp_standard_price, "tracking": "none",
            })
            component_meta.append({"main_pid": pid, "name": cname, "standard_price": comp_standard_price})

    all_component_ids = client.create_batch('product.product', component_vals_list)
    ctx.component_ids.extend(all_component_ids)
    for meta, cid in zip(component_meta, all_component_ids):
        meta["id"] = cid

    components_by_product: dict = {}
    for meta in component_meta:
        components_by_product.setdefault(meta["main_pid"], []).append(meta)

    # --- Step 3: batch-create ALL raw materials for sub-BOMs (across all products) ---
    raw_count = max(2, min(4, components_per_bom // 2 + 1))
    raw_vals_list = []
    raw_meta = []  # [{"comp": <component_meta dict>}, ...], order == raw_vals_list
    for pid, comps in components_by_product.items():
        for comp in comps[:sub_boms_per_product]:
            comp_tmpl_id = get_product_template_id(client, comp["id"])
            if not comp_tmpl_id:
                continue
            comp["tmpl_id"] = comp_tmpl_id
            for raw_idx in range(raw_count):
                raw_name = f"{comp['name']} Rohteil {raw_idx + 1}"
                raw_list = round(max(15, comp["standard_price"] * random.uniform(0.4, 0.9)), 2)
                raw_std = round(raw_list * random.uniform(0.5, 0.85), 2)
                raw_vals_list.append({
                    "name": raw_name, "sale_ok": False, "purchase_ok": True, "is_storable": True,
                    "list_price": raw_list, "standard_price": raw_std, "tracking": "none",
                })
                raw_meta.append({"comp": comp})

    all_raw_ids = client.create_batch('product.product', raw_vals_list)
    ctx.component_ids.extend(all_raw_ids)
    for meta, rid in zip(raw_meta, all_raw_ids):
        meta["comp"].setdefault("raw_ids", []).append(rid)

    # --- Step 4: batch-create ALL BOMs (main + sub) in one call, bom_line_ids inline ---
    bom_vals_list = []
    bom_meta = []  # [{"product_id"}, ...], order == bom_vals_list
    for pid in main_product_ids:
        tmpl_id = main_tmpl_id.get(pid)
        if not tmpl_id:
            continue
        comps = components_by_product.get(pid, [])
        line_cmds = [
            (0, 0, {"product_id": c["id"], "product_qty": max(1, random.randint(1, 4))})
            for c in comps
        ]
        bom_vals_list.append({
            "product_tmpl_id": tmpl_id, "product_id": pid, "type": "normal",
            "product_qty": 1.0, "code": f"BOM-{pid}", "bom_line_ids": line_cmds,
        })
        bom_meta.append({"product_id": pid})

        for comp_idx, comp in enumerate(comps[:sub_boms_per_product]):
            comp_tmpl_id = comp.get("tmpl_id")
            raw_ids = comp.get("raw_ids") or []
            if not comp_tmpl_id or not raw_ids:
                continue
            raw_line_cmds = [
                (0, 0, {"product_id": rid, "product_qty": round(random.uniform(1.0, 3.0), 2)})
                for rid in raw_ids
            ]
            bom_vals_list.append({
                "product_tmpl_id": comp_tmpl_id, "product_id": comp["id"], "type": "normal",
                "product_qty": 1.0, "code": f"SUB-{pid}-{comp_idx + 1}", "bom_line_ids": raw_line_cmds,
            })
            bom_meta.append({"product_id": comp["id"]})

    created_bom_ids = client.create_batch('mrp.bom', bom_vals_list)
    bom_product_map = {}  # bom_id -> product_id, for MO creation
    for bom_id, meta in zip(created_bom_ids, bom_meta):
        bom_product_map[bom_id] = meta["product_id"]

    logger.info(f"✅ {len(created_bom_ids)} Stücklisten für {num_mrp_products} Fertigungsprodukte erstellt.")

    # --- SECTION A: Work Centers ---
    workcenter_name_to_id: dict = {}
    wc_data: dict = {}
    # B15: default aligned with the UI's routings_on default — a missing
    # mrp_routings flag must not mean "off in the GUI, on in the module".
    # feature_flags['mrp_routings'] is itself has_create_access-backed since
    # S10 (R10/WP1), but ctx.model_access is checked again directly here too:
    # feature_flags defaults CLOSED on a missing key (this module's own
    # pre-existing B15 convention, not model_access's — the two intentionally
    # differ), so this is the actual open-by-default guard against a probe
    # that never ran (parent module not in installed_modules at connect time).
    mrp_routings_ok = (ctx.feature_flags.get('mrp_routings', False)
                       and ctx.model_access.get('mrp.workcenter', True))
    if mrp_routings_ok and num_workcenters > 0:
        try:
            logger.info("\n--- MANUFACTURING: Erstelle Arbeitszentren ---")
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
            company_id = _get_company_id()
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
            logger.info(f"Arbeitszentren erstellt: {len(workcenter_name_to_id)}")
        except Exception as e:
            logger.info(f"Arbeitszentren konnten nicht erstellt werden: {e}")
    else:
        logger.info("ℹ️  Work Orders nicht aktiviert — Arbeitszentren und Arbeitsgänge übersprungen.")

    # --- SECTION B: BOM Operations ---
    if workcenter_name_to_id and created_bom_ids:
        try:
            logger.info("\n--- MANUFACTURING: Verknuepfe Arbeitsgaenge mit Stuecklisten ---")
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
            logger.info(f"Arbeitsgaenge erstellt: {op_count}")
        except Exception as e:
            logger.info(f"Arbeitsgaenge konnten nicht erstellt werden: {e}")

    # --- SECTION C: Manufacturing Orders ---
    if num_manufacturing_orders > 0 and created_bom_ids:
        try:
            logger.info("\n--- MANUFACTURING: Erstelle Fertigungsauftraege ---")
            company_id = _get_company_id()
            picking_type_id = (
                get_manufacturing_picking_type_id(client, company_id)
                if company_id else None
            )
            if not picking_type_id:
                logger.warning("Kein Fertigungs-Vorgangstyp gefunden - Fertigungsauftraege uebersprungen.")
            else:
                confirmed_mo_ids = []
                created_mo_count = 0
                # Pre-fetch quality references once (only if needed)
                qp_team_id = None
                qp_test_type_id = None
                # Same open-by-default reasoning as mrp_routings_ok above:
                # feature_flags['quality'] defaults closed on a missing key,
                # ctx.model_access.get(..., True) is the guard against a probe
                # that simply never ran.
                if (create_quality_points and ctx.feature_flags.get('quality', False)
                        and ctx.model_access.get('quality.point', True)):
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
                        logger.warning(f"Fertigungsauftrag uebersprungen: {mo_e}")

                logger.info(f"Fertigungsauftraege: {created_mo_count} erstellt, {len(confirmed_mo_ids)} bestaetigt.")

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
                        logger.info(f"Qualitaetspruefpunkte erstellt: {qp_count}")
                    except Exception as qp_e:
                        logger.info(f"Qualitaetspruefpunkte konnten nicht erstellt werden: {qp_e}")
        except Exception as e:
            logger.info(f"Fertigungsauftraege konnten nicht erstellt werden: {e}")
