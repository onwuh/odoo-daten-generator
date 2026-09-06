"""Unit tests for odoo_actions helpers (no real Odoo connection needed)."""
import os
import sys
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from odoo_actions import (get_enabled_features, get_server_version, check_field_compatibility,
                          probe_model_access, MODEL_ACCESS_PROBES, classify_version_status,
                          LAST_VERIFIED_VERSION, KNOWN_BROKEN_VERSIONS, create_second_warehouse,
                          get_or_create_analytic_accounts, _ANALYTIC_COST_CENTER_NAMES,
                          get_main_company_id, get_main_company_info)
from config import DemoCriteria, ModuleSelections, RunContext


class _AlwaysHasField(dict):
    """Stands in for a fields_get() result when a test only cares WHICH
    models were probed, not which fields are present on them — reports every
    field as present so check_field_compatibility never emits an unrelated
    warning that would just be noise for that test's purpose."""

    def __contains__(self, _key):
        return True


def run():
    """Returns (all_passed, [(label, ok, detail), ...])"""
    results = []

    # ------------------------------------------------------------------
    # B1 — Pattern 3: feature-flag probes fire only with installed_modules
    # ------------------------------------------------------------------

    try:
        mock_client = MagicMock()
        mock_client.search_read.return_value = []
        flags = get_enabled_features(mock_client, {"crm", "mrp"})
        assert mock_client.search_read.call_count > 0, "Expected probes to fire when modules passed"
        assert "mrp_routings" in flags
        assert "crm_leads" in flags
        results.append(("B1: get_enabled_features with modules → probes fire, flags populated", True, ""))
    except Exception as e:
        results.append(("B1: get_enabled_features with modules → probes fire, flags populated", False, str(e)))

    try:
        mock_client = MagicMock()
        get_enabled_features(mock_client)  # no installed_modules
        assert mock_client.search_read.call_count == 0, (
            f"Expected 0 API calls without modules, got {mock_client.search_read.call_count}"
        )
        results.append(("B1: get_enabled_features without modules → zero API calls", True, ""))
    except Exception as e:
        results.append(("B1: get_enabled_features without modules → zero API calls", False, str(e)))

    try:
        mock_client = MagicMock()
        flags = get_enabled_features(mock_client)
        assert flags == {}, f"Expected empty dict, got {flags!r}"
        results.append(("B1: get_enabled_features without modules → returns {}", True, ""))
    except Exception as e:
        results.append(("B1: get_enabled_features without modules → returns {}", False, str(e)))

    try:
        mock_client = MagicMock()
        mock_client.search_read.return_value = []
        # Only crm — mrp probes must not fire
        flags = get_enabled_features(mock_client, {"crm"})
        assert "crm_leads" in flags
        assert "mrp_routings" not in flags, "mrp_routings should not probe when mrp not installed"
        results.append(("B1: get_enabled_features crm-only → mrp probe skipped", True, ""))
    except Exception as e:
        results.append(("B1: get_enabled_features crm-only → mrp probe skipped", False, str(e)))

    # ------------------------------------------------------------------
    # S5 tier 1 — get_server_version parsing
    # ------------------------------------------------------------------

    try:
        mock_client = MagicMock()
        mock_client.search_read.return_value = [{"latest_version": "saas~19.4.1.3"}]
        version = get_server_version(mock_client)
        assert version == "19.4", f"Expected '19.4', got {version!r}"
        results.append(("S5: get_server_version parses live saas~ format", True, ""))
    except Exception as e:
        results.append(("S5: get_server_version parses live saas~ format", False, str(e)))

    try:
        mock_client = MagicMock()
        mock_client.search_read.return_value = [{"latest_version": "19.0"}]
        version = get_server_version(mock_client)
        assert version == "19.0", f"Expected '19.0', got {version!r}"
        results.append(("S5: get_server_version parses plain self-hosted format", True, ""))
    except Exception as e:
        results.append(("S5: get_server_version parses plain self-hosted format", False, str(e)))

    try:
        mock_client = MagicMock()
        mock_client.search_read.return_value = []
        version = get_server_version(mock_client)
        assert version is None, f"Expected None for empty result, got {version!r}"
        results.append(("S5: get_server_version empty result → None, no crash", True, ""))
    except Exception as e:
        results.append(("S5: get_server_version empty result → None, no crash", False, str(e)))

    try:
        mock_client = MagicMock()
        mock_client.search_read.return_value = [{"latest_version": "garbage"}]
        version = get_server_version(mock_client)
        assert version is None, f"Expected None for unparseable version, got {version!r}"
        results.append(("S5: get_server_version unparseable string → None, no crash", True, ""))
    except Exception as e:
        results.append(("S5: get_server_version unparseable string → None, no crash", False, str(e)))

    try:
        mock_client = MagicMock()
        mock_client.search_read.side_effect = Exception("connection lost")
        version = get_server_version(mock_client)
        assert version is None, f"Expected None on exception, got {version!r}"
        results.append(("S5: get_server_version search_read raises → None, no crash", True, ""))
    except Exception as e:
        results.append(("S5: get_server_version search_read raises → None, no crash", False, str(e)))

    # ------------------------------------------------------------------
    # S11/R5 WP4 — classify_version_status: three distinguishable states
    # instead of the old binary "version detected or not".
    # ------------------------------------------------------------------
    try:
        assert classify_version_status(None) == "unknown"
        results.append(("S11/WP4: classify_version_status(None) -> 'unknown'", True, ""))
    except AssertionError as e:
        results.append(("S11/WP4: classify_version_status(None) -> 'unknown'", False, str(e)))

    try:
        assert classify_version_status(LAST_VERIFIED_VERSION) == "known_good"
        results.append(("S11/WP4: classify_version_status(LAST_VERIFIED_VERSION) -> 'known_good'", True, ""))
    except AssertionError as e:
        results.append(("S11/WP4: classify_version_status(LAST_VERIFIED_VERSION) -> 'known_good'", False, str(e)))

    try:
        # A version this codebase has never run a WP5 check against.
        never_seen = "999.9"
        assert never_seen != LAST_VERIFIED_VERSION and never_seen not in KNOWN_BROKEN_VERSIONS
        assert classify_version_status(never_seen) == "untested"
        results.append(("S11/WP4: classify_version_status(unseen version) -> 'untested'", True, ""))
    except AssertionError as e:
        results.append(("S11/WP4: classify_version_status(unseen version) -> 'untested'", False, str(e)))

    try:
        # KNOWN_BROKEN_VERSIONS starts empty (no real finding yet, same as
        # WP3's registry) — exercise the branch with a fake entry rather than
        # mutating the real module-level dict.
        import odoo_actions as _odoo_actions_mod
        orig = _odoo_actions_mod.KNOWN_BROKEN_VERSIONS
        _odoo_actions_mod.KNOWN_BROKEN_VERSIONS = {"20.0": "some fixed issue"}
        try:
            assert _odoo_actions_mod.classify_version_status("20.0") == "known_broken_with_fix"
        finally:
            _odoo_actions_mod.KNOWN_BROKEN_VERSIONS = orig
        results.append(("S11/WP4: classify_version_status(version in KNOWN_BROKEN_VERSIONS) -> 'known_broken_with_fix'", True, ""))
    except AssertionError as e:
        results.append(("S11/WP4: classify_version_status(version in KNOWN_BROKEN_VERSIONS) -> 'known_broken_with_fix'", False, str(e)))

    # ------------------------------------------------------------------
    # S5 tier 1 — check_field_compatibility
    # ------------------------------------------------------------------

    try:
        mock_client = MagicMock()
        # 'street' missing from the live fields_get response
        mock_client.model_method.return_value = {"name": {}, "is_company": {}}
        warnings = check_field_compatibility(mock_client, whitelist={"res.partner": ["name", "is_company", "street"]})
        assert len(warnings) == 1, f"Expected 1 warning, got {warnings!r}"
        assert "street" in warnings[0] and "res.partner" in warnings[0]
        results.append(("S5: check_field_compatibility flags a missing field", True, ""))
    except Exception as e:
        results.append(("S5: check_field_compatibility flags a missing field", False, str(e)))

    try:
        mock_client = MagicMock()
        mock_client.model_method.return_value = {"name": {}, "is_company": {}, "street": {}}
        warnings = check_field_compatibility(mock_client, whitelist={"res.partner": ["name", "is_company", "street"]})
        assert warnings == [], f"Expected no warnings, got {warnings!r}"
        results.append(("S5: check_field_compatibility no warnings when all fields present", True, ""))
    except Exception as e:
        results.append(("S5: check_field_compatibility no warnings when all fields present", False, str(e)))

    try:
        mock_client = MagicMock()
        # Model itself inaccessible (e.g. parent app not installed) → skip silently,
        # not a version-compatibility warning.
        mock_client.model_method.side_effect = Exception("model not found")
        warnings = check_field_compatibility(mock_client, whitelist={"mrp.production": ["product_id"]})
        assert warnings == [], f"Expected no warnings for inaccessible model, got {warnings!r}"
        results.append(("S5: check_field_compatibility skips inaccessible model silently", True, ""))
    except Exception as e:
        results.append(("S5: check_field_compatibility skips inaccessible model silently", False, str(e)))

    # ------------------------------------------------------------------
    # S10/R10 — check_field_compatibility gates the DEFAULT whitelist on
    # installed_modules (A2b): a fields_get on a model whose app isn't
    # installed used to 404 through the full 6-attempt fallback chain for
    # nothing on every connect.
    # ------------------------------------------------------------------
    try:
        mock_client = MagicMock()
        mock_client.model_method.return_value = _AlwaysHasField()
        # No installed_modules at all: only the ungated (module_key=None)
        # entries — res.partner, product.product, mail.activity, ir.attachment
        # — may be checked; every mrp/hr_*/project/account/crm/sale model must
        # be skipped, i.e. model_method must never be called for them.
        check_field_compatibility(mock_client, installed_modules=set())
        called_models = {c.args[0] for c in mock_client.model_method.call_args_list}
        gated_but_called = called_models & {"mrp.production", "mrp.bom", "hr.leave",
                                            "hr.leave.allocation", "hr.work.entry.type",
                                            "hr.applicant", "hr.job.skill", "project.task",
                                            "account.move", "account.bank.statement",
                                            "crm.lead", "sale.order", "hr.employee"}
        assert not gated_but_called, f"gated models were probed anyway: {gated_but_called}"
        assert "res.partner" in called_models, "ungated core model must still be checked"
        results.append(("S10: check_field_compatibility skips models of uninstalled modules (Pattern 3)", True,
                        f"{len(called_models)} Modelle geprüft"))
    except Exception as e:
        results.append(("S10: check_field_compatibility skips models of uninstalled modules (Pattern 3)", False, str(e)))

    try:
        mock_client = MagicMock()
        mock_client.model_method.return_value = _AlwaysHasField()
        # hr_holidays installed (but not 'hr' itself) -> hr.leave IS checked.
        # This is the exact live gap R10 found: hr.leave ships with
        # hr_holidays, not with hr.
        check_field_compatibility(mock_client, installed_modules={"hr_holidays"})
        called_models = {c.args[0] for c in mock_client.model_method.call_args_list}
        assert "hr.leave" in called_models, "hr.leave must be gated on hr_holidays, not hr"
        assert "hr.employee" not in called_models, "hr.employee must stay gated on hr"
        results.append(("S10: check_field_compatibility gates hr.leave on hr_holidays, not hr", True, ""))
    except Exception as e:
        results.append(("S10: check_field_compatibility gates hr.leave on hr_holidays, not hr", False, str(e)))

    # ------------------------------------------------------------------
    # S11/R5 WP2 — check_field_compatibility composes model_access with
    # installed_modules: a model that's installed but write-blocked (the
    # S10 mrp.workcenter case — installed, readable, "Arbeitsaufträge" off)
    # must stay silent too, same as an uninstalled model (Pattern 3 analog).
    # ------------------------------------------------------------------
    try:
        mock_client = MagicMock()
        mock_client.model_method.return_value = _AlwaysHasField()
        # hr installed (hr.employee gated on it) but blocked per model_access.
        check_field_compatibility(mock_client, installed_modules={"hr"},
                                  model_access={"hr.employee": False})
        called_models = {c.args[0] for c in mock_client.model_method.call_args_list}
        assert "hr.employee" not in called_models, "write-blocked model must stay silent"
        results.append(("S11/WP2: check_field_compatibility skips installed-but-blocked model", True, ""))
    except Exception as e:
        results.append(("S11/WP2: check_field_compatibility skips installed-but-blocked model", False, str(e)))

    try:
        mock_client = MagicMock()
        mock_client.model_method.return_value = _AlwaysHasField()
        # hr installed, model_access says writable -> still checked normally.
        check_field_compatibility(mock_client, installed_modules={"hr"},
                                  model_access={"hr.employee": True})
        called_models = {c.args[0] for c in mock_client.model_method.call_args_list}
        assert "hr.employee" in called_models, "writable model must still be checked"
        results.append(("S11/WP2: check_field_compatibility still checks writable model", True, ""))
    except Exception as e:
        results.append(("S11/WP2: check_field_compatibility still checks writable model", False, str(e)))

    try:
        mock_client = MagicMock()
        mock_client.model_method.return_value = _AlwaysHasField()
        # A model absent from model_access (never probed) defaults to
        # checked — same "indeterminate = True" convention probe_model_access
        # itself uses, not a second, stricter default.
        check_field_compatibility(mock_client, installed_modules={"hr"}, model_access={})
        called_models = {c.args[0] for c in mock_client.model_method.call_args_list}
        assert "hr.employee" in called_models, "model absent from model_access must default to checked"
        results.append(("S11/WP2: check_field_compatibility defaults unprobed model to checked", True, ""))
    except Exception as e:
        results.append(("S11/WP2: check_field_compatibility defaults unprobed model to checked", False, str(e)))

    try:
        mock_client = MagicMock()
        # 'street' still missing — explicit whitelist must warn regardless of
        # model_access, same unconditional contract installed_modules already has.
        mock_client.model_method.return_value = {"name": {}, "is_company": {}}
        warnings = check_field_compatibility(
            mock_client, whitelist={"res.partner": ["name", "is_company", "street"]},
            model_access={"res.partner": False})
        assert len(warnings) == 1, f"explicit whitelist must ignore model_access, got {warnings!r}"
        results.append(("S11/WP2: explicit whitelist bypasses model_access gate", True, ""))
    except Exception as e:
        results.append(("S11/WP2: explicit whitelist bypasses model_access gate", False, str(e)))

    # ------------------------------------------------------------------
    # S10/R10 — get_enabled_features stores real bools, not whatever
    # has_create_access's caller happened to return (a stubbed client can
    # return a non-bool truthy/falsy value; the flags dict is serialised to
    # JSON and must not carry that through).
    # ------------------------------------------------------------------
    try:
        mock_client = MagicMock()
        mock_client.has_create_access.return_value = 1  # truthy, not a bool
        mock_client.search_read.return_value = []
        flags = get_enabled_features(mock_client, {"mrp"})
        assert flags["mrp_routings"] is True, f"expected real True, got {flags['mrp_routings']!r}"
        results.append(("S10: get_enabled_features stores real bool True, not a truthy stand-in", True, ""))
    except Exception as e:
        results.append(("S10: get_enabled_features stores real bool True, not a truthy stand-in", False, str(e)))

    try:
        mock_client = MagicMock()
        mock_client.has_create_access.return_value = 0  # falsy, not a bool
        flags = get_enabled_features(mock_client, {"quality"})
        assert flags["quality"] is False, f"expected real False, got {flags['quality']!r}"
        results.append(("S10: get_enabled_features stores real bool False, not a falsy stand-in", True, ""))
    except Exception as e:
        results.append(("S10: get_enabled_features stores real bool False, not a falsy stand-in", False, str(e)))

    try:
        mock_client = MagicMock()
        mock_client.has_create_access.return_value = True
        flags = get_enabled_features(mock_client, {"mrp"})
        # mrp_routings now checks BOTH mrp.workcenter and mrp.routing.workcenter —
        # the second model this sprint added because it's the one the routing
        # path actually writes. ('mrp' installed also fires the separate
        # 'quality' flag, which is why this is a subset check, not equality.)
        probed = {c.args[0] for c in mock_client.has_create_access.call_args_list}
        assert {"mrp.workcenter", "mrp.routing.workcenter"} <= probed, probed
        results.append(("S10: mrp_routings probes both mrp.workcenter and mrp.routing.workcenter", True, ""))
    except Exception as e:
        results.append(("S10: mrp_routings probes both mrp.workcenter and mrp.routing.workcenter", False, str(e)))

    # ------------------------------------------------------------------
    # S10/R10 — probe_model_access: only models of installed modules are
    # probed (Pattern 3), duplicates across module keys are probed once,
    # and the always-on module keys (stammdaten, documents) are unconditional.
    # ------------------------------------------------------------------
    try:
        mock_client = MagicMock()
        mock_client.has_create_access.return_value = True
        result = probe_model_access(mock_client, installed_modules=set())
        expected_always_on = set(MODEL_ACCESS_PROBES["stammdaten"]) | set(MODEL_ACCESS_PROBES["documents"])
        assert set(result.keys()) == expected_always_on, \
            f"expected only always-on models with no installed modules, got {sorted(result.keys())}"
        results.append(("probe_model_access: no installed modules -> only stammdaten/documents probed (Pattern 3)",
                        True, f"{len(result)} Modelle"))
    except Exception as e:
        results.append(("probe_model_access: no installed modules -> only stammdaten/documents probed (Pattern 3)",
                        False, str(e)))

    try:
        mock_client = MagicMock()
        mock_client.has_create_access.return_value = True
        result = probe_model_access(mock_client, installed_modules={"crm"})
        assert "crm.lead" in result and "mail.activity" in result, result
        assert "sale.order" not in result, "sale not installed, sale.order must not be probed"
        results.append(("probe_model_access: crm-only -> crm models probed, sale skipped (Pattern 3)", True, ""))
    except Exception as e:
        results.append(("probe_model_access: crm-only -> crm models probed, sale skipped (Pattern 3)", False, str(e)))

    try:
        mock_client = MagicMock()
        mock_client.has_create_access.return_value = True
        # mrp lists quality.point too (via MODEL_ACCESS_PROBES); make sure a
        # model shared across module keys is probed exactly once.
        probe_model_access(mock_client, installed_modules={"mrp"})
        probed_calls = [c.args[0] for c in mock_client.has_create_access.call_args_list]
        assert probed_calls.count("quality.point") == 1, \
            f"quality.point probed {probed_calls.count('quality.point')} times, expected exactly 1"
        results.append(("probe_model_access: a model listed once is probed exactly once", True, ""))
    except Exception as e:
        results.append(("probe_model_access: a model listed once is probed exactly once", False, str(e)))

    # ------------------------------------------------------------------
    # S13/R14 — create_second_warehouse: create() then read-back lot_stock_id
    # ------------------------------------------------------------------
    try:
        mock_client = MagicMock()
        mock_client.create.return_value = 42
        mock_client.search_read.return_value = [{"lot_stock_id": [16, "WH2/Stock"]}]
        result = create_second_warehouse(mock_client, company_id=1)
        assert result == {"warehouse_id": 42, "stock_location_id": 16}, result
        create_call = mock_client.create.call_args
        assert create_call.args[0] == "stock.warehouse", create_call
        vals = create_call.args[1]
        assert vals["company_id"] == 1, vals
        assert "name" in vals and "code" in vals, vals
        results.append(("S13/R14: create_second_warehouse creates + reads back lot_stock_id", True, ""))
    except AssertionError as e:
        results.append(("S13/R14: create_second_warehouse creates + reads back lot_stock_id", False, str(e)))

    try:
        # Pattern 2-adjacent: no warehouse found on read-back -> None, no crash.
        mock_client = MagicMock()
        mock_client.create.return_value = 42
        mock_client.search_read.return_value = []
        result = create_second_warehouse(mock_client, company_id=1)
        assert result is None, result
        results.append(("S13/R14: create_second_warehouse -> None if read-back finds nothing", True, ""))
    except AssertionError as e:
        results.append(("S13/R14: create_second_warehouse -> None if read-back finds nothing", False, str(e)))

    # ------------------------------------------------------------------
    # S13/Befund 3 — get_enabled_features: stock_multi_locations/stock_lots
    # are purely-informational feature flags, gated on 'stock' installed,
    # left UNSET (not False) on any read failure (missing-key convention).
    # ------------------------------------------------------------------
    try:
        mock_client = MagicMock()
        mock_client.create.return_value = 4
        mock_client.search_read.return_value = [
            {"group_stock_multi_locations": True, "group_stock_production_lot": False},
        ]
        flags = get_enabled_features(mock_client, {"stock"})
        assert flags["stock_multi_locations"] is True, flags
        assert flags["stock_lots"] is False, flags
        results.append(("S13/Befund 3: get_enabled_features reads stock settings when 'stock' installed", True, ""))
    except AssertionError as e:
        results.append(("S13/Befund 3: get_enabled_features reads stock settings when 'stock' installed", False, str(e)))

    try:
        # 'stock' not installed -> no res.config.settings probe at all.
        mock_client = MagicMock()
        flags = get_enabled_features(mock_client, {"crm"})
        assert "stock_multi_locations" not in flags and "stock_lots" not in flags, flags
        assert mock_client.create.call_count == 0, "res.config.settings probed without 'stock' installed"
        results.append(("S13/Befund 3: no stock settings probe when 'stock' not installed (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("S13/Befund 3: no stock settings probe when 'stock' not installed (Pattern 3)", False, str(e)))

    try:
        # A failed settings read leaves the keys UNSET, never False — a
        # caller doing .get(key, True) must never see a false "setting is
        # off" hint from a probe that simply couldn't run.
        mock_client = MagicMock()
        mock_client.create.side_effect = Exception("no res.config.settings access")
        flags = get_enabled_features(mock_client, {"stock"})
        assert "stock_multi_locations" not in flags, flags
        assert "stock_lots" not in flags, flags
        results.append(("S13/Befund 3: failed settings read leaves keys unset, not False", True, ""))
    except AssertionError as e:
        results.append(("S13/Befund 3: failed settings read leaves keys unset, not False", False, str(e)))

    # ==================================================================
    # S15/R20 — get_or_create_analytic_accounts
    # ==================================================================

    def _make_ctx():
        criteria = DemoCriteria(
            mode="both", industry="IT", num_companies=0,
            num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
            num_services=0, num_consumables=0, num_storables=0,
        )
        return RunContext(
            criteria=criteria, module_selections=ModuleSelections(), industry="IT",
            language_name="German", language_code="de",
        )

    try:
        # Happy path: one plan create, one batch create for the cost centers
        # (Pattern 8), ids returned and cached.
        mock_client = MagicMock()
        mock_client.create.return_value = 501
        mock_client.create_batch.return_value = [601, 602, 603]
        ctx = _make_ctx()
        result = get_or_create_analytic_accounts(mock_client, ctx)
        assert result == [601, 602, 603], result
        assert ctx.analytic_account_ids == [601, 602, 603], ctx.analytic_account_ids
        plan_calls = [c for c in mock_client.create.call_args_list if c.args[0] == 'account.analytic.plan']
        assert len(plan_calls) == 1, plan_calls
        assert "company_id" not in plan_calls[0].args[1], plan_calls[0].args[1]
        acc_batches = [c for c in mock_client.create_batch.call_args_list if c.args[0] == 'account.analytic.account']
        assert len(acc_batches) == 1, acc_batches
        acc_vals = acc_batches[0].args[1]
        assert len(acc_vals) == len(_ANALYTIC_COST_CENTER_NAMES), acc_vals
        for v in acc_vals:
            assert set(v.keys()) == {"name", "plan_id"}, v  # no company_id — live-confirmed unneeded
            assert v["plan_id"] == 501, v
        results.append(("get_or_create_analytic_accounts: happy path, one plan + one batch create (Pattern 8)", True, ""))
    except AssertionError as e:
        results.append(("get_or_create_analytic_accounts: happy path, one plan + one batch create (Pattern 8)", False, str(e)))

    try:
        # Memoization: a second call on the same ctx must not create anything again.
        mock_client = MagicMock()
        mock_client.create.return_value = 501
        mock_client.create_batch.return_value = [601, 602, 603]
        ctx = _make_ctx()
        first = get_or_create_analytic_accounts(mock_client, ctx)
        mock_client.create.reset_mock()
        mock_client.create_batch.reset_mock()
        second = get_or_create_analytic_accounts(mock_client, ctx)
        assert second == first, (first, second)
        mock_client.create.assert_not_called()
        mock_client.create_batch.assert_not_called()
        results.append(("get_or_create_analytic_accounts: memoized, second call makes no API calls", True, ""))
    except AssertionError as e:
        results.append(("get_or_create_analytic_accounts: memoized, second call makes no API calls", False, str(e)))

    try:
        # is None vs [] distinction: a genuinely empty result (plan create
        # failed) must NOT be retried on a later call within the same run.
        mock_client = MagicMock()
        mock_client.create.side_effect = Exception("no create rights on account.analytic.plan")
        ctx = _make_ctx()
        first = get_or_create_analytic_accounts(mock_client, ctx)
        assert first == [], first
        assert ctx.analytic_account_ids == [], ctx.analytic_account_ids  # not None -> "already tried"
        mock_client.create.reset_mock()
        second = get_or_create_analytic_accounts(mock_client, ctx)
        assert second == [], second
        mock_client.create.assert_not_called()  # must not retry
        results.append(("get_or_create_analytic_accounts: empty result on failure is cached, not retried", True, ""))
    except AssertionError as e:
        results.append(("get_or_create_analytic_accounts: empty result on failure is cached, not retried", False, str(e)))

    try:
        # model_access explicitly blocking account.analytic.plan -> no
        # create attempted at all, empty result cached.
        mock_client = MagicMock()
        ctx = _make_ctx()
        ctx.model_access = {"account.analytic.plan": False}
        result = get_or_create_analytic_accounts(mock_client, ctx)
        assert result == [], result
        mock_client.create.assert_not_called()
        mock_client.create_batch.assert_not_called()
        results.append(("get_or_create_analytic_accounts: model_access=False -> no calls, empty result (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("get_or_create_analytic_accounts: model_access=False -> no calls, empty result (Pattern 3)", False, str(e)))

    try:
        # empty model_access defaults open (B1 guard) — must not itself block.
        mock_client = MagicMock()
        mock_client.create.return_value = 501
        mock_client.create_batch.return_value = [601]
        ctx = _make_ctx()
        ctx.model_access = {}
        result = get_or_create_analytic_accounts(mock_client, ctx)
        assert result == [601], result
        results.append(("get_or_create_analytic_accounts: empty model_access defaults open (B1 guard)", True, ""))
    except AssertionError as e:
        results.append(("get_or_create_analytic_accounts: empty model_access defaults open (B1 guard)", False, str(e)))

    # ==================================================================
    # S16/D3 — get_main_company_id/get_main_company_info become
    # company_id-parameter-aware
    # ==================================================================

    try:
        # company_id given -> returned directly, no search_read at all.
        mock_client = MagicMock()
        result = get_main_company_id(mock_client, company_id=42)
        assert result == 42, result
        mock_client.search_read.assert_not_called()
        results.append(("get_main_company_id: company_id given -> returned directly, no lookup", True, ""))
    except AssertionError as e:
        results.append(("get_main_company_id: company_id given -> returned directly, no lookup", False, str(e)))

    try:
        # company_id omitted -> original id=1-first behavior unchanged.
        mock_client = MagicMock()
        mock_client.search_read.return_value = [{"id": 1}]
        result = get_main_company_id(mock_client)
        assert result == 1, result
        results.append(("get_main_company_id: company_id omitted -> original id=1 lookup unchanged", True, ""))
    except AssertionError as e:
        results.append(("get_main_company_id: company_id omitted -> original id=1 lookup unchanged", False, str(e)))

    try:
        # company_id given -> searches for THAT id, not id=1.
        mock_client = MagicMock()
        mock_client.search_read.return_value = [{
            "name": "Firma 2", "street": "", "street2": "", "zip": "", "city": "",
            "country_id": False, "vat": False,
        }]
        get_main_company_info(mock_client, company_id=7)
        domain = mock_client.search_read.call_args_list[0].args[1]
        assert domain == [["id", "=", 7]], domain
        results.append(("get_main_company_info: company_id given -> searches for that id, not id=1", True, ""))
    except AssertionError as e:
        results.append(("get_main_company_info: company_id given -> searches for that id, not id=1", False, str(e)))

    try:
        # company_id omitted -> original id=1 lookup unchanged.
        mock_client = MagicMock()
        mock_client.search_read.return_value = [{
            "name": "Firma 1", "street": "", "street2": "", "zip": "", "city": "",
            "country_id": False, "vat": False,
        }]
        get_main_company_info(mock_client)
        domain = mock_client.search_read.call_args_list[0].args[1]
        assert domain == [["id", "=", 1]], domain
        results.append(("get_main_company_info: company_id omitted -> original id=1 lookup unchanged", True, ""))
    except AssertionError as e:
        results.append(("get_main_company_info: company_id omitted -> original id=1 lookup unchanged", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
