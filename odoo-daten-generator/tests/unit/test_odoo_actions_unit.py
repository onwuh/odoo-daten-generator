"""Unit tests for odoo_actions helpers (no real Odoo connection needed)."""
import os
import sys
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from odoo_actions import get_enabled_features


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

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
