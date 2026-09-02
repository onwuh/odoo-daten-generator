#!/usr/bin/env bash
# S11/R5/WP5 — formalizes the version-compat check that was done by hand for
# V20-beta (2026-08-29, PR #20): run the full test suite against a new/beta
# Odoo instance with field capture on, so a version bump either passes clean
# or surfaces exactly what changed.
#
# Dev-side only. Never runs automatically as part of a user-triggered run —
# it has side effects (creates real records on whatever instance
# tests/test_config.ini points at) and takes minutes. Point
# tests/test_config.ini at the target instance before running this (same
# setup tests/integration/test_suite.py itself already expects — see
# CLAUDE.md's "Testing" section).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"      # odoo-daten-generator/

CONFIG_FILE="$APP_DIR/tests/test_config.ini"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Missing $CONFIG_FILE — point it at the target instance first" \
        "(see config.ini.example for the shape), then rerun." >&2
    exit 2
fi

cd "$APP_DIR"

echo "== check_compat: running full suite with field capture against $(grep '^url' tests/test_config.ini | head -1) =="

# ODOO_GENERATOR_CAPTURE_FIELDS=1: exercises Phase A's WP1 capture in the
# same run, so a version check also refreshes field_manifest.json — the
# drift signal a new-version check exists to produce, not a separate step.
if ODOO_GENERATOR_CAPTURE_FIELDS=1 python3 tests/integration/test_suite.py; then
    echo
    echo "== check_compat: PASS =="
    echo "Suite is clean against this instance. Next steps (manual — this"
    echo "script does not do them for you):"
    echo "  1. Diff field_manifest.json against odoo_actions.FIELD_COMPAT_WHITELIST"
    echo "     for anything unexpected."
    echo "  2. If genuinely clean, bump LAST_VERIFIED_VERSION in odoo_actions.py"
    echo "     to this instance's detected version (printed above by the"
    echo "     connect/version step during the run)."
    echo "  Never bump it from anything but this script's own clean pass."
    exit 0
else
    status=$?
    echo
    echo "== check_compat: FAIL =="
    echo "Triage each finding against the boundary table in ROADMAP.md's R5"
    echo "section (registry-entry vs. real code branch) before writing a fix."
    echo "Do NOT bump LAST_VERIFIED_VERSION until a subsequent clean run."
    exit "$status"
fi
