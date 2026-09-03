"""Unit tests for run_config.py — the payload → config mapping that replaced gui.py.

Covers the S8 carry-over that S9 exists to close: WANTED_MODULES must list
`purchase` and `stock`, or those modules never enter ctx.installed_modules and
orchestrator.py skips them forever (B1 bug class). Pattern 3 applies throughout —
a module that is off must produce no selection at all.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import run_config
from config import ModuleSelections


_FULL = {
    "mode": "both",
    "industry": "Maschinenbau",
    "use_existing": True,
    "existing_data_consent": "granted",
    "skip_master_data": False,
    "master_data": {"num_companies": 2, "num_delivery_contacts": 1, "num_invoice_contacts": 1,
                    "num_other_contacts": 0, "num_services": 4, "num_consumables": 2,
                    "num_storables": 3},
    "modules": {
        "crm": {"enabled": True, "count": 6, "leads": 2,
                "chatter": {"enabled": True, "style": "full_email", "messages_per_opp": 5},
                "activities": {"enabled": True, "past_pct": 40, "today_pct": 30},
                "lost": {"enabled": True, "pct": 25}},
        "sale": {"enabled": True, "count": 7, "confirm_pct": 80},
        "account": {"enabled": True, "count": 5, "bills": 3, "bank_transactions": True},
        "hr": {"enabled": True, "count": 9,
               "timeoff": {"enabled": True, "entries_per_employee": 3, "avg_length_days": 4,
                           "past_future_pct": 50, "timescale_days": 200, "validate_pct": 90}},
        "project": {"enabled": True, "count": 4, "tasks_per_project": 6},
        "hr_timesheet": {"enabled": True, "count": 25},
        "mrp": {"enabled": True, "num_products": 2, "components_per_bom": 3,
                "sub_boms_per_product": 9, "num_workcenters": 2,
                "num_manufacturing_orders": 4, "create_quality_points": True},
        "hr_recruitment": {"enabled": True, "num_jobs": 3, "num_candidates": 8,
                           "create_skills": True, "num_skill_types": 2, "skills_per_type": 3},
        "purchase": {"enabled": True, "count": 6, "confirm_pct": 55},
        "stock": {"enabled": True, "avg_qty": 42, "sub_locations": 3, "second_warehouse": True,
                  "tracking_lot_pct": 20, "tracking_serial_pct": 10, "tracking_serial_max": 7,
                  "orderpoints_pct": 15, "orderpoint_min_qty": 8, "orderpoint_max_qty": 30},
        "hr_expense": {"enabled": True, "count_per_employee": 4, "approved_pct": 60},
        "documents": {"enabled": True, "bill_pdfs": True, "cv_pdfs": False},
    },
}

_ALL_INSTALLED = {"crm", "sale", "account", "hr", "project", "hr_timesheet",
                  "mrp", "hr_recruitment", "purchase", "stock", "hr_expense"}


def _build(payload, installed=None, flags=None, model_access=None):
    return run_config.build_context(
        payload,
        language_name="German", language_code="de_DE", llm_model_name="m",
        installed_modules=installed if installed is not None else _ALL_INSTALLED,
        feature_flags=flags if flags is not None else {"crm_leads": True},
        model_access=model_access,
        existing_company_ids=[101, 102], existing_product_ids=[201],
    )


def run():
    results = []

    # ------------------------------------------------------------------
    # S8 carry-over: purchase + stock must be probed and orchestrated
    # ------------------------------------------------------------------
    try:
        assert "purchase" in run_config.WANTED_MODULES, run_config.WANTED_MODULES
        assert "stock" in run_config.WANTED_MODULES, run_config.WANTED_MODULES
        # Every probed module must also have a label, or it appears in a
        # checklist detail with no readable name. GATE_ONLY_MODULES
        # (hr_holidays/hr_work_entry) are probed and labelled but deliberately
        # have no progress-row slot — they only gate a sub-behaviour of an
        # already-installed module (modules/hr.py's create_leave_data), see
        # run_config.GATE_ONLY_MODULES's docstring.
        for key in run_config.WANTED_MODULES + run_config.PSEUDO_MODULES:
            assert key in run_config.MODULE_LABELS, key
            if key in run_config.GATE_ONLY_MODULES:
                assert key not in run_config.MODULE_RUN_ORDER, \
                    f"{key} is gate-only and must never be its own progress row"
                continue
            assert key in run_config.MODULE_RUN_ORDER, key
        results.append(("WANTED_MODULES enthält purchase und stock", True, ""))
    except Exception as e:
        results.append(("WANTED_MODULES enthält purchase und stock", False, str(e)))

    try:
        # The progress order must match what orchestrator actually executes.
        src = open(os.path.join(_ROOT, "orchestrator.py"), encoding="utf-8").read()
        positions = [src.index(f'("{key}"') for key in run_config.MODULE_RUN_ORDER]
        assert positions == sorted(positions), \
            f"Reihenfolge weicht von orchestrator.module_order ab: {run_config.MODULE_RUN_ORDER}"
        results.append(("MODULE_RUN_ORDER folgt orchestrator.module_order", True, ""))
    except Exception as e:
        results.append(("MODULE_RUN_ORDER folgt orchestrator.module_order", False, str(e)))

    # ------------------------------------------------------------------
    # Full payload maps onto every ModuleSelections field
    # ------------------------------------------------------------------
    try:
        ctx, selected = _build(_FULL)
        sel = ctx.module_selections
        assert sel.crm == 6 and sel.leads == 2
        assert sel.crm_chatter == {"enabled": True, "style": "full_email",
                                   "messages_per_opp": 5, "use_db_names": True}, sel.crm_chatter
        assert sel.crm_activities == {"enabled": True, "past_pct": 40, "today_pct": 30}
        assert sel.crm_lost == {"pct": 25}, sel.crm_lost
        assert sel.sale == 7 and sel.sale_confirm_pct == 80
        assert sel.account == 5 and sel.account_bills == 3 and sel.create_bank_transactions is True
        assert sel.hr == 9 and sel.hr_timeoff["validate_pct"] == 90
        assert sel.project == 4 and sel.tasks_per_project == 6
        assert sel.hr_timesheet == 25
        # sub_boms is clamped to the component count, never above it
        assert sel.mrp["sub_boms_per_product"] == 3, sel.mrp
        assert sel.hr_recruitment["num_candidates"] == 8
        assert sel.purchase == 6 and sel.purchase_confirm_pct == 55
        assert sel.stock == {
            "avg_qty": 42, "sub_locations": 3, "second_warehouse": True,
            "tracking_lot_pct": 20, "tracking_serial_pct": 10, "tracking_serial_max": 7,
            "orderpoints_pct": 15, "orderpoint_min_qty": 8, "orderpoint_max_qty": 30,
        }, sel.stock
        assert sel.hr_expense == {"count_per_employee": 4, "approved_pct": 60}, sel.hr_expense
        assert sel.documents == {"bill_pdfs_enabled": True, "cv_pdfs_enabled": False}
        assert selected == _ALL_INSTALLED | {"documents"}, selected
        results.append(("Vollständiges Payload füllt alle ModuleSelections-Felder", True, ""))
    except Exception as e:
        results.append(("Vollständiges Payload füllt alle ModuleSelections-Felder", False, str(e)))

    # ------------------------------------------------------------------
    # stock must stay dict-shaped: orchestrator looks it up via getattr(sel, "stock")
    # ------------------------------------------------------------------
    try:
        ctx, _ = _build(_FULL)
        assert ctx.module_selections.get("stock")["avg_qty"] == 42
        assert ctx.module_selections.get("purchase") == 6
        assert bool(ctx.module_selections.get("stock")) is True
        empty = ModuleSelections()
        assert not empty.get("stock") and not empty.get("purchase")
        results.append(("orchestrator-Gate: sel.get('stock')/'purchase' greifen", True, ""))
    except Exception as e:
        results.append(("orchestrator-Gate: sel.get('stock')/'purchase' greifen", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 3: a disabled module produces no selection and no progress row
    # ------------------------------------------------------------------
    try:
        payload = dict(_FULL)
        payload["modules"] = dict(_FULL["modules"])
        payload["modules"]["purchase"] = {"enabled": False, "count": 99}
        payload["modules"]["stock"] = {"enabled": False, "avg_qty": 99}
        ctx, selected = _build(payload)
        assert "purchase" not in selected and "stock" not in selected
        assert ctx.module_selections.purchase == 0
        assert ctx.module_selections.stock == {}
        keys = run_config.active_progress_keys(ctx, selected)
        assert "purchase" not in keys and "stock" not in keys, keys
        results.append(("Pattern 3: abgeschaltetes Modul erzeugt keine Auswahl", True, ""))
    except Exception as e:
        results.append(("Pattern 3: abgeschaltetes Modul erzeugt keine Auswahl", False, str(e)))

    # ------------------------------------------------------------------
    # R11: crm_lost only ever settable when crm itself is enabled — sending
    # modules.crm.lost.enabled=True with modules.crm.enabled=False must not
    # leak crm_lost into the selection (build_selections never reads "lost"
    # outside the `if _enabled(crm)` block).
    # ------------------------------------------------------------------
    try:
        payload = dict(_FULL)
        payload["modules"] = dict(_FULL["modules"])
        payload["modules"]["crm"] = {"enabled": False, "lost": {"enabled": True, "pct": 90}}
        ctx, selected = _build(payload)
        assert "crm" not in selected, selected
        assert ctx.module_selections.crm_lost == {}, ctx.module_selections.crm_lost
        results.append(("R11: crm_lost cannot be set while crm itself is disabled", True, ""))
    except Exception as e:
        results.append(("R11: crm_lost cannot be set while crm itself is disabled", False, str(e)))

    # ------------------------------------------------------------------
    # B10: installed AND selected gates the progress rows
    # ------------------------------------------------------------------
    try:
        ctx, selected = _build(_FULL, installed={"crm", "sale"})
        keys = run_config.active_progress_keys(ctx, selected)
        assert keys == ["stammdaten", "crm", "sale", "documents"], keys
        results.append(("B10: Fortschrittszeilen nur für installiert UND gewählt", True, str(keys)))
    except Exception as e:
        results.append(("B10: Fortschrittszeilen nur für installiert UND gewählt", False, str(e)))

    # ------------------------------------------------------------------
    # feature_flags plumbing — {} silently disables MRP routings/quality (B1 class)
    # ------------------------------------------------------------------
    try:
        ctx, _ = _build(_FULL, flags={"mrp_routings": True, "quality": False, "crm_leads": True})
        assert ctx.feature_flags["mrp_routings"] is True, ctx.feature_flags
        ctx_empty, _ = _build(_FULL, flags={})
        assert ctx_empty.feature_flags == {}, ctx_empty.feature_flags
        assert ctx_empty.feature_flags.get("mrp_routings", False) is False
        results.append(("feature_flags werden in den RunContext durchgereicht", True, ""))
    except Exception as e:
        results.append(("feature_flags werden in den RunContext durchgereicht", False, str(e)))

    # ------------------------------------------------------------------
    # master mode selects nothing transactional
    # ------------------------------------------------------------------
    try:
        payload = dict(_FULL, mode="master")
        ctx, selected = _build(payload)
        assert selected == set(), selected
        assert ctx.module_selections.crm == 0 and ctx.module_selections.purchase == 0
        assert run_config.active_progress_keys(ctx, selected) == ["stammdaten"]
        results.append(("Modus 'master': keine Bewegungsdaten-Module aktiv", True, ""))
    except Exception as e:
        results.append(("Modus 'master': keine Bewegungsdaten-Module aktiv", False, str(e)))

    # ------------------------------------------------------------------
    # skip_master_data drops the Stammdaten row; use_existing seeds the ids
    # ------------------------------------------------------------------
    try:
        payload = dict(_FULL, skip_master_data=True)
        ctx, selected = _build(payload)
        assert ctx.skip_master_data is True
        assert ctx.company_ids == [101, 102] and ctx.product_ids == [201]
        assert "stammdaten" not in run_config.active_progress_keys(ctx, selected)
        no_existing = dict(_FULL, use_existing=False)
        ctx2, _ = _build(no_existing)
        assert ctx2.company_ids == [] and ctx2.product_ids == []
        results.append(("skip_master_data / use_existing werden übernommen", True, ""))
    except Exception as e:
        results.append(("skip_master_data / use_existing werden übernommen", False, str(e)))

    # ------------------------------------------------------------------
    # Existing-data consent: the one setting that lets a value read out of the
    # target database reach an LLM prompt (crm.py's chatter customer/salesperson)
    # ------------------------------------------------------------------
    try:
        base = dict(_FULL, use_existing=True)
        base.pop("existing_data_consent", None)
        for label, consent in [("ohne Antwort", None), ("abgelehnt", "denied")]:
            payload = dict(base)
            if consent is not None:
                payload["existing_data_consent"] = consent
            try:
                _build(payload)
                raise AssertionError(f"akzeptiert: {label}")
            except run_config.ConfigError:
                pass
        results.append(("Einwilligung: use_existing ohne Zustimmung wird abgelehnt", True, ""))
    except Exception as e:
        results.append(("Einwilligung: use_existing ohne Zustimmung wird abgelehnt", False, str(e)))

    try:
        granted = dict(_FULL, use_existing=True, existing_data_consent="granted")
        ctx, _ = _build(granted)
        assert ctx.module_selections.crm_chatter["use_db_names"] is True, ctx.module_selections.crm_chatter
        assert ctx.company_ids == [101, 102], ctx.company_ids

        # Without existing data the question does not arise, and the chatter
        # prompt still gets generic placeholders rather than real names.
        without = dict(_FULL, use_existing=False)
        without.pop("existing_data_consent", None)
        ctx2, _ = _build(without)
        assert ctx2.module_selections.crm_chatter["use_db_names"] is False, ctx2.module_selections.crm_chatter
        assert ctx2.company_ids == [], ctx2.company_ids
        results.append(("Einwilligung: Zustimmung gibt DB-Namen frei, sonst nicht", True, ""))
    except Exception as e:
        results.append(("Einwilligung: Zustimmung gibt DB-Namen frei, sonst nicht", False, str(e)))

    try:
        bogus = dict(_FULL, use_existing=True, existing_data_consent="vielleicht")
        try:
            _build(bogus)
            raise AssertionError("unbekannter Wert akzeptiert")
        except run_config.ConfigError:
            pass
        results.append(("Einwilligung: unbekannter Wert wird abgelehnt", True, ""))
    except Exception as e:
        results.append(("Einwilligung: unbekannter Wert wird abgelehnt", False, str(e)))

    # ------------------------------------------------------------------
    # Untrusted input: bad types and out-of-range values are refused, not coerced
    # ------------------------------------------------------------------
    try:
        bad_payloads = [
            ({"mode": "bogus"}, "unbekannter Modus"),
            ({"mode": "both", "modules": "nope"}, "modules kein Objekt"),
            ({"mode": "both", "modules": {"sale": {"enabled": True, "count": "viele"}}}, "Zahl als Text"),
            ({"mode": "both", "modules": {"sale": {"enabled": True, "count": -5}}}, "negativ"),
            ({"mode": "both", "modules": {"sale": {"enabled": True, "count": 10 ** 9}}}, "absurd groß"),
            ({"mode": "both", "modules": {"crm": {"enabled": True, "count": 1,
                                                  "chatter": {"enabled": True, "style": "evil"}}}}, "Stil"),
        ]
        for payload, label in bad_payloads:
            try:
                _build(payload)
                raise AssertionError(f"akzeptiert: {label}")
            except run_config.ConfigError:
                pass
        results.append(("Ungültige Payloads werden mit ConfigError abgelehnt", True, ""))
    except Exception as e:
        results.append(("Ungültige Payloads werden mit ConfigError abgelehnt", False, str(e)))

    # ------------------------------------------------------------------
    # S10/R10 — effective_installed_modules: a module drops out only when its
    # PRIMARY model is blocked; a missing probe entry defaults to usable
    # (B1 error class guard), and a secondary model being blocked must not
    # remove the module.
    # ------------------------------------------------------------------
    try:
        usable, blocked = run_config.effective_installed_modules(
            {"crm", "sale"}, {"crm.lead": False, "sale.order": True})
        assert usable == {"sale"}, usable
        assert blocked == {"crm"}, blocked
        results.append(("effective_installed_modules: blocked primary model drops the module", True, ""))
    except Exception as e:
        results.append(("effective_installed_modules: blocked primary model drops the module", False, str(e)))

    try:
        # crm.lead (primary) writable, mail.activity (secondary, not consulted
        # here) irrelevant to this decision.
        usable, blocked = run_config.effective_installed_modules(
            {"crm"}, {"crm.lead": True, "mail.activity": False})
        assert usable == {"crm"} and blocked == set(), (usable, blocked)
        results.append(("effective_installed_modules: a blocked secondary model does not remove the module", True, ""))
    except Exception as e:
        results.append(("effective_installed_modules: a blocked secondary model does not remove the module", False, str(e)))

    try:
        # Empty model_access: nothing was probed at all -> every module stays
        # usable (default-True-Regel), never silently dropped.
        usable, blocked = run_config.effective_installed_modules(
            {"crm", "sale", "hr"}, {})
        assert usable == {"crm", "sale", "hr"} and blocked == set(), (usable, blocked)
        results.append(("effective_installed_modules: empty model_access blocks nothing (Default-True)", True, ""))
    except Exception as e:
        results.append(("effective_installed_modules: empty model_access blocks nothing (Default-True)", False, str(e)))

    try:
        # A module with no PRIMARY_MODEL_PER_MODULE entry at all (e.g. a
        # future module key this mapping hasn't caught up with) is unaffected.
        usable, blocked = run_config.effective_installed_modules(
            {"stammdaten"}, {"res.partner": False})
        assert usable == {"stammdaten"} and blocked == set(), (usable, blocked)
        results.append(("effective_installed_modules: module without a primary-model entry is always usable", True, ""))
    except Exception as e:
        results.append(("effective_installed_modules: module without a primary-model entry is always usable", False, str(e)))

    try:
        # End-to-end through build_context: a blocked module must disappear
        # from ctx.installed_modules AND from active_progress_keys, exactly
        # like an uninstalled one (B10's existing invariant, now also driven
        # by write access, not just ir.module.module state).
        ctx, selected = _build(_FULL, model_access={"crm.lead": False})
        assert "crm" not in ctx.installed_modules, ctx.installed_modules
        keys = run_config.active_progress_keys(ctx, selected)
        assert "crm" not in keys, keys
        results.append(("effective_installed_modules: blocked module vanishes from ctx AND progress keys", True, ""))
    except Exception as e:
        results.append(("effective_installed_modules: blocked module vanishes from ctx AND progress keys", False, str(e)))

    try:
        # ctx.model_access carries the raw probe dict through unchanged, for
        # modules that gate a sub-behaviour directly (documents.py's
        # ir.attachment, modules/hr.py's leave models) rather than through
        # the coarser installed_modules/feature_flags gates.
        ctx, _ = _build(_FULL, model_access={"ir.attachment": False})
        assert ctx.model_access == {"ir.attachment": False}, ctx.model_access
        results.append(("model_access wird unverändert in den RunContext durchgereicht", True, ""))
    except Exception as e:
        results.append(("model_access wird unverändert in den RunContext durchgereicht", False, str(e)))

    # ------------------------------------------------------------------
    # Pre-flight counts are arithmetic and non-negative
    # ------------------------------------------------------------------
    try:
        ctx, selected = _build(_FULL)
        counts = run_config.estimate_record_counts(ctx, selected)
        assert counts["Kontakte"] == 2 * (1 + 1 + 1 + 0), counts
        assert counts["Produkte"] == 4 + 2 + 3, counts
        assert counts["Aufgaben"] == 4 * 6, counts
        assert counts["Chatter-Nachrichten"] == 6 * 5, counts
        assert counts["Bestellungen"] == 6, counts
        assert all(v > 0 for v in counts.values()), counts
        results.append(("Pre-Flight-Zahlen sind rechnerisch korrekt", True, f"{sum(counts.values())} gesamt"))
    except Exception as e:
        results.append(("Pre-Flight-Zahlen sind rechnerisch korrekt", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
