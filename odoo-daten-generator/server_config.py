"""Operator-supplied connection defaults (BETA).

The settled design for S9 was: the server holds no secrets of its own, every
credential is user-supplied per session and discarded. That is what made
self-hosting defensible in the first place.

This module deliberately relaxes that for a **beta phase**: when a field arrives
empty, the value from `config.ini` (or the environment) is used instead, so a
tester can click Verbinden without pasting four values. The trade-off is real and
worth stating plainly:

  Anyone who knows the shared access code can then drive the operator's own Odoo
  API key. Guard A still applies — the configured URL must itself be a
  demo-*.odoo.com host — so the blast radius is a throwaway demo database, not a
  customer system. It is not nothing, though: the access code becomes the only
  thing between a visitor and that instance.

Kill switch: ODOO_GENERATOR_CONFIG_DEFAULTS=off disables the fallback entirely
and restores the original "bring your own credentials" behaviour. Set it once the
beta is over.

Secrets never leave this module. `public_defaults()` reports only *whether* a
default exists, plus the non-secret URL, database and model name.
"""
import configparser
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "config.ini"


def enabled() -> bool:
    return (os.environ.get("ODOO_GENERATOR_CONFIG_DEFAULTS") or "on").lower() not in (
        "off", "0", "false", "no"
    )


def _read_ini() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    path = Path(os.environ.get("ODOO_GENERATOR_CONFIG_FILE") or _CONFIG_PATH)
    if path.exists():
        try:
            parser.read(path, encoding="utf-8")
        except Exception as exc:
            logger.warning(f"[config] {path.name} nicht lesbar: {exc}")
    return parser


def _ini(parser: configparser.ConfigParser, section: str, key: str) -> Optional[str]:
    try:
        value = (parser.get(section, key) or "").strip()
    except (configparser.NoSectionError, configparser.NoOptionError):
        return None
    return value or None


def defaults() -> Dict[str, Optional[str]]:
    """Resolved defaults, secrets included. Never serialise this."""
    if not enabled():
        return {}
    parser = _read_ini()
    return {
        "url": _ini(parser, "odoo", "url"),
        "db": _ini(parser, "odoo", "db"),
        "odoo_key": os.environ.get("ODOO_API_KEY") or _ini(parser, "odoo", "api_key"),
        "llm_key": (os.environ.get("GROQ_API_KEY")
                    or os.environ.get("GEMINI_API_KEY")
                    or _ini(parser, "llm", "api_key")
                    or _ini(parser, "gemini", "api_key")),
        "llm_model": _ini(parser, "llm", "model") or _ini(parser, "gemini", "model"),
    }


def apply(field: str, supplied: Optional[str]) -> Optional[str]:
    """Return the supplied value, or the operator default when it is blank."""
    value = (supplied or "").strip()
    if value:
        return value
    return (defaults().get(field) or "").strip() or None


def public_defaults() -> Dict[str, Any]:
    """What the UI may know: the non-secret values, and *whether* keys exist.

    Placeholder values are still real config content — the demo hostname is
    prospect-identifying — so this endpoint is behind the access code like every
    other one. It never carries a key.
    """
    resolved = defaults()
    return {
        "enabled": enabled(),
        "url": resolved.get("url"),
        "db": resolved.get("db"),
        "llm_model": resolved.get("llm_model"),
        "has_odoo_key": bool(resolved.get("odoo_key")),
        "has_llm_key": bool(resolved.get("llm_key")),
    }
