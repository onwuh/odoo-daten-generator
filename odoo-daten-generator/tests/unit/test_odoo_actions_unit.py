"""Unit tests for odoo_actions helpers (no real Odoo connection needed)."""
import os
import sys
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from odoo_actions import get_enabled_features, get_server_version, check_field_compatibility


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

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
