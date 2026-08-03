"""Shared text helpers for deterministic (non-LLM) name-derived values."""

import re

_UMLAUT_MAP = str.maketrans({
    'ä': 'ae', 'ö': 'oe', 'ü': 'ue',
    'Ä': 'ae', 'Ö': 'oe', 'Ü': 'ue',
    'ß': 'ss',
})


def slugify(text: str) -> str:
    """Transliterate umlauts before stripping non-alphanumerics, so German
    names don't get mangled (e.g. "Müller" -> "mueller", not "m.ller")."""
    ascii_text = text.translate(_UMLAUT_MAP).lower()
    return re.sub(r'[^a-z0-9]+', '.', ascii_text).strip('.')


def email_from_name(name: str, domain: str = "example.com") -> str:
    return f"{slugify(name)}@{domain}"
