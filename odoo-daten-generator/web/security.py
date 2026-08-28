"""Guards A and B — target validation for every Odoo instance the server talks to.

Two independent guards share one host regex but exist for different reasons; both
are required and neither replaces the other.

**Guard A — wrong target.** The tool must only ever write into a throwaway
``demo-*.odoo.com`` instance. The Odoo API-key privilege model does *not* cover
this: a key carries its creator's rights, and consultants do have write access to
customer production — so the key would happily write demo data into a real
customer database. That accident is what Guard A prevents.

**Guard B — SSRF.** The *server* makes the request, from inside the operator's
home network, on a box that also hosts their other private services. On top of
Guard A's host check this rejects non-https schemes, embedded userinfo
(``https://demo-ok.odoo.com@evil.example/``) and port overrides
(``https://demo-x.odoo.com:8080/`` — ``urlsplit().hostname`` strips the port, so
the host regex alone passes it). The remaining half of Guard B lives in
``odoo_client._post``: redirects are refused outright, so an allowed host cannot
302 the request onward to ``169.254.169.254`` while this validation still reads
"passed", and HTTP error bodies are reduced to their structured Odoo message so
a response body never crosses the API boundary verbatim.

Guard B stays enabled in the local profile too. A security control that is
switched on by configuration is a security control that eventually ships off.
"""
import re
from typing import Optional
from urllib.parse import urlsplit

# Guard A / Guard B share this. Mirrored client-side in static/app.js purely for
# fast feedback; the server validates independently and is the only authority.
DEMO_HOST_RE = re.compile(r"^demo-[a-z0-9-]+\.odoo\.com$")

# Odoo database names are subdomain-shaped on SaaS. Validated because the value
# is user-supplied and goes into the X-Odoo-Database request header.
DB_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class TargetUrlError(ValueError):
    """Raised when a target URL fails Guard A or Guard B."""


def validate_target_url(raw: Optional[str]) -> str:
    """Validate a user-supplied Odoo target URL and return its normalised form.

    Returns ``https://<host>`` with no trailing slash, path, query or fragment —
    the exact shape ``OdooJson2Client.__init__`` expects to append ``/json/2`` to.

    Raises TargetUrlError with a message safe to show the user.
    """
    if not raw or not isinstance(raw, str):
        raise TargetUrlError("Keine Ziel-URL angegeben.")

    candidate = raw.strip()
    if not candidate:
        raise TargetUrlError("Keine Ziel-URL angegeben.")
    # Whitespace *inside* the URL (CR/LF request-splitting attempts included).
    if any(ch.isspace() for ch in candidate):
        raise TargetUrlError("Ziel-URL enthält Leerzeichen oder Zeilenumbrüche.")

    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise TargetUrlError(f"Ziel-URL nicht lesbar: {exc}") from exc

    if parts.scheme.lower() != "https":
        raise TargetUrlError("Nur https:// ist erlaubt.")

    if parts.username is not None or parts.password is not None:
        raise TargetUrlError("Ziel-URL darf keine Zugangsdaten (user:pass@) enthalten.")

    try:
        port = parts.port
    except ValueError as exc:
        raise TargetUrlError(f"Ungültiger Port in der Ziel-URL: {exc}") from exc
    if port is not None:
        raise TargetUrlError("Ein abweichender Port ist nicht erlaubt.")

    host = (parts.hostname or "").lower()
    if not DEMO_HOST_RE.match(host):
        raise TargetUrlError(
            "Diese URL ist keine demo-*.odoo.com-Instanz. Aus Sicherheitsgründen blockiert."
        )

    if parts.path not in ("", "/"):
        raise TargetUrlError("Ziel-URL darf keinen Pfad enthalten.")
    if parts.query or parts.fragment:
        raise TargetUrlError("Ziel-URL darf keine Query-Parameter oder Fragmente enthalten.")

    return f"https://{host}"


def validate_database_name(raw: Optional[str]) -> str:
    """Validate the Odoo database name (goes into a request header, so no CR/LF)."""
    if not raw or not isinstance(raw, str):
        raise TargetUrlError("Kein Datenbankname angegeben.")
    candidate = raw.strip()
    if not DB_NAME_RE.match(candidate):
        raise TargetUrlError("Datenbankname enthält unzulässige Zeichen.")
    return candidate
