"""Manufacturing module: creates products, BOMs, and BOM lines.

Uses a single Gemini call for all BOM component names (batch) instead of one per product.
"""

import logging
import datetime
import random

import data_factory
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


def get_product_template_ids_bulk(client, product_ids: list) -> dict:
    """Return {product_id: template_id} for all given product variants in one
    search_read instead of one per product — same lookup as
    get_product_template_id, just batched."""
    if not product_ids:
        return {}
    records = client.search_read(
        'product.product', [["id", "in", product_ids]], fields=["id", "product_tmpl_id"], limit=0,
    )
    result = {}
    for r in records:
        tmpl = r.get("product_tmpl_id")
        if isinstance(tmpl, (list, tuple)) and tmpl:
            tmpl = tmpl[0]
        result[r["id"]] = tmpl
    return result


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
    quality_fail_pct = max(0, min(100, int(mrp_config.get("quality_fail_pct", 0))))
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
    # bom_line_ids inlined — see ROADMAP.md D3. Sub-BOMs need their
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

    # Template ids + component name lists per main product (one bulk read for
    # all main products instead of one search_read per product).
    main_tmpl_ids = get_product_template_ids_bulk(client, main_product_ids)
    main_tmpl_id = {}
    main_component_names = {}
    for pid, name in zip(main_product_ids, main_products):
        tmpl_id = main_tmpl_ids.get(pid)
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
    sub_bom_comps = [
        comp for comps in components_by_product.values() for comp in comps[:sub_boms_per_product]
    ]
    comp_tmpl_ids = get_product_template_ids_bulk(client, [c["id"] for c in sub_bom_comps])
    raw_vals_list = []
    raw_meta = []  # [{"comp": <component_meta dict>}, ...], order == raw_vals_list
    for pid, comps in components_by_product.items():
        for comp in comps[:sub_boms_per_product]:
            comp_tmpl_id = comp_tmpl_ids.get(comp["id"])
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
            wc_names = []
            wc_vals_list = []
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
                wc_names.append(wc_name)
                wc_vals_list.append(wc_vals)
            wc_ids = client.create_batch('mrp.workcenter', wc_vals_list)
            for wc_name, wc_id in zip(wc_names, wc_ids):
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
            op_vals_list = []
            for bom_id in created_bom_ids:
                num_ops = random.randint(2, 3)
                chosen_wcs = random.choices(wc_names, k=num_ops)
                for op_seq, wc_name in enumerate(chosen_wcs, start=1):
                    wc_id = workcenter_name_to_id[wc_name]
                    ops_list = wc_data.get(wc_name, {}).get("operations", ["Bearbeitung"])
                    op_name = random.choice(ops_list)
                    op_vals_list.append({
                        "name": op_name,
                        "bom_id": bom_id,
                        "workcenter_id": wc_id,
                        "sequence": op_seq * 10,
                        "time_cycle_manual": round(random.uniform(15, 90), 2),
                    })
            client.create_batch('mrp.routing.workcenter', op_vals_list)
            logger.info(f"Arbeitsgaenge erstellt: {len(op_vals_list)}")
        except Exception as e:
            logger.info(f"Arbeitsgaenge konnten nicht erstellt werden: {e}")

    # --- SECTION C: Manufacturing Orders ---
    # S14/R18: company_id/picking_type_id/confirmed_mo_ids/mo_bom_map are
    # read by Section D below regardless of whether this section runs (or
    # runs successfully) — pre-declared here, not just inside the
    # `if to_confirm:` branch, which is also the pre-existing
    # UnboundLocalError fix (an unlucky 0.7-roll on a small mo_vals_list
    # could leave to_confirm empty, and confirmed_mo_ids was only ever
    # assigned inside that branch).
    company_id = None
    picking_type_id = None
    confirmed_mo_ids = []
    mo_bom_map = {}  # mo_id -> bom_id, for Section D's quality.check linkage
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
                mo_vals_list = []
                mo_bom_list = []
                for _ in range(num_manufacturing_orders):
                    bom_id = random.choice(created_bom_ids)
                    product_id = bom_product_map.get(bom_id)
                    if not product_id:
                        continue
                    mo_vals_list.append({
                        "product_id": product_id,
                        "product_qty": float(random.randint(5, 50)),
                        "bom_id": bom_id,
                        "company_id": company_id,
                        "date_start": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })
                    mo_bom_list.append(bom_id)
                mo_ids = client.create_batch('mrp.production', mo_vals_list) if mo_vals_list else []
                created_mo_count = len(mo_ids)
                mo_bom_map = dict(zip(mo_ids, mo_bom_list))
                to_confirm = [mo_id for mo_id in mo_ids if random.random() < 0.7]

                if to_confirm:
                    try:
                        client.call_method('mrp.production', 'action_confirm', ids=to_confirm)
                        confirmed_mo_ids = list(to_confirm)
                    except Exception:
                        # Batched confirm failed for the group (e.g. one MO
                        # lacking components) — fall back to the per-MO path,
                        # which already isolates failures one at a time.
                        confirmed_mo_ids = [
                            mo_id for mo_id in to_confirm if confirm_manufacturing_order(client, mo_id)
                        ]

                logger.info(f"Fertigungsauftraege: {created_mo_count} erstellt, {len(confirmed_mo_ids)} bestaetigt.")
        except Exception as e:
            logger.info(f"Fertigungsauftraege konnten nicht erstellt werden: {e}")

    # --- SECTION D: Quality Points + Checks ---
    # S14/R18: structurally independent of Section C — quality points need
    # only a BOM + manufacturing picking type, never an MO count
    # (`if created_bom_ids:`, not `if num_manufacturing_orders > 0 and
    # created_bom_ids:` like the old combined block). Reuses Section C's
    # company_id/picking_type_id when that ran first, otherwise resolves
    # them itself — a quality-points-only run (num_manufacturing_orders=0)
    # must not silently skip. Two separate try/except blocks (points,
    # checks) so a failure in one never takes the other down.
    if create_quality_points and created_bom_ids:
        if company_id is None:
            company_id = _get_company_id()
        if picking_type_id is None and company_id:
            picking_type_id = get_manufacturing_picking_type_id(client, company_id)

        qp_team_id = None
        qp_test_type_id = None
        # Same open-by-default reasoning as mrp_routings_ok above:
        # feature_flags['quality'] defaults closed on a missing key,
        # ctx.model_access.get(..., True) is the guard against a probe
        # that simply never ran.
        if (picking_type_id and ctx.feature_flags.get('quality', False)
                and ctx.model_access.get('quality.point', True)):
            try:
                teams = client.search_read('quality.alert.team', [], fields=["id"], limit=1)
                qp_team_id = teams[0]["id"] if teams else None
                test_types = client.search_read('quality.point.test_type', [], fields=["id"], limit=1)
                qp_test_type_id = test_types[0]["id"] if test_types else None
            except Exception:
                pass  # quality module likely not installed

        # Quality Points (per BOM). apply_to='products' + product_ids is the
        # real, writable link to a BOM's product — bom_id is a compute
        # facade Odoo silently discards on write (live-confirmed, S14/WP1).
        # test_report_type: 'pdf', not 'none' — 'none' is not a valid
        # selection value and 500s (live-confirmed, S14/WP1); this path had
        # never succeeded before this fix.
        bom_to_qp = {}
        if qp_team_id and qp_test_type_id:
            try:
                qp_vals_list = []
                qp_bom_list = []
                for bom_id in created_bom_ids:
                    product_id = bom_product_map.get(bom_id)
                    if not product_id:
                        continue
                    qp_vals_list.append({
                        "name": f"QP-BOM-{bom_id}",
                        "team_id": qp_team_id,
                        "picking_type_ids": [(4, picking_type_id)],
                        "company_id": company_id,
                        "test_type_id": qp_test_type_id,
                        "test_report_type": "pdf",
                        "apply_to": "products",
                        "product_ids": [(6, 0, [product_id])],
                    })
                    qp_bom_list.append(bom_id)
                qp_ids = client.create_batch('quality.point', qp_vals_list) if qp_vals_list else []
                bom_to_qp = dict(zip(qp_bom_list, qp_ids))
                logger.info(f"Qualitaetspruefpunkte erstellt: {len(qp_ids)}")
            except Exception as qp_e:
                logger.info(f"Qualitaetspruefpunkte konnten nicht erstellt werden: {qp_e}")

        # Quality Checks (per confirmed MO, linked to its BOM's point).
        # Gated on its own model_access probe — a blocked quality.check
        # access degrades only check creation, not quality.point or the
        # rest of this module. No duplicate risk from Section C confirming
        # MOs before this section creates the points: live-confirmed
        # action_confirm alone never auto-generates a quality.check even
        # when a matching point already exists (S14/WP4) — Odoo only does
        # that at stock.picking validation, which this codebase never
        # reaches (see module docstring).
        if (bom_to_qp and confirmed_mo_ids and qp_team_id and qp_test_type_id
                and ctx.model_access.get('quality.check', True)):
            try:
                qc_vals_list = []
                for mo_id in confirmed_mo_ids:
                    bom_id = mo_bom_map.get(mo_id)
                    point_id = bom_to_qp.get(bom_id)
                    product_id = bom_product_map.get(bom_id)
                    if not point_id or not product_id:
                        continue
                    qc_vals_list.append({
                        "point_id": point_id,
                        "production_id": mo_id,
                        "product_id": product_id,
                        "team_id": qp_team_id,
                        "test_type_id": qp_test_type_id,
                        "company_id": company_id,
                    })
                qc_ids = client.create_batch('quality.check', qc_vals_list) if qc_vals_list else []
                # Native do_pass/do_fail (live-confirmed to exist, S14/WP1)
                # instead of writing quality_state directly — sets
                # control_date/user_id the way a real inspection would, same
                # native-over-manual precedent as action_apply_inventory/
                # action_confirm/action_create_invoice/action_reset. Two
                # batched call_method calls (Pattern 8), never per-check.
                pass_ids, fail_ids = data_factory.assign_quality_state(qc_ids, quality_fail_pct)
                if pass_ids:
                    client.call_method('quality.check', 'do_pass', ids=pass_ids)
                if fail_ids:
                    client.call_method('quality.check', 'do_fail', ids=fail_ids)
                logger.info(
                    f"Qualitaetspruefungen erstellt: {len(qc_ids)} "
                    f"({len(pass_ids)} bestanden, {len(fail_ids)} fehlgeschlagen)."
                )
            except Exception as qc_e:
                logger.info(f"Qualitaetspruefungen konnten nicht erstellt werden: {qc_e}")
