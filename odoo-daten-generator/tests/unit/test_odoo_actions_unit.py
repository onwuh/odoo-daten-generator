"""Unit tests for odoo_actions helpers (no real Odoo connection needed)."""
import os
import sys
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from odoo_actions import (get_enabled_features, get_server_version, check_field_compatibility,
                          probe_model_access, MODEL_ACCESS_PROBES, classify_version_status,
                          LAST_VERIFIED_VERSION, KNOWN_BROKEN_VERSIONS)


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

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
