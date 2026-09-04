# Roadmap — odoo-daten-generator

**Stand:** 2026-07-20 · **Basis:** Code-Review aller Kernmodule (gui.py, orchestrator.py, llm_service.py, odoo_client.py, modules/*, tests/*)

**Umbenannt von `IMPLEMENTIERUNGSPLAN.md`, 2026-08-29:** Inhalt und Historie unverändert — der Name passt seit S1–S10 (10 abgeschlossene Sprints) besser als "Plan". Gleichzeitig um R11–R20 erweitert (Erweiterungen an bestehenden Modulen + vier neue App-Domänen), Peer-reviewed nach dem etablierten S5-S10-Verfahren (fremder Opus-Agent, Plan-Text + Live-Repo, keine Konversationshistorie) vor dem Commit.

Dieses Dokument ist der Implementierungsrahmen für die nächsten Entwicklungszyklen. Es gliedert sich in:

1. [Leitprinzip: LLM-Minimalismus](#1-leitprinzip-llm-minimalismus)
2. [Bugs & Logikfehler](#2-bugs--logikfehler) (priorisiert, mit konkretem Fix)
3. [Architektur- & Design-Verbesserungen](#3-architektur--design-verbesserungen)
4. [Weiterentwicklung / Roadmap](#4-weiterentwicklung--roadmap) (inkl. PDF-Generierung)
5. [Umsetzungsreihenfolge](#5-umsetzungsreihenfolge)

Kennzeichnung: 🔴 kritisch · 🟠 hoch · 🟡 mittel · ⚪ niedrig · 🔒 = berührt "Do Not Touch"-Bereich aus CLAUDE.md → Architekten-Freigabe erforderlich.

**2026-09-02 aufgeteilt:** Diese Datei enthält nur noch offene/geplante Punkte (kein ✅). Erledigte Bugs/Design-Punkte/Roadmap-Items stehen jetzt in [`ROADMAP_ARCHIVE.md`](ROADMAP_ARCHIVE.md), das Sprint-für-Sprint-Narrativ in [`odoo-daten-generator/SPRINT_LOG.md`](odoo-daten-generator/SPRINT_LOG.md).

---

## 1. Leitprinzip: LLM-Minimalismus

**Maxime:** Das LLM liefert ausschließlich *atomare kreative Bausteine* (Namen, Bezeichnungen, Textkörper), niemals fertige Importstrukturen. Alles Strukturelle — Adressen-Zusammenbau, E-Mails, Telefonnummern, Preise, Mengen, Datumswerte, Record-Verschachtelung — wird deterministisch im Code erzeugt.

**Begründung:**
- Weniger Output-Tokens (Kosten, Latenz, Timeout-Risiko)
- Keine invaliden Felder mehr aus dem LLM (das aktuelle Filtern von `uom`, `vat`, `detailed_type` etc. wird überflüssig)
- Strukturfehler (fehlende Pflichtfelder, falsche Kontakt-Typen) unmöglich, weil Struktur im Code liegt
- Reproduzierbarkeit und Testbarkeit steigen (Struktur ist unit-testbar ohne LLM)

## 3. Architektur- & Design-Verbesserungen

### D5 🟡 Typisierte Modul-Configs statt roher Dicts 🔒

`ModuleSelections.mrp/hr_recruitment/hr_timeoff/crm_chatter/crm_activities` sind untypisierte Dicts mit Shape-Kommentaren. Fehlerklasse: Tippfehler im Key fällt erst zur Laufzeit (oder nie) auf.

**Fix (nach Architekten-Freigabe, da Config-Schema):** je ein Dataclass (`MrpConfig`, `TimeoffConfig`, …) mit Defaults; `ModuleSelections` referenziert `Optional[MrpConfig]`. GUI erzeugt die Objekte direkt. `enabled`-Bools entfallen (Objekt vorhanden = aktiv).

### D6 🟡 Namens-Hygiene: `gemini` → `llm`

Parameter heißt in allen Modulsignaturen `gemini`, Provider ist primär Groq; `RunContext.gemini_model_name` ist ungenutztes Erbe. Umbenennen (`llm`, `llm_model_name`), rein mechanisch.

### D8 ⚪ Kleinigkeiten

**Teilstatus, verifiziert 2026-09-02** — 2 von 5 erledigt, 3 offen:

- ⚪ **Offen:** `test_mrp_live.py` steht weiterhin im Wurzelverzeichnis von `odoo-daten-generator/` → nach `tests/integration/` verschieben oder löschen.
- ✅ **Erledigt (durch Umbau, nicht gezielt):** die ursprüngliche `odoo_client._post:46`-Stelle mit dem immer-wahren `response is not None`-Check existiert so nicht mehr — `odoo_client.py`s Fehlerbehandlung wurde in S9/S10 komplett umgebaut (`_record_failure`-Frame-Stack). Die verbleibenden `response is not None`-Checks (z. B. `create_batch`s HTTPError-Handler, `has_create_access`) sind echte Null-Checks, keine toten Bedingungen mehr.
- ✅ **Erledigt:** `LLMService.ping()` existiert (`llm_service.py:262`) und wird von `connect_service.py:245` verwendet — ohnehin gegenstandslos, da `gui.py` seit S9 komplett entfernt ist.
- ⚪ **Offen:** Provider-Erkennung ist weiterhin Prefix-Sniffing — `connect_service.py:126`: `"groq" if llm_key.startswith("gsk_") else "gemini"`. Kein explizites Dropdown/Feld für die Provider-Wahl im Web-Frontend.
- ⚪ **Offen:** `orchestrator.py:54` — `gemini.fetch_name_suggestions(...)` läuft weiterhin unconditional bei jedem Lauf, außerhalb des `skip_master_data`-Gates (Zeile 46). Lädt Namensbänke auch dann, wenn kein Modul sie braucht.

### D10 ✅ Erledigt (2026-09-03) — Proaktives Rate-Limiting in `odoo_client.py` 🔒

**Herkunft:** während S12/WP1 (Barcode) beobachtet — 3 aufeinanderfolgende volle
`test_suite.py`-Läufe gegen `demo-test5.odoo.com`, jedes Mal genau 1 Fehlschlag im
`ODOO_ACTIONS`-Live-Testblock (`tests/integration/test_odoo_actions.py`), aber jeweils eine
**andere** der beiden "exakt N POSTs"-Assertionen (`has_create_access`s Ein-POST-Check
einmal, `probe_model_access`s Drei-POST-Check ein anderes Mal) — Muster passt zu
Netzwerk-Timing, nicht zu einem deterministischen Regressions-Bug. Beide betroffenen
Assertionen zählen echte `session.post`-Aufrufe; ein zusätzlicher Aufruf entsteht nur, wenn
`odoo_client._send` (Zeile 225-241) auf einen `429`/`503` mit einem Retry reagiert
(`_RETRY_STATUSES`, Zeile 41). Die Retry-Logik selbst ist **reaktiv** — sie greift erst,
nachdem ein Request bereits mit 429 abgelehnt wurde; es gibt aktuell **keinen** proaktiven
Drosselungspunkt, der verhindert, dass Anfragen überhaupt zu schnell aufeinanderfolgen.
CLAUDE.md dokumentiert das Instanz-Verhalten selbst: "Demo SaaS instances rate-limit at
~1 req/s sustained (burst ~150 req), bare HTML 429, no Retry-After" — ein voller Testlauf
(80+ Live-Schritte, viele mit mehreren API-Calls) feuert deutlich dichter als 1 req/s,
sobald mehrere Module kurz hintereinander laufen.

**Vorschlag:** ein einfacher proaktiver Drosselpunkt in `odoo_client.py` — z. B. ein
Mindestabstand (Token-Bucket oder simples "letzter Request war vor < X ms, dann schlafen")
direkt vor jedem `session.post`-Aufruf in `_send`, konfigurierbar, Default ~1 req/s passend
zur dokumentierten Instanz-Grenze. Reduziert/eliminiert die 429-Retry-induzierte Flakiness
bei den exakten POST-Zähl-Assertionen **und** verbessert die Robustheit echter Nutzer-Läufe
gegen Demo-SaaS-Instanzen allgemein (weniger 429-Fehlschläge im Fehlerbericht, nicht nur in
Tests) — zwei Nutzen für eine kleine, additive Änderung.

**🔒-Hinweis:** `odoo_client.py` steht auf der "Do Not Touch"-Liste (JSON2-Payload-Format),
aber diese Änderung berührt nur das Timing vor dem Senden, kein Payload-Format — analog zu
S10 Phase A, wo `has_create_access`s Zugriffsproben additiv in `odoo_client.py` ergänzt
wurden, ohne das Payload-Format anzufassen (die eigentlich geschützte Sache). Vor Umsetzung
trotzdem kurz beim Architekten bestätigen, reiner Formsache-Fall (kein Verhaltensrisiko),
aber die Datei ist gelistet.

**Komplexität:** Niedrig · **Benefit:** Mittel (Test-Stabilität) + Niedrig-Mittel
(Produktions-Robustheit).

**Umsetzung (2026-09-03, Branch `d10-rate-limiting`):** `OdooJson2Client.
_send` ruft vor jedem Attempt (auch bei Retries) eine neue `_throttle()`
auf, die genau so lange schläft, dass zwei aufeinanderfolgende Requests
dieses Clients mindestens `min_request_interval` Sekunden auseinander
liegen (`time.monotonic`, nie Wall-Clock). **Per Instanz konfigurierbar**,
nicht als eingefrorene Modul-Konstante beim Import gelesen — ein Aufrufer
kann `min_request_interval=0` an den Konstruktor übergeben, um für genau
diesen Client abzuschalten (production-Aufrufstellen lassen den Parameter
auf `None`, was bei Konstruktion die Env-Var `ODOO_GENERATOR_
MIN_REQUEST_INTERVAL` liest, Default `1.0`). Diese Instanz-statt-Modul-
Entscheidung war nötig, nicht kosmetisch: ein erster Versuch mit
eingefrorener Modul-Konstante brach `test_web_security_unit.py`s Backoff-
Test (`slept.call_count == 2`) — mit gemocktem `time.sleep` vergeht
zwischen Attempts real keine Zeit, also löste der Drosselpunkt bei jedem
Retry zusätzlich aus. Die drei betroffenen Testdateien (`test_odoo_client_
unit.py`, `test_web_security_unit.py`) übergeben jetzt explizit
`min_request_interval=0` an ihre eigenen Test-Clients; production bleibt
unberührt (`connect_service.py`, `web/app.py`, `tests/integration/
test_suite.py` konstruieren weiterhin ohne den Parameter). 6 neue Unit-
Tests (`_throttle` selbst, Clamp auf negativen Wert, Env-Var-Read bei
Konstruktion, `_send` ruft `_throttle` vor jedem Attempt). Unit 425/425,
Live-`test_suite.py` 92/92 grün (inkl. des vorher flakigen `ODOO_ACTIONS`-
Blocks, der diesmal sauber durchlief — ein einzelner grüner Lauf beweist
die Flake-Elimination nicht abschließend, ist aber ein gutes Zeichen).

---

### Referenz — Registrierungskette für ein neues orchestriertes Modul

**Herkunft:** bei R19 (Expenses, S12/WP2) gefunden — die ursprüngliche R19-Planung hatte nur
`config.py` + `orchestrator.py` genannt, ohne die Kette lief das Feature nie (B1-Fehlerklasse).
R12 und R18 verwiesen schon vorher auf einen "Implementierungshinweis oben", der nie
ausgeschrieben war — diese Liste ist jetzt die kanonische Fassung, hierher verlinken statt neu
herzuleiten. Archiv-Kontext (Blocker-Historie) in `ROADMAP_ARCHIVE.md`s R19-Statusblock.

Für jedes neue orchestrierte Modul (eigener `orchestrator.py`-`module_order`-Eintrag, eigene
GUI-Karte, eigenes `ModuleSelections`-Feld):
1. `run_config.WANTED_MODULES` — Odoo-Technikname aufnehmen, sonst liefert
   `odoo_actions.get_installed_modules` das Modul nie in `ctx.installed_modules`, und der
   `orchestrator.py`-Gate ist tote Logik.
2. `run_config.MODULE_LABELS` — GUI-Anzeigename; von `test_run_config_unit.py`s
   `"WANTED_MODULES enthält purchase und stock"`-Test gegen `WANTED_MODULES` auf
   Vollständigkeit geprüft.
3. `run_config.MODULE_RUN_ORDER` — Position exakt so wie in `orchestrator.py`s
   `module_order`; ein eigener Test prüft `positions == sorted(positions)` gegen den
   Literal-Text von `orchestrator.py`. **Ausnahme:** ein Modul, das bewusst log-only ohne
   Fortschrittszeile bleiben soll (siehe R11/`crm_lost`), lässt diesen Schritt aus — dann
   auch keinen `orchestrator.py`-Sonderfall-Kommentar vergessen, der das begründet.
4. `run_config.build_selections` — ohne diesen Eintrag bleibt das `ModuleSelections`-Feld
   beim Default, das Frontend-Formularfeld landet nie in `ctx.module_selections`.
5. `run_config.estimate_record_counts` — sonst keine Vorschau-Zeile im Prüfen-Screen; kein
   Crash, aber eine stille Lücke.
6. `static/app.js`: `MODULE_DEFS` **und** `ICONS` — ein fehlender Icon-Key rendert ein leeres
   `<svg>`, kein Crash, aber optisch kaputt.
7. `tests/unit/test_run_config_unit.py`: `_FULL`-Payload und `_ALL_INSTALLED` — Letzteres wird
   wörtlich gegen `selected` geprüft, muss um den neuen Key erweitert werden, sonst schlägt
   der Test beim ersten `build_selections`-Aufruf fehl, der das neue Feld tatsächlich setzt.

Modul-Key-Konvention (aus demselben R19-Fund): der Key muss überall identisch sein — Odoo-
Technikname, `WANTED_MODULES`, `MODULE_RUN_ORDER`, das `orchestrator.py`-Tupel, der
`app.js`-Karten-Key — **nicht** ein eigener, "sprechenderer" Name (R19 wollte ursprünglich
`expenses` statt `hr_expense`). Präzedenzfälle: `hr_recruitment`, `hr_timesheet`, `hr_expense`.

Optionale Ergänzung, kein Blocker: `odoo_actions.py`s `FIELD_COMPAT_WHITELIST`/
`MODEL_ACCESS_PROBES`/`PRIMARY_MODEL_PER_MODULE` um das neue Modul/Modell erweitern, damit
S11s Kompat-Check und Zugriffsproben es mit abdecken (ein fehlender Eintrag ist nicht fatal —
`model_access`-Lookups sind default-open — aber eine stille Abdeckungslücke).

---

## 4. Weiterentwicklung / Roadmap

### R1 🟠 PDF-Generierung & -Einspielung

**Ziel:** Realistische Dokumente im System — der größte sichtbare Demo-Mehrwert.

**Technik-Empfehlung:** `fpdf2` (pures Python, keine Systemabhängigkeiten wie cairo/pango — wichtig für Windows-Kompatibilität, vgl. ThreadPoolExecutor-Entscheidung). Upload nach Odoo als `ir.attachment`:

```python
import base64
client.create('ir.attachment', {
    "name": "Rechnung_2026-1042.pdf",
    "res_model": "account.move",
    "res_id": move_id,
    "type": "binary",
    "datas": base64.b64encode(pdf_bytes).decode(),
    "mimetype": "application/pdf",
})
```

**Ausbaustufen:**

| Stufe | Inhalt | Odoo-Ziel |
|---|---|---|
| P1 | **Eingangsrechnungs-PDFs** (Lieferanten-Briefkopf, Positionen, Beträge passend zur Vendor Bill) | Anhang an `account.move` (Entwurf) → Demo für Belegdigitalisierung/OCR; alternativ per `message_post` mit Attachment |
| P2 | **Bewerbungsunterlagen** (CV pro Bewerber: Name, Skills aus der generierten Taxonomie, Werdegang-Stichpunkte via LLM-Batch-Call) | Anhang an `hr.applicant` |
| P3 | **Lieferscheine/Bestellungen** von Lieferanten | Anhang an Purchase-Belege (setzt R2 voraus) |
| P4 | **Verträge** (Wartungsvertrag zu gewonnenen Opportunities, 1-Seiter) | Anhang an `crm.lead` / `sale.order` |

Hinweis: Ausgangsbelege (Angebote, Kundenrechnungen) erzeugt Odoo selbst über die Report-Engine — hier **nicht** nachbauen. Der Wert liegt bei *eingehenden* Dokumenten, die es sonst nicht gäbe.

LLM-Maxime gilt auch hier: LLM liefert nur Textbausteine (Positionstexte, CV-Stichpunkte) — Layout, Zahlen und Struktur macht `pdf_factory.py` deterministisch.

**Neue Dateien:** `pdf_factory.py` (Rendering), `modules/documents.py` (Pipeline-Schritt, läuft **nach** accounting/recruiting — 🔒 Pipeline-Reihenfolge, Architekten-Freigabe). GUI: Checkbox je Stufe im jeweiligen Modul-Panel.

**Teilstatus, verifiziert 2026-09-02:** P1 (Eingangsrechnungs-PDFs) und P2 (Bewerbungsunterlagen) implementiert — `pdf_factory.py` (`build_vendor_bill_pdf`/`build_cv_pdf`, 5 Layout-Varianten seit S10) + `modules/documents.py` (`create_documents`, gated auf `ctx.model_access` seit S10). P3 (Lieferscheine/Bestellungen, setzt R2/Purchase voraus — R2 ist seit S8 fertig, P3 selbst nicht begonnen) und P4 (Verträge) sind **nicht** umgesetzt — kein `build_`-Pendant in `pdf_factory.py`, kein Aufruf in `modules/documents.py` oder `modules/purchase.py`. R1 bleibt offen für P3/P4.

### R5 🟠 API-Versions-Kompatibilität — Registry + Übersetzungspunkt (überarbeitet 2026-09-02, hohe Priorität)

**Status:** Tier 1 (Versions-Erkennung + Feld-Warnliste) seit 2026-08-04 implementiert und
live geprüft — siehe Baustein 1 unten, unverändert. Der ursprüngliche Tier-2-Plan (nutzer­
editierbare `api_versions/<version>.json`-Mapping-Dateien + Adapter) ist **verworfen**,
zugunsten eines schlankeren, im Chat vom 2026-09-02 erarbeiteten Designs — siehe WP1-WP5
unten. Auslöser: Nutzerfrage zur V20-Absicherung; im selben Gespräch wurde auch festgestellt,
dass V20-Beta bereits am 2026-08-29 live getestet wurde ([PR #20](https://github.com/pahuodoo/odoo-daten-generator/pull/20)) —
V20-Beta meldet sich intern als `saas-19.5`, ein `ir.attachment.raw`-Read-Shape-Wechsel war
der einzige reale Fund, Fix ist bereits gemerged. Kein Rename in zwei geprüften
Versions-Übergängen (19.2→19.4, 19.4→19.5) bislang — die Registry unten startet deshalb
bewusst leer, sie ist die Leitung, nicht der Inhalt.

**Eingeplant als S11** (2026-09-02, Nutzerentscheidung, siehe §5) — vor der bereits
vorgesehenen "Quick Wins"-Sprint, die dafür zu S12 verschoben wird. Verifiziert vor
Sprintstart: `ir.attachment.raw` wird produktionsseitig nur geschrieben, nie gelesen
(`modules/documents.py:184,251`) — WP3 startet damit als reine, folgenlose Infrastruktur
(leere Registry, Regressionstest auf byte-gleiche Payloads), kein echter Eintrag ist
aktuell belegt.

**Warum das JSON-Mapping-Design verworfen wurde:** zwei Lücken, beide im Chat gefunden.
(1) `fields_get`-basierte Prüfungen sehen nur "Feld weg/da", nicht "Feld da, aber Laufzeit-
Form geändert" — exakt der V20-Fund (`ir.attachment.raw` existiert weiter, liefert aber ein
Dict statt eines Strings). Eine Rename-Map hätte das nicht behoben, nur eine Werttransformation.
(2) Der ursprüngliche Plan hätte pro Request-Kontext übersetzt; belastbare Praxis (SQLAlchemy-
Dialects, Kubernetes-Client-Discovery) löst das genau einmal beim Connect, nicht pro Call —
schlanker und vermeidet exakt das Muster, das die entfernte JSON2-Payload-Fallback-Kette schon
einmal als Fehlkonstruktion gezeigt hat (blindes Pro-Call-Probieren statt einmaliger,
belegter Prüfung).

**Neues Design — fünf Arbeitspakete:**

#### WP1: Dynamisches Feld-Manifest statt Hardcoded-Whitelist

`FIELD_COMPAT_WHITELIST` (`odoo_actions.py:390`) ist von Hand kuratiert und bereits nachweislich
unvollständig — Stichprobe `res.partner` (2026-09-02): tatsächlich geschriebene Felder sind
`name, street, zip, city, country_id, email, phone, website, is_company, parent_id, type`
(`modules/master_data.py:110-150`, `data_factory.build_company`/`build_contacts`), die
Whitelist kennt nur 8 von 11 — `country_id`/`parent_id` (beide Many2one, höchstes
Rename-Risiko) und `type` (Selection, laut Docstring bereits historisch fragil) fehlen.

Statische AST-Analyse über `modules/*.py` ist hier ungeeignet — Felder werden über
`data_factory.py`-Hilfsfunktionen zusammengesetzt, nicht als Literal am Call-Site.
Stattdessen: Laufzeit-Capture. Ein Env-Flag (`ODOO_GENERATOR_CAPTURE_FIELDS=1`) an
`odoo_client.py`s einzigem Übersetzungspunkt pro Operation (`create`/`create_batch`/`write`/
`call_method`) protokolliert jedes tatsächlich gesendete `(model, field)`-Paar. Ein Lauf von
`tests/integration/test_suite.py` mit diesem Flag erzeugt das vollständige, echte Manifest —
committed, regeneriert bei Bedarf, optional per CI gegen den committed Stand geprüft (Drift
= neues Feld ohne Whitelist-Eintrag).

#### WP2: Zugriffs-Ebenen komponieren (Feld-Warnung ↔ Nicht-installiert ↔ Kein Schreibzugriff)

Drei Signale existieren, zwei davon bereits gebaut, aber nicht komponiert:
1. **App nicht installiert** → `installed_modules`, gattert `FIELD_COMPAT_WHITELIST`
   bereits korrekt (`odoo_actions.py:439-441`).
2. **App installiert, Schreibzugriff blockiert** (der MRP/Workcenter-Fall aus S10:
   `mrp.workcenter` meldete sich per Lese-Probe als vorhanden, obwohl die Einstellung
   "Arbeitsaufträge" aus war — `has_access` ist die Lösung, `odoo_client.has_create_access`/
   `odoo_actions.probe_model_access`, S10) — **wird `check_field_compatibility` heute nicht
   übergeben.** Beleg: `connect_service.py:213` ruft `check_field_compatibility(client,
   installed_modules=mods)` — `result.model_access` (Zeile 197, zwei Schritte vorher im selben
   Connect-Flow bereits berechnet) fließt nicht ein.
3. **App installiert, Schreibzugriff da, Feld fehlt trotzdem in `fields_get`** — erst dieses
   Signal ist ein echter Versions-/Schema-Fund.

Fix: `check_field_compatibility` bekommt `model_access` als zusätzlichen Parameter, gattert
zusätzlich zu `installed_modules`. Kein neuer Mechanismus — nur korrekte Verdrahtung von S10s
eigenem Ergebnis. Eine Feld-Warnung ist danach eindeutig: installiert, schreibbar, trotzdem
fehlend → garantiert ein Versions-Fund, nie Rauschen aus Install-/Rechte-Zustand.

#### WP3: Übersetzungs-Registry + ein Übersetzungspunkt 🔒 — **zurückgestellt, siehe unten**

**Status 2026-09-02 (S11 Phase B, nach Cold-Review):** zurückgestellt, nicht verworfen.
Peer-Review (1 fremder Opus-Agent, Plantext + Live-Repo, keine Konversationshistorie —
gleiches Verfahren wie S5-S10) fand vier Blocker in der ursprünglichen Fassung unten:

1. **Keine Verdrahtung von Connect zum echten Lauf-Client.** Drei getrennte
   `OdooJson2Client`-Instanzen existieren (`connect_service.probe()`s Client,
   `JournalingClient` in `web/jobs.py` — der Client, der die eigentlichen Schreibvorgänge
   macht —, und ein dritter für D7-Cleanup in `web/app.py`). Eine in `probe()` aufgelöste
   Tabelle erreicht keinen davon; der Transportweg war im ursprünglichen Design nicht
   spezifiziert.
2. **Die vier genannten Hook-Punkte decken nur den Schreibpfad ab.** Modell-Umbenennung
   (im URL-Pfad *jeder* Methode, auch `search`/`search_read`) und der einzige reale
   Präzedenzfall (`ir.attachment.raw`s Laufzeit-Form-Wechsel, ein *Lese*-Transform) sind
   von `create`/`create_batch`/`write`/`call_method` aus nicht erreichbar — genau der Fund,
   den WP3 laut Grenztabelle unten eigentlich abdecken soll.
3. **`create_batch`s 404/422-Fallback ruft intern `create()` auf** — ein an beiden Stellen
   angewandter Transform würde auf diesem Pfad doppelt greifen.
4. Die Begründung "kein gemeinsamer Übersetzungspunkt existiert heute" war ungenau —
   `_post()` ist ein echter gemeinsamer Punkt; was fehlt, ist Wissen um die Payload-Form
   (`vals_list` vs. `vals` vs. nackte kwargs), nicht der Aufrufpunkt selbst.

**Reviewer-Einschätzung zur eigentlichen Anti-Pattern-Frage** (explizit nicht nur die eigene
Begründung dieses Dokuments bestätigen sollen): WP3 ist **kein** Wiederauflegen der entfernten
JSON2-Payload-Fallback-Kette — die alte Kette war aktiv (verbrannte Requests, änderte
Kontrollfluss, maskierte echte Fehler bei jedem Call), eine leere Registry mit
Byte-gleich-Regressionstest ist es nicht. Trotzdem empfohlen: **zurückstellen**, weil (a) der
einzige Präzedenzfall (`ir.attachment.raw`) live bestätigt nur *geschrieben*, nie in
Produktionscode *gelesen* wird (`modules/documents.py:184,251` — Lesezugriff existiert nur in
Test-Assertions), also kein Produktions-Fund vorliegt, und (b) das Design ihn ohnehin nicht
hätte auffangen können (Blocker 2) — Infrastruktur "für den nächsten Fund" gebaut, die den
letzten Fund nachweislich nicht aufgefangen hätte, ist schwache Infrastruktur.

**Auslöser für Wiederaufnahme:** der erste Fund aus einem `scripts/check_compat.sh`-Lauf
(WP5, S11 Phase B umgesetzt), den die Grenztabelle unten mit ✅ markiert (Registry-Eintrag
reicht). Bis dahin bleibt dieser Punkt offen im Backlog, siehe `§5` Sprinttabelle.

**Falls doch vorgezogen — Mindeststandard, den die Umsetzung dann erfüllen muss:**
- Blocker 1-3 oben lösen, bevor der erste Code entsteht.
- Reviewer-Alternative statt Direkt-Patch an `odoo_client.py`: **Subclass** (`class
  VersionAdaptingClient(OdooJson2Client)`, eigene Datei) statt Änderung der 🔒-Datei selbst
  — genau das Muster, das `run_journal.JournalingClient`s eigener Docstring bereits
  begründet ("Subclass rather than a patch: journaling is a separate concern from how
  create/create_batch build their request"). Löst NICHT Blocker 1 (die drei
  Client-Instanzen) — verlagert nur, wo die Verdrahtung passiert.
- Regressionstest muss `(url, payload)`-Paare vergleichen, nicht nur Payloads — sonst keine
  echte Absicherung gegen eine Modell-Umbenennung im URL-Pfad.
- Versions-Schlüssel-Semantik vorab festlegen und dokumentieren: exaktes Match oder "diese
  Version und später"? (`classify_version_status`, Phase A, hat dieselbe Exact-Match-Eigenschaft
  — ein Eintrag für "19.5" gilt nicht mehr ab 19.6/20.0, genau dann, wenn er gebraucht würde.)

**Ursprüngliches Design (Referenz, nicht mehr aktueller Umsetzungsplan):**

Ersetzt Baustein 2+3 des alten Plans. Statt nutzereditierbarer JSON-Dateien mit eigener
Vererbungs-/Transformations-DSL: eine kleine, **in Code gepflegte** Registry (nur von
Entwickler/Claude befüllt, kein End-Nutzer-Editier-Pfad — der war ohnehin nie ein reales
Bedürfnis, `IMPLEMENTIERUNGSPLAN.md`s ursprüngliche Prämisse dafür war unbelegt):

```python
# Beispielform, nicht final — startet LEER (siehe Status oben, keine Renames bisher belegt)
FIELD_OVERRIDES: Dict[str, Dict[str, Dict[str, Any]]] = {
    "19.5": {
        "ir.attachment": {"raw": {"read_transform": normalize_attachment_raw}},
    },
}
```

Aufgelöst **einmal pro Connect** (nicht pro Request — Analogie: SQLAlchemy wählt seinen
Dialect einmal bei Connect, nicht pro Query) zu einer pro-Lauf gültigen Übersetzungstabelle.
`odoo_client.py`s `create`/`write`/`call_method` konsultieren sie an ihrem bereits
bestehenden einzigen Übersetzungspunkt pro Operation, bevor der Payload rausgeht — kein
Modul in `modules/*.py` ändert sich. 🔒 `odoo_client.py` betroffen — **Architekten-Freigabe
hiermit erteilt** (Chat 2026-09-02: "wir werden es mit hoher Priorität umsetzen").

Kein Plugin-System, keine Ausdrucks-DSL — bleibt bei reiner Umbenennung/Wert-Transformation/
Weglassen. Ein Fund, der mehr braucht (siehe Grenztabelle unten), wird ein echter Code-Zweig,
keine Registry-Zeile.

#### WP4: `LAST_VERIFIED_VERSION`-Marker ✅ (S11 Phase A, 2026-09-02)

Kleine Konstante (`odoo_actions.LAST_VERIFIED_VERSION`, aktuell `"19.4"`), gepflegt bei jedem
sauberen Durchlauf von WP5 gegen eine neue Version — nie sonst. `classify_version_status`
liefert drei Zustände statt vorher nur "Version erkannt oder nicht":
`known_good`/`known_broken_with_fix` (letzteres über `KNOWN_BROKEN_VERSIONS`, startet leer,
gleiche Logik wie WP3s Registry) /`untested`. Im Connect-Checklist als
`19.4 (geprüft)`/`(ungetestet)`/`(bekannte Probleme, Fix aktiv)` sichtbar — reine
Anzeigelogik im bestehenden `version`-Step-Detail, kein Frontend-Code nötig.

#### WP5: `scripts/check_compat.sh` ✅ (S11 Phase B, 2026-09-02) — formalisierte Versions-Prüfung (Dev-seitig)

Formalisiert, was am 2026-08-29 für V20-Beta ad-hoc gemacht wurde: `tests/test_config.ini`
auf eine neue/Beta-Instanz zeigen, `./scripts/check_compat.sh` ausführen (voller
`test_suite.py`-Lauf mit `ODOO_GENERATOR_CAPTURE_FIELDS=1`, verlässt sich auf den
Exit-Code, kein Output-Parsing), Funde bei Fehlschlag nach der Grenztabelle unten triagieren
(Registry-Zeile vs. echter Code-Zweig), `LAST_VERIFIED_VERSION` **manuell** bumpen — das
Skript tut das bewusst nicht selbst. **Läuft nicht automatisch bei einem Nutzer-Lauf** — hat
Seiteneffekte (echte Datensätze auf der Zielinstanz), dauert Minuten, ist eine bewusste,
entwicklerseitige Freigabe-Aktion pro neuer Odoo-Version, kein Teil des Produkts selbst.
Live verifiziert (2026-09-02, demo-test5): PASS- und FAIL-Zweig beide durchlaufen, korrekter
Exit-Code in beiden Fällen.

**Cadence-Zusammenfassung:** WP2/WP4 (Erkennung, Zugriffs-Ebenen) laufen bei **jedem**
Connect — billig, read-only, pro Zielinstanz unterschiedlich, lässt sich nicht vorberechnen.
WP1 (Manifest) regeneriert sich bei Code-Änderungen, nicht bei Odoo-Releases — eigener,
unabhängiger Auslöser. WP5 (volle Prüfung, Registry-Inhalt entdecken) läuft selten, bewusst,
mit Seiteneffekten, nie automatisch im Produkt. WP3 (Registry-Anwendung pro Connect) ist
zurückgestellt, siehe dessen eigener Statusblock oben — die Cadence-Aussage dafür gilt erst,
wenn es umgesetzt wird.

#### Grenze: Was geht per Registry-Eintrag — was braucht echten Code

| Änderungstyp im Release | Registry-Eintrag reicht | Begründung |
|---|:---:|---|
| Feld umbenannt (1:1) | ✅ | reine Umbenennung |
| Feld ersatzlos gestrichen | ✅ (weglassen) | kein Transform nötig |
| Neues Pflichtfeld mit konstantem Default | ✅ | statischer Wert |
| Methode umbenannt | ✅ | reine Umbenennung |
| Modell umbenannt | ✅ | reine Umbenennung |
| Feld-Laufzeit-Form geändert (z. B. `ir.attachment.raw` String→Dict) | ✅ (Transform-Funktion) | kleine, registrierte Funktion — kein genereller Adapter nötig |
| Char-Feld wurde Many2one (Wert braucht Lookup) | ❌ Code | braucht zusätzlichen API-Call |
| Workflow geändert (z. B. zwei Bestätigungsschritte statt einem) | ❌ Code | braucht Ablauflogik |
| Modell aufgespalten / zusammengelegt | ❌ Code | Strukturänderung — laut Chat 2026-09-02 realistisch nur an Major-Version-Grenzen erwartet |

**Tests:** Unit — Registry-Auflösung pro Version, Transform-Funktionen einzeln; Integration —
WP2s Komposition (Feld-Warnung bleibt stumm, wenn `model_access` blockiert meldet — Pattern 3
analog); ein Lauf mit leerer Registry muss byte-gleiche Payloads erzeugen wie heute
(Regressionsschutz, gleiche Idee wie im alten Plan).

### R6 🟡 Multi-Country Customer/Supplier Generation

**Hinzugefügt:** 2026-08-03, während S3-Review (Architekten-Feedback zu A1).

Ziel: GUI-Option zur Auswahl des Ziellandes/der Zielländer für generierte Kunden und
Lieferanten (z.B. zusätzlich zu DACH), mit landestypischen Namen und Adressen — eine
"lebende Datenbank" statt einer festen DACH-Annahme.

Bausteine:
- `static_data.py`s Struktur ist bereits länderweise organisiert (Fundament aus S3) —
  neue Länder sind reine Datenergänzungen (`CITIES["IT"] = [...]`, `STREET_NAMES` ggf.
  pro Land, falls generische DACH-Straßennamen für andere Länder unpassend sind).
- Personen-/Firmennamen werden aktuell **einmal pro Lauf, generisch für die Branche**
  generiert (`fetch_name_suggestions`, kein Länderparameter) — braucht entweder (a) einen
  zusätzlichen Ziel-Land-Parameter an eine neue/erweiterte LLM-Funktion, oder (b) statische
  landesspezifische Namensbänke analog zu `FALLBACK_*`.
- GUI: neues Auswahlfeld "Zielland(er)" in Screen 3 (Stammdaten-Panel), Default DACH
  (aktuelles Verhalten unverändert), Mehrfachauswahl möglich.
- 🔒 Neues Feld in `RunContext`/`DemoCriteria` nötig → Config-Schema-Erweiterung,
  Architekten-Freigabe erforderlich vor Umsetzung.

Aufwand: mittel-groß. Nicht Teil von S3.

**Verifiziert 2026-09-02:** weiterhin nicht umgesetzt — kein Zielland-Feld in `config.py`
(`DemoCriteria`/`RunContext`), `modules/master_data.py:18` hat `_TARGET_COUNTRIES = ["DE",
"AT", "CH"]` hartkodiert, `static_data.py`s `CITIES` kennt nur DE/AT/CH. Fundament aus S3
(länderweise Struktur) steht, das Feature selbst nicht begonnen.

### R12 🆕 Geplant (S14) — Nachbestellregeln / Replenishment-Planung

**Annahme zur Bezeichnung "MRP Planung":** interpretiert als Odoos Nachbestellregeln
(`stock.warehouse.orderpoint`, im deutschen Inventory-Menü unter "Planung"/"Nachbestellung"),
**nicht** MPS (Master Production Schedule, separates Enterprise-Widget ohne eigenes
Kern-Modell in dieser Feldliste). Falls anders gemeint: vor S13-Start korrigieren.

**Live bestätigt (`stock.warehouse.orderpoint`, saas-19.4):** vollständiges Feldschema
gezogen — u. a. `warehouse_id`/`location_id`/`product_id` (alle `required`),
`product_min_qty`/`product_max_qty` (`required`, float), `trigger` (`auto`/`manual`),
`route_id`, `company_id` (`required`).

**Dev Tasks:**
- **In `inventory.py` einziehen, kein neues Modul** (siehe §3s "Referenz —
  Registrierungskette für ein neues orchestriertes Modul") —
  Orderpoints als weiterer Zweig im bereits vorhandenen `ModuleSelections.stock`-Dict
  (`stock: dict = {"avg_qty": int, "orderpoints_pct": int, ...}`), keine neuen Einträge in
  `run_config.py`/`odoo_actions.py`/Frontend nötig.
- Orderpoints für einen konfigurierbaren Produkt-Anteil (Storables aus
  `ctx.product_ids`/`ctx.component_ids`), `location_id` = Lagerort aus R15 (oder Fallback
  `warehouse["stock_location_id"]` wie bisher), `company_id` via
  `odoo_actions.get_main_company_id` (nicht `ctx.company_ids[0]` — bekannte Falle, siehe
  CLAUDE.md).
- **`trigger='manual'`, nicht `'auto'`** (Peer-Review-Korrektur): `purchase.py` legt
  aktuell kein `product.supplierinfo`/`seller_ids` an — mit `trigger='auto'` würde Odoos
  Scheduler auf jedem betroffenen Produkt eine "kein Lieferant"-Procurement-Exception
  werfen. Erst mit Lieferanten-Zuordnung (nicht Teil von R12) wäre `'auto'` sicher.
- **Sicherer Minimal-Scope:** nur die Orderpoint-Records anlegen (bereits ein sichtbarer,
  echter Demo-Artefakt in Odoos Inventory-App). Odoos eigener Scheduler-Cron zieht sie auf
  der Ziel-SaaS-Instanz von selbst (mit `trigger='manual'` löst er allerdings nicht
  automatisch aus — genau deshalb hier unkritisch).
- **Stretch, nicht S13-Pflicht:** sofortiger Replenishment-Trigger per API. Methodenname
  unverifiziert (`action_replenish`? ein Wizard?) — vor Umsetzung live per
  `has_access`-Probe oder Testaufruf klären, sonst nicht spekulativ einbauen.

**Tests:** Pattern 1 (keine Storables → skip), Pattern 5 (kein Warehouse → skip, analog
S8), Pattern 4 (Read-back `product_min_qty`/`product_max_qty`).

**Komplexität:** Mittel (Minimal-Scope) · **Benefit:** Mittel-Hoch

### R14 🟠 Quant-Anteil ✅ erledigt (S13/WP2-4, siehe `ROADMAP_ARCHIVE.md`), Wareneingangs-Anteil noch offen — Multi-Warehouse

**Verbleibender offener Anteil:** `orchestrator.py`s `module_order` lässt
`purchase` vor `stock` laufen — das zweite Warehouse entsteht aber erst im
`stock`-Schritt, `purchase.py` kann es zum Zeitpunkt seines eigenen Laufs
also nicht sehen. S13 hat deshalb nur den Quant-Anteil umgesetzt (zweites
Warehouse bekommt einen Anteil der von `inventory.py` erzeugten
`stock.quant`-Bestände, siehe `ROADMAP_ARCHIVE.md`s R14-Statusblock). Dieser
Abschnitt bleibt der richtige Ort für den Wareneingangs-Anteil, bis eine
Pipeline-Reorder-Entscheidung (🔒, Architekten-Freigabe) das auflöst.

**Dev Task (offen):** `purchase.py`: konfigurierbarer Anteil der
Wareneingänge aufs zweite Lager statt immer `get_default_warehouse` — braucht
entweder die Pipeline-Reorder-Entscheidung oder einen anderen Weg, das zweite
Warehouse vor `purchase.py`s Lauf verfügbar zu machen.

**Komplexität:** Mittel · **Benefit:** Mittel

### R17 🆕 Geplant (S16) — Multicompany

**Live bestätigt (Architektur-Spike, 2026-09-04, `demo-test5.odoo.com`):**
`res.company.parent_id`/`child_ids` (Firmenhierarchie), `res.users.company_id`/
`company_ids` (Default- + erlaubte Firmen) — Standard-Odoo-Multi-Company-Modell ist auf
dieser Instanz vorhanden und nutzbar. Vier zuvor offene Fragen live geklärt:

1. **Kontenplan einer neuen `res.company` ist NICHT automatisch da — und wird für den
   Minimal-Scope bewusst NICHT geladen (Cold-Review-Korrektur, siehe unten).**
   `res.company.create()` liefert eine Firma ohne jeden `account.account` (bestätigt:
   `search_read` auf `account.account` mit `company_ids in [<neue id>]` → `[]`). Das Laden
   funktioniert zwar mechanisch per JSON2 (`client.call_method('account.chart.template',
   'try_loading', ids=[], kwargs={'template_code': ..., 'company': <id>})`, ein
   `@api.model`-Classmethod, `ids=[]` funktioniert), braucht vorher gesetztes
   `res.company.country_id` (sonst `"Missing required field 'Country' ... for model
   'Tax'"`) und ein zum Country passendes Template (`generic_coa` + Deutschland scheitert
   mit `"The tax group must have the same country_id as the tax using it"`, `de_skr03` +
   Deutschland funktioniert — 5+ Accounts danach lesbar). **Cold-Review Runde 1 fand hier
   drei zusammenhängende Blocker gegen einen rein kosmetischen Nutzen:** (a) das
   Hardcoding auf Deutschland/`de_skr03` widerspricht `data_factory.py`s eigenem
   `_DEFAULT_COUNTRIES = ["DE", "AT", "CH"]` und der Tatsache, dass `web/security.py`s
   Host-Allowlist jeden `demo-*.odoo.com`-Mandanten zulässt, nicht nur deutsche; (b) die vom
   Chart-Load angelegten `account.account`/`account.journal`/`account.tax`-Records
   entstehen serverseitig innerhalb eines einzigen Methodenaufrufs und laufen nie durch
   `JournalingClient.create` — D7s `delete_run` kann sie also grundsätzlich nicht erfassen,
   unabhängig von `ARCHIVE_FALLBACK_MODELS`; (c) kein Fallback bei Fehlschlag (Firma bereits
   angelegt, Chart-Load schlägt fehl → halb-fertige Firma, "Fehler"-Zeile). Der Minimal-Scope
   enthält ohnehin keine Belege gegen die neue Firma (siehe unten) — ein Kontenplan hat also
   keinen funktionalen Zweck, nur einen kosmetischen. **Entscheidung:** Kontenplan-Laden
   entfällt komplett aus dem Scope. Die neue Firma bleibt ohne Chart of Accounts; das
   spiegelt akkurat, dass in diesem Umfang nichts Buchhaltungsrelevantes gegen sie läuft.
2. **Referenzierte Records lassen sich nicht umhängen — jetzt live bestätigt, nicht mehr nur
   angenommen.** `write()` von `company_id` auf ein bereits in einer `sale.order.line`
   referenziertes `product.product` scheitert mit `"Das Unternehmen dieses Produkts kann nicht
   geändert werden, solange es Lagerbuchungen gibt, die einem anderen Unternehmen gehören."` —
   bestätigt exakt das erwartete Verhalten, der Minimal-Scope (nur frisch erzeugte Records der
   zweiten Firma zuweisen) bleibt die einzig gangbare Route.
3. **Keine Record-Rule-Maskierung — API-Key-User sieht Firma-2-Records IMMER, gefiltert oder
   nicht (Cold-Review Runde 2, Blocker B1: Runde-1-Text widersprach sich selbst und war
   falsch).** Ursprünglich stand hier, ein ungefilterter `search_read` zeige Firma-2-Records
   nicht (Standard-Odoo-Record-Rule-Scoping). **Live nachgeprüft (2026-09-04, frische
   Firma+Warehouse, drei parallele Reads):** `search_read` mit `id`-Domain, mit
   `company_id`-Domain UND **komplett ungefiltert** (`limit=0`) liefern alle drei dasselbe
   Ergebnis — das Firma-2-Warehouse erscheint in jeder der drei Abfragen. Der ursprüngliche
   "maskiert"-Befund war ein Auswertungsfehler: die erste Stichprobe nutzte `limit=20` und
   das gesuchte Objekt lag (nach `id` sortiert) hinter Position 20 — Limit-Abschneidung, keine
   Record Rule. Für diesen API-Key-User (`res.users` id 2, `company_ids=[1]`) gilt: **keine**
   company-basierte Zugriffsbeschränkung greift überhaupt, unabhängig von Domain oder
   `company_ids`. (Ursache nicht weiter verfolgt — plausibel ein privilegierter/interner
   API-User, dessen Zugriff Multi-Company-Record-Rules nicht unterliegt; für den Scope dieses
   Spikes nicht relevant, das Verhalten selbst ist das, was zählt.)
   **Konsequenz 1 (positiv):** WP3s Pattern-4-Read-Backs (Partner/Produkte/Warehouse) funktionieren
   ohne Weiteres — keine Sonderbehandlung für Lese-Domains nötig.
   **Konsequenz 2 (der eigentliche Befund, ersetzt die alte "company_ids-Erweiterung wäre
   riskant"-Begründung):** weil nichts maskiert wird, **existiert das Leck-Risiko bereits
   heute, unabhängig von jeder `company_ids`-Erweiterung.** `connect_service.fetch_existing_data`
   filtert seine `product.product`/`res.partner`-Abfragen (`:109-124`) nicht nach `company_id` —
   sobald dieser Sprint das erste Firma-2-Produkt anlegt, zieht jeder spätere Lauf mit
   "Vorhandene Daten einbeziehen" (`use_existing`) es ungefiltert in den geteilten
   `ctx.product_ids`-Pool (`run_config.py:474-476`), unabhängig davon, ob der API-User
   `company_ids=[1]` oder `[1,2]` hat. **Das macht den `company_id`-Filter in
   `fetch_existing_data` zu einem echten WP2-Pflichtschritt dieses Sprints, nicht zu einer
   optionalen Zukunftsaufgabe** — R17 selbst erzeugt zum ersten Mal Records, die dieses Loch
   real treffen können. Die `company_ids`-Erweiterung des API-Users bleibt weiterhin außerhalb
   des Scopes (kein Bedarf — Konsequenz 1 zeigt, sie war nie nötig, um Firma-2-Records lesbar
   zu machen), aber aus einem anderen, einfacheren Grund als ursprünglich angenommen: sie war
   schlicht nie die Lösung für irgendetwas.
4. **`odoo_actions.get_main_company_id`s Blast Radius ist kleiner als befürchtet.** Vier
   Call-Sites (`modules/expenses.py:50`, `modules/mrp.py:152`, `modules/inventory.py:55`,
   `modules/purchase.py:151`) — alle vier geben laut Doku-String bewusst "Firma mit id=1,
   sonst die erste gefundene" zurück. Das bleibt mit einer zweiten Firma unverändert korrekt:
   die komplette bestehende Pipeline arbeitet weiterhin ausschließlich gegen Firma 1, keine der
   vier Stellen muss angefasst werden. Die neue Firma-2-Befüllung (R17-Minimal-Scope) läuft
   über einen eigenen, neuen Helper — additiv, nicht als Parameter an
   `get_main_company_id` angehängt. **Cold-Review-Ergänzung:** drei weitere `res.company`-
   lesende Helper existieren (`get_main_company_name` — `odoo_actions.py:388`, genutzt von
   `connect_service.py:164`; `get_main_company_info` — `:427`, genutzt von
   `modules/documents.py:97`; `get_main_company_language` — `:675`), alle mit demselben
   "id=1 zuerst"-Fallback wie `get_main_company_id` — ebenfalls unverändert korrekt, aber
   bisher nicht namentlich genannt.

**⚠️ Warum eigener Architektur-Spike vor Code nötig war (🔒-adjacent):** `RunContext.company_ids`
heißt trotz seines Namens **niemals** `res.company`, sondern hält `res.partner`-IDs
(Kunden-/Firmenkontakte aus `master_data.py`) — diese Verwechslung hat bereits einen
echten, monatelang unbemerkten Bug in `mrp.py` verursacht (S8, gefixt in S10). Eine zweite
echte Firma vervielfacht die Gelegenheiten für exakt diese Fehlerklasse über praktisch
jedes Modul hinweg (`sale.py`, `purchase.py`, `inventory.py`, `mrp.py` nutzen `company_id`
an vielen Stellen).

**Entschieden (Spike-Ergebnis, kein Rename):** **neues** Feld statt Umbenennung —
`RunContext.res_company_ids: List[int]`, echte `res.company`-Ids, Name bewusst von
`company_ids` (hält `res.partner`-Ids) unterschieden. Begründung: ein Rename von
`company_ids` träfe Dutzende Call-Sites über `sale.py`/`purchase.py`/`inventory.py`/
`mrp.py`/`crm.py`/`project.py`/`hr.py`/`recruiting.py` hinweg — u. a. genau die Dateien, die
S15/R20 eine Stunde vor diesem Spike zuletzt angefasst hat. Klarheitsgewinn eines Renames
steht in keinem Verhältnis zum Regressionsrisiko über einen Diff, den noch niemand
"gelebt" hat. Form bewusst eine flache Liste, nicht `second_company_id` + parallele
`second_*_ids`-Felder — das würde "zwei Firmen" ins Config-Schema einbrennen und bei einer
dritten Firma erneut eine 🔒-Änderung erzwingen; eine Liste sagt nichts über die Anzahl aus.
Das ist eine Config-Schema-Änderung 🔒, Architekten-Freigabe wie jede andere 🔒-Stelle —
freigegeben im Rahmen dieses Spikes (Nutzer hat "plane den nächsten Sprint" beauftragt, das
schließt diese Design-Entscheidung ein).

**Empfohlener Minimal-Scope für den ersten Wurf (bewusst klein, nicht die volle
Pipeline verdoppeln):**
- **Nicht** den kompletten 8-Module-Durchlauf pro Firma wiederholen — bei
  ~1 req/s Live-Rate-Limit (siehe CLAUDE.md, jetzt auch hart durch D10 durchgesetzt) und
  bereits heute mehrstufigen Läufen wäre das ein Laufzeit- und Fehlerbudget-Vielfaches ohne
  klar proportionalen Demo-Mehrwert.
- Stattdessen: **eine** zusätzliche `res.company` anlegen (ohne Kontenplan, siehe Punkt 1
  oben), ihr NUR frisch für sie selbst erzeugte Records zuweisen (neue Partner/Produkte/ein
  neues Warehouse aus R14) — **nicht** bestehende, von Company-1-Belegen referenzierte
  Records per `company_id`-Write umhängen (Punkt 2 oben, jetzt live bestätigt statt nur
  angenommen).

**Details, WP-Aufteilung und Cold-Review-Ergebnisse:** siehe "S16 — WP-Sequenz" in
`ROADMAP.md`.

**Komplexität:** Hoch · **Benefit:** Hoch (langfristig), aber bewusst klein für den
Minimal-Scope des ersten Wurfs — der volle Nutzen hängt vom tatsächlich freigegebenen
Umfang ab, nicht von dieser Schätzung allein.

### R18 🆕 Geplant (S14) — Quality Checks

**🚨 Peer-Review-Blocker — das gibt es schon:** `modules/mrp.py:86-89`
(`create_quality_point`) und `:369-432` erzeugen bereits `quality.point`-Records, suchen
bereits `quality.alert.team`/`quality.point.test_type` (search-only, kein Anlegen bei
leerem Pool — **das** ist das echte Vorbild für "suchen, nie erzeugen" in diesem Repo, nicht
`hr.skill.level`) und sind bereits gegatet auf `mrp_config.get("create_quality_points",
False)` + `ctx.feature_flags['quality']` + `ctx.model_access['quality.point']`
(`run_config.py:282`). R18 ist also **kein neues Modul**, sondern eine Erweiterung/ein
Bugfix des bestehenden `mrp.py`-Pfads — die ursprüngliche Fassung dieses Punktes ("neue
Datei `modules/quality.py`") war schlicht falsch und hätte den existierenden Code dupliziert.

**Nebenbefund — bestehender Live-Bug, im Zuge von R18 mitfixen:** `mrp.py:427` sendet
`"test_report_type": "none"` — laut Live-Feldschema ist `test_report_type` **required**
mit Selection **nur** `pdf`/`zpl` (kein `none`). Bleibt bisher unbemerkt, weil
`create_quality_points` in `tests/integration/test_mrp.py:163` auf `False` steht (der Pfad
läuft in keinem Test). Fix: `"pdf"` setzen.

**Live bestätigt (`quality.point`/`quality.check`, saas-19.4):** `quality.point`:
zusätzlich zu `team_id`/`test_type_id`/`apply_to`/`picking_type_ids`/`operation_id`
(→ `mrp.routing.workcenter`) auch `measure_on` **required**. `quality.check`: `team_id`,
`test_type_id`, `company_id` sind **required** (bisher in `mrp.py`s Erzeugung nicht
vollständig abgedeckt — vor Erweiterung gegen den aktuellen `qp_vals_list`-Aufbau in
`mrp.py:418-432` genau abgleichen, welche Felder dort schon gesetzt werden und welche
fehlen), `measure_on` ist dort **required + readonly** (vom `point_id` abgeleitet).

**Dev Tasks:**
- Bestehenden Pfad in `mrp.py:369-432` reparieren (`test_report_type`-Bug oben) und um
  fehlende Required-Felder auf `quality.check`-Erzeugung ergänzen, statt parallel etwas
  Neues zu bauen.
- **`operation_id`/Workorder-Anbindung als Stretch, nicht v1**: `ctx.workcenter_ids` ist
  der falsche Pool dafür (das sind `mrp.workcenter`-IDs, `mrp.py:320`) — `operation_id`
  braucht `mrp.routing.workcenter`-IDs, die `create_bom_operation` aktuell gar nicht
  zurückgibt/speichert. Diese IDs additiv festzuhalten ist eine eigene kleine Vorarbeit;
  bis dahin bleibt R18 v1 auf `apply_to='products'` + `picking_type_ids`
  (Wareneingangs-Kontrolle über die Warehouse-`in_type_id`, unabhängig von R12/S12
  verfügbar seit S8) beschränkt.
- Quality Checks mit verteiltem `quality_state` (Pattern 7: überwiegend `pass`, ein
  konfigurierbarer Fail-Anteil), `lot_ids` aus R13 wenn vorhanden (sonst leer — keine harte
  Abhängigkeit zwischen R18 und R13).
- Falls über den `mrp.py`-Pfad hinaus ein eigenständiges Feature draus wird (z. B.
  eigene GUI-Karte), dann **alle sieben** Punkte aus §3s "Referenz — Registrierungskette
  für ein neues orchestriertes Modul" beachten — nicht nur `orchestrator.py`/`config.py`.

**Tests:** Pattern 1 (kein `test_type_id`/Team → skip, bereits vorhanden), Pattern 3 (Flag
aus → keine Calls, bereits vorhanden), Pattern 7 (Pass/Fail-Verteilung, neu), plus ein
Regressionstest, der `create_quality_points=True` tatsächlich laufen lässt (bisher deckt
`test_mrp.py` das mit `False` ab und hätte den `test_report_type`-Bug nie gefangen).

**Komplexität:** Mittel (kleiner als ursprünglich geschätzt — Fundament existiert bereits)
· **Benefit:** Mittel-Hoch (nutzt die MRP-Investition aus S1 weiter aus, guter visueller
Payoff im Quality-App-Dashboard)

### R20 🆕 Geplant (S15) — Analytic Accounting

**Live bestätigt (S15/WP1, 2026-09-03, gegen `demo-test5.odoo.com`):**
`analytic_distribution` (json, nicht required, Format `{"<analytic_account_id als
String>": <Prozent als Float>}`) existiert **identisch** auf allen fünf Zielmodellen:
`sale.order.line`, `purchase.order.line`, `account.move.line`, `hr.expense`,
`account.analytic.line` — die ursprüngliche "mit hoher Wahrscheinlichkeit"-Annahme war
richtig, jetzt zweifach per echtem `fields_get` bestätigt.

**Zwei Korrekturen an R20s ursprünglichem Text, live gefunden (WP1):**
1. **Der bestehende Default-Plan (`account.analytic.plan` id=1, "Project Plan") ist
   bereits stark durch S7/R8s eigene Projekt-Auto-Erzeugung besetzt** (223
   `account.analytic.account`-Records live gezählt, je einer pro
   `service_tracking='task_in_project'`-Projekt) — R20 legt seine Kostenstellen
   ("Vertrieb"/"Produktion"/"Verwaltung") deshalb unter einem **neuen, eigenen** Plan an
   (`account.analytic.plan.create({"name": ...})`, keine weiteren Pflichtfelder außer
   `name`), nicht unter Plan 1. Hält die beiden Buchungskreise sauber getrennt: Plan 1
   bleibt "ein Analytic-Account pro Projekt" (Odoo-eigene Ableitung, siehe unten), der
   neue Plan ist "eine Handvoll Kostenstellen" (dieses Items eigene Erzeugung).
2. **`analytic_distribution` lässt sich entgegen dem ursprünglichen Text sehr wohl auf
   einer bereits `state='posted'` `account.move.line` schreiben** — live getestet:
   `write()` liefert `True`, Read-back zeigt den neuen Wert. Ändert nichts am gewählten
   Design (Distribution weiterhin **vor** `accounting.py` auf der `sale.order.line`
   setzen, native Wizard-Propagation nutzen — sauberer, kein Nachtrags-Write nötig), aber
   die "kein gangbarer Fallback"-Aussage war falsch; ein Nachtrags-Write auf gebuchte
   Move-Lines wäre technisch möglich, ist nur nicht der gewählte Weg.

**Weitere Live-Funde:**
- Nur **110 von 1332** `sale.order.line`-Records (≈8 %) tragen aktuell überhaupt eine
  `analytic_distribution` — exakt die `service_tracking='task_in_project`-Teilmenge aus
  S7/R8. Die übrigen ≈92 % sind der reale Zielbereich dieses Items, keine Überschneidung.
- **End-to-End live bestätigt:** eine manuell auf einer `sale.order.line` gesetzte
  `analytic_distribution` überlebt `action_confirm` unverändert und wird vom
  `sale.advance.payment.inv`-Wizard (S7/R8) **automatisch** auf die erzeugte
  `account.move.line` übertragen — kein eigener Übertragungscode nötig, exakt wie geplant.
- `account.analytic.line.analytic_distribution` ist **`store: false`** (reines Compute-
  Feld) — nicht per Domain filterbar (`search_read` mit `!=`/`in` auf dieses Feld liefert
  einen 500er), bestätigt aber zusätzlich die bestehende "nicht anfassen"-Entscheidung
  unten: ein Compute-Feld ohne Storage lässt sich ohnehin nicht sinnvoll direkt setzen.
- `hr.department`: nur **ein** Department existiert live ("Administration"), `hr.py`
  legt selbst nie welche an — die ursprünglich "optionale" 1:1-Kopplung Kostenstelle↔
  Department bringt bei nur einem Department keinen Mehrwert. **Gestrichen**, nicht nur
  zurückgestellt.

**Dev Tasks:**
- Ein neuer, gemeinsamer Helper (`odoo_actions.get_or_create_analytic_accounts` o. ä.,
  Cross-Modul-Helper analog `get_main_company_id`) legt Plan + Kostenstellen lazy und
  memoized über `ctx.analytic_account_ids` an — egal welches der drei Module (`sale`,
  `purchase`, `hr_expense`) zuerst läuft, es erzeugt sie einmal, die anderen beiden lesen
  `ctx.analytic_account_ids` und erzeugen nichts doppelt. Kein `orchestrator.py`-
  Reihenfolge-🔒 nötig — bewusste Abweichung von "wer zuerst läuft erzeugt sie" (`sale`
  liefe laut `module_order` immer zuerst, aber sich darauf zu verlassen wäre eine
  stille, brüchige Kopplung an eine Ausführungsreihenfolge, die dieses Item nicht
  besitzt).
- `analytic_distribution` auf einem konfigurierbaren Anteil von Sale-Order-**Zeilen**
  setzen, **bevor** `accounting.py` läuft (Pipeline-Position 3 vs. 8, siehe
  Objekt-Pipeline oben) — nur auf Zeilen, deren `analytic_distribution` noch leer ist
  (die S7/R8-Projekt-Zeilen NIE überschreiben, siehe "Explizit nicht anfassen" unten).
  Rechnungszeilen übernehmen die Distribution serverseitig vom verknüpften
  `sale.order.line` über den bestehenden `sale.advance.payment.inv`-Wizard (S7/R8,
  Übernahme live bestätigt, siehe oben).
- `analytic_distribution` zusätzlich auf einem Anteil der Purchase-Order-Zeilen und (R19
  ist seit S12 gelandet, siehe `ROADMAP_ARCHIVE.md`) Expense-Zeilen setzen — beide
  unabhängig vom Sale/Invoice-Pfad, beide live schreib-/lesbar bestätigt.
- **Explizit NICHT anfassen:** `project.py`s bestehende Timesheet-Analytic-Anbindung
  (S7/R8). **Peer-Review-Korrektur zur Begründung:** `project.py:210-244` setzt beim
  Anlegen von `account.analytic.line`-Timesheet-Einträgen nur `project_id`/`task_id`/
  `so_line` — **nicht** `account_id` selbst (Odoo leitet die Analytic-Account serverseitig
  aus dem Projekt ab). Der Grund, das nicht anzufassen, ist also nicht "wäre bereits
  korrekt gesetzt", sondern: ein zusätzlicher expliziter `account_id`-Write würde die
  bestehende, funktionierende serverseitige Ableitung überschreiben/duplizieren, ohne
  Mehrwert — dieses Item bleibt additiv auf andere Belegarten beschränkt. Aus demselben
  Grund darf die neue Sale-Zeilen-Logik auch nie eine bereits gesetzte (S7/R8-)
  Distribution überschreiben.

**Tests:** Pattern 1 (leerer Kandidatenpool für den Kostenstellen-Zuweisungs-Draw),
Pattern 3 (Flag aus → keine `analytic_distribution`-Writes), Pattern 4 (Read-back
`analytic_distribution`-JSON-Struktur pro Belegzeile, plus des End-to-End-Wizard-
Übertragungspfads), Pattern 5 (fehlende Prerequisites → Skip), Pattern 7
(Anteils-Verteilung über eine eigenständige, isoliert testbare `data_factory`-Funktion,
analog `assign_tracking`/`assign_quality_state`), Pattern 8 (Batch-Call-Count für die
Kostenstellen-Erzeugung).

**Komplexität:** Hoch (4+ Dateien: `sale.py`, `purchase.py`, `odoo_actions.py`,
`expenses.py`, plus `config.py`/`run_config.py`/`static/app.js` für die neue
Konfigurationsfläche) · **Benefit:** Mittel-Hoch

---

### S12 — WP-Sequenz (Quick Wins: R11, R16 Produkt-Ebene, R19)

**Stand 2026-09-02, geplant parallel zu S11** (eigener Worktree/Branch
`s12-quickwins-planning`, kein Code-Anfassen, solange S11 läuft; S11 inzwischen
abgeschlossen, `s12-quickwins-planning` nach `s11-api-version-compat-d9` gemergt).
R11/R16/R19 waren schon inhaltlich fertig designt (Live-Felder, Peer-Review-Korrekturen,
Testpattern); was fehlte war die WP-Reihenfolge und die konkreten Einfügestellen — gegen
den tatsächlichen Code (`sale.py`, `orchestrator.py`, `config.py`, `odoo_client.py`)
aufgelöst, analog zu R5s WP1-5-Struktur.

**Cold-Review 2026-09-02 (fremder Opus-Agent, Plan-Text + Live-Repo, keine
Konversationshistorie) vor Implementierungsstart — 4 Blocker gefunden, alle eingearbeitet**
(Details in `ROADMAP_ARCHIVE.md`s R11/R19-Statusblöcken, R16 weiterhin oben in diesem
Abschnitt — R11/R19 sind seit WP4/WP2 abgeschlossen und archiviert): R19s fehlende
Registrierungskette + falscher Modul-Key (Blocker 1-3), R11s `orchestrator.py`-Einfügung
fälschlich als "Formsache" eingestuft (Blocker 4). Zu Blocker 4: **Architekten-Freigabe
eingeholt 2026-09-02** — neuer `orchestrator.py`-Eintrag zwischen `sale` und `hr`
(Nutzerentscheidung gegen die Review-Alternative "Aufruf aus `sale.create_sale_data`", die
zwar keine 🔒-Freigabe gebraucht hätte, aber den Schritt aus `module_order` genommen und
damit Fortschrittszeile/Fehler-Erfassung/Gate verloren hätte — architektonisch schlechter
trotz weniger sichtbarem Antrag). Reihenfolge nach Review-Empfehlung getauscht (siehe
Begründung unten).

**Reihenfolge-Begründung (überarbeitet nach Cold-Review):** ursprünglich R16→R19→
Live-Verifikation→R11→Review, mit der Annahme, R16/R19 könnten "unabhängig von R11s
Live-Verifikation" durchlaufen und mergen. Das hält nicht: CLAUDE.md verlangt einen grünen
Live-`test_suite.py`-Lauf zum Abschluss **jedes** Arbeitspakets, also schließt keines der
drei ohne die Zielinstanz ab, unabhängig von der Reihenfolge. Zusätzlich gaten WP3(a)/(b)
tatsächlich das *Design* von R19/R11 (`write()` vs. Action-Methode) — sie zuerst zu klären
vermeidet Nacharbeit. Da S11 die Instanz inzwischen freigegeben hat (Sprint-Status oben:
S11 ✅), ist die Vorbedingung für WP3 bereits erfüllt. Neue Reihenfolge: **WP3 zuerst**
(ein gebündelter Live-Check statt verteilter Einzel-Calls, schont das ~1-req/s-Limit),
dann WP1 (R16, keine Design-Abhängigkeit von WP3), WP2 (R19, jetzt mit geklärtem
`approval_state`-Verhalten), WP4 (R11, jetzt mit geklärtem Lost-Verhalten und erteilter
🔒-Freigabe), WP5 (Review vor Merge in `main`).

| WP | Inhalt | 🔒 | Voraussetzung |
|---|---|---|---|
| **WP3** ✅ (2026-09-02) | Gebündelte Live-Verifikation gegen `demo-test5.odoo.com` (Batch-Skript, ein hr.expense + ein crm.lead angelegt, geschrieben, gelesen, wieder gelöscht). **(a) hr.expense:** `write(approval_state='submitted')` funktioniert direkt, kein `action_submit`/`action_approve` nötig — aber `product_id` muss vorher gesetzt sein (sonst "Select a product to proceed" auf `write()` **und** auf beiden Action-Methoden). **(b) crm.lead:** `write(active=False, probability=0, lost_reason_id=X)` funktioniert direkt, `won_status` (Compute-Feld) zieht korrekt auf `'lost'` nach — kein `action_set_lost` nötig. **(c) übersprungen** (optional, gate't WP1 nicht, siehe R16-Abschnitt) | nein | S11 hat Instanz freigegeben (erfüllt) |
| **WP1** ✅ (2026-09-02) | R16 Produkt-Ebene: `data_factory.assign_barcodes` (EAN-13-Generator + Kollisions-Dedup), `master_data._create_products` liest bestehende Barcodes einmalig, `odoo_actions.py`-Feld-Compat-Eintrag. Unit (3 neue Fälle) + Live-Integrationstest (Pattern 4, echte EAN-13-Prüfziffer-Validierung auf realen Odoo-IDs) grün | nein | — (WP3(c) optional, gate't nicht) |
| **WP2** ✅ (2026-09-02) | R19: `modules/expenses.py` neu, vollständige Registrierungskette (`WANTED_MODULES`/`MODULE_LABELS`/`MODULE_RUN_ORDER`/`build_selections`/`estimate_record_counts`/`app.js`-`MODULE_DEFS`+`ICONS`/`test_run_config_unit.py`-Payloads), Modul-Key durchgängig `hr_expense`, `orchestrator.py`-Eintrag vor `"documents"` (nach `"stock"`), `odoo_actions.py`-Feld-Compat-Eintrag (Whitelist + `MODEL_ACCESS_PROBES` + `PRIMARY_MODEL_PER_MODULE`). Batched Approval-Writes (2 Calls total, nicht 2×N). Unit (7 neue Fälle) + Live-Integration (3 neue Schritte, Pattern 4/5) grün, erster Lauf durch | additiv | WP3(a) |
| **WP4** ✅ (2026-09-02) | R11: `config.py`-Feld `crm_lost` (additiv, nur innerhalb `if _enabled(crm)` in `build_selections` gesetzt), `RunContext.linked_opportunity_ids` (additiv), `sale.py`-Zeile 89-96 um Tracking ergänzt, `modules/crm.py`s `mark_lost_opportunities` (Pattern 1/3/5, gruppierte Batch-Writes pro `lost_reason_id`), `orchestrator.py`-Eintrag nach `"sale"` (🔒-Freigabe erteilt), `static/app.js`-CRM-Karten-Unterblock. Unit (7 neue Fälle inkl. "nur unlinked wird geschrieben") + Live-Integration (2 neue Schritte, echter Beweis: verlinkte Opp bleibt aktiv, unverlinkte wird `won_status=lost`) grün | additiv + 🔒 (Freigabe erteilt) | WP3(b) |
| **WP5** ✅ (2026-09-02) | Peer-Review vor Merge (1 fremder Opus-Agent, Plan-Text + Live-Repo, keine Konversationshistorie) — 2 Blocker gefunden, beide live gefixt: (1) genehmigte `hr.expense`-Datensätze (Default 70%) blockierten `delete_run` komplett — Odoo verweigert `unlink` auf genehmigten Spesen, und `delete_run` löscht pro Modell in einem Aufruf, sodass eine einzige genehmigte Spese alle Spesen des Laufs unlöschbar machte und kaskadierend auch Mitarbeiter blockierte; Fix: `run_journal.CANCEL_BEFORE_UNLINK["hr.expense"] = ["action_reset"]`, live verifiziert. (2) `sale.py`s `ctx.linked_opportunity_ids`-Erzeugung hatte keine Testabdeckung auf dem echten Code-Pfad (nur handgesetzte Listen in Tests) — Zeile hätte gelöscht werden können, ohne dass eine Testsuite es gemerkt hätte; 2 bestehende B14-Tests (Unit + Live) um die fehlende Assertion ergänzt. Unit 371/371, Live-Integration 87/87 grün | — | WP1-WP4 Code steht |

**Pro Arbeitspaket verbindlich:** dieselben Testing Design Patterns wie jedes bisherige
Sprintpaket (siehe CLAUDE.md) — für R16 EAN-13-Unit-Test + Pattern 1 + Integrationstest
(Dedup ist Verhaltensänderung, kein reiner Pure-Function-Zusatz mehr), R19 Pattern 1/3/5/7,
R11 Pattern 1/3/5/7 (Pattern 5 neu: leere `opportunity_ids` → Skip, siehe
`ROADMAP_ARCHIVE.md`s R11-Statusblock).

---

### S13 — WP-Sequenz (Lager-Tiefe: R14, R15, R13)

**Stand 2026-09-02.** Plan **zweimal** cold-reviewed vor Umsetzungsstart
(2× fremder Opus-Agent, Plan-Text + Live-Repo, keine Konversationshistorie,
S5-S12-Verfahren) — ein zusätzlicher Durchlauf gegenüber S11/S12, weil Runde
1 strukturelle Blocker fand: 6 Blocker + 10 Should-Fix (Verdikt "needs
another draft pass"). Runde 2 (nach Einarbeitung + eigener
Live-Schema-Verifikation per `odoo-fields`-MCP-Tool gegen saas-19.4) fand
4 Blocker + 10 Should-Fix, alle mechanisch/lokal begrenzt (Verdikt "kein
dritter Entwurf nötig"). Alle 20 Funde eingearbeitet vor Implementierungsstart.

Alle drei Items bleiben innerhalb des bestehenden `orchestrator.py`-"stock"-
Schritts (`inventory.create_inventory_data`) bzw. im bereits laufenden
`master_data`-Schritt (R13s Tracking-Zuweisung) — kein neuer
`module_order`-Eintrag. "Keine neue Registrierungskette nötig" (§3-Referenz)
stimmt für 6 von 7 Punkten; einzige Ausnahme ist der Test-Link
(`test_run_config_unit.py`s `_FULL`-Payload prüft den `stock`-Dict per
exakter Gleichheit — jeder neue Key braucht eine Testanpassung).

**Vier zentrale Design-Entscheidungen aus der Planungsphase:**
1. **R14-Scope-Cut:** `purchase` läuft vor `stock` (`orchestrator.py`s
   `module_order`) — das zweite Warehouse existiert zum Zeitpunkt von
   `purchase.py`s Lauf noch nicht. S13 setzt nur den Quant-Anteil um, der
   Wareneingangs-Anteil bleibt offen (siehe R14-Abschnitt oben).
2. **`run_journal.py` bekommt einen neuen `ARCHIVE_FALLBACK_MODELS`-
   Mechanismus:** Archivieren (`active=False`) statt Hart-Löschen, wenn
   `unlink` scheitert — für `stock.warehouse`/`stock.location` (beide haben
   ein `active`-Feld, live bestätigt). `stock.lot` hat keins — bleibt
   dokumentierte Einschränkung, kein Fix. `delete_run`s Rückgabe bekommt ein
   drittes Feld `archived` (zusätzlich zu `deleted`/`failed`/`skipped`),
   UI-Cleanup-Meldung entsprechend erweitert.
3. **Settings-Gates** (`group_stock_multi_locations`/
   `group_stock_production_lot`, live bestätigt): Records werden **immer**
   erzeugt, nie vom Einstellungs-Zustand abhängig gemacht — bei nachweislich
   deaktivierter Einstellung nur ein Hinweis ("Einstellung X in Odoo
   aktivieren, um die erzeugten Daten in der UI zu sehen"), kein Skip (ein
   Skip hätte das Feature unsichtbar gemacht, ohne dass die Daten technisch
   unanlegbar gewesen wären). Probe liegt einmalig in
   `odoo_actions.get_enabled_features` (analog `crm_leads`), nicht verteilt
   auf mehrere Module.
4. **`RunContext.new_product_ids`** (neues additives Feld): nur von
   `master_data.py` in diesem Lauf frisch erzeugte Produkt-IDs — WP4s
   Lot-/Serial-Branch feuert nur dafür, nie für `use_existing`-
   Bestandsprodukte oder MRP-Komponenten/-Fertigprodukte (beide schreiben
   unabhängig in `ctx.product_ids`, ohne je in `new_product_ids` zu landen).

| WP | Inhalt | 🔒 | Voraussetzung |
|---|---|---|---|
| **WP1** ✅ | Gebündelte Live-Verifikation gegen `demo-test5.odoo.com` (`stock.warehouse`-Minimalfall, `tracking='lot'`-Schreib-/Read-back inkl. `product.template`, `stock.lot`-Namenskollision, `stock.location.barcode`-Namensraum, Unlink-/Archive-Verhalten für alle drei neuen Modelle, `action_apply_inventory` mit Lot-/Serial-Quants, Settings-Lesemechanismus) | nein | — |
| **WP2** ✅ | R15 Lagerplätze + R16 Location-Ebene Barcode: Sub-Locations, Location-Pool-Refactor der Quant-Schleife (Round-Robin statt Zufalls-Draw), `ARCHIVE_FALLBACK_MODELS`+`stock.location`, `MODEL_ACCESS_PROBES`/`FIELD_COMPAT_WHITELIST` additiv | nein | WP1 |
| **WP3** ✅ | R14 Multi-Warehouse (Quant-Anteil): 2. Warehouse vor dem `get_default_warehouse`-Lookup erzeugt (braucht kein 1. Warehouse), in WP2s Location-Pool eingehängt, `ARCHIVE_FALLBACK_MODELS`+`stock.warehouse` | nein | WP1, WP2 (Pool) |
| **WP4** ✅ | R13 Lot-/Serial-Tracking: `data_factory.assign_tracking`, `RunContext.new_product_ids`, tracking-bewusste Quant-/Lot-Erzeugung mit Lauf-weitem Serial-Deckel (`_MAX_SERIAL_RECORDS_PER_RUN`, entkoppelt von `avg_qty`) | nein | WP1, WP2 (Pool) |
| **WP5** ✅ | Peer-Review vor Merge (S5-S12-Verfahren), grüner Live-`test_suite.py` | — | WP1-WP4 Code steht |

**WP1-Ergebnisse (live gegen `demo-test5.odoo.com`, 2026-09-02):** 7 von 8
Checks bestätigten die Planannahme direkt. Eine Korrektur: Archive-Fallback
(`active=False`) funktioniert für `stock.warehouse` auch mit vorhandenem
Bestand, schlägt aber für `stock.location` fehl, solange sie noch einen Quant
enthält (derselbe Fehler wie beim Unlink) — Design-Entscheidung 2 oben
dokumentiert diese Asymmetrie bereits korrekt, keine Nacharbeit nötig.

**WP2-WP4-Umsetzung (2026-09-02):** in `modules/inventory.py` einem
gemeinsamen, überarbeiteten `create_inventory_data` umgesetzt statt drei
getrennten Anknüpfungspunkten — WP2 selbst sah diesen gemeinsamen
Location-Pool-Refactor als Grundlage für WP3/WP4 vor. Vollständige
Testabdeckung (Unit + 3 neue Live-Integrationsschritte) ergänzt, alle
Testing Design Patterns unten erfüllt. Unit-Suite 399/399 grün, Live-
`test_suite.py` 90/90 grün (Erstlauf); ein Zweitlauf zeigte den
vorbestehenden, dokumentierten Rate-Limit-Flake in `ODOO_ACTIONS`
(`has_create_access`-POST-Zähler) — alle S13-eigenen Schritte blieben in
beiden Läufen grün.

**WP5-Ergebnisse (2026-09-03):** unabhängiger Cold-Review-Agent (Opus,
Diff statt Plan-Text, gleiches Verfahren wie S12/WP5) fand keine Blocker,
5 echte Should-Fixes — alle behoben: (1) Produkt-Batch-Create war
All-or-Nothing auf dem Tracking-Feld, jetzt Retry ohne `tracking` bei
Fehlschlag; (2) blockierte `stock.lot`-Erstellung riss vorher das ganze
Stock-Modul mit, degradiert jetzt betroffene Produkte auf normale
Bestände; (3) Archiv-Fallback in `run_journal.py` meldete bei eigenem
Fehlschlag den falschen (Unlink-)Grund statt des eigenen — Mark-Punkt
korrigiert; (4) Testlücke geschlossen — kein Test prüfte, dass ein
explizit deaktiviertes Feature-Flag (`stock_multi_locations`/
`stock_lots`) die Erstellung nicht überspringt, nur den Hinweis auslöst;
(5) Live-Integrationstest für das zweite Warehouse konnte auf Altdaten
der geteilten Testinstanz falsch-positiv laufen, jetzt ID-basiert
abgegrenzt. `ODOO_GOTCHAS.md` um S13s Live-Befunde ergänzt. Unit-Suite
404/404 grün, Live-`test_suite.py` 90/90 grün (inkl. des vorher flakigen
`ODOO_ACTIONS`-Tests). Branch `s13-lager-tiefe` bereit zum Merge nach
`main` (Freigabe ausstehend).

**Pro Arbeitspaket verbindlich:** dieselben Testing Design Patterns wie jedes
bisherige Sprintpaket (siehe CLAUDE.md) — Pattern 1 (Location-Pool nie leer,
konstruktionsbedingt), Pattern 3 (jedes neue Flag aus → keine zusätzlichen
Calls), Pattern 4 (Read-back auf allen neuen Feldern), Pattern 5 (fehlende
Prerequisites → Skip, inkl. Koexistenz mit einem bereits erzeugten 2.
Warehouse), Pattern 6 (`lot_id`-m2o-Tupel), Pattern 7 (Tracking-Verteilung,
`assign_tracking` isoliert), Pattern 8 (`stock.lot`-Batch-Call-Count).

### S14 — WP-Sequenz (Prozess-Tiefe: R12, R18)

**Stand 2026-09-03.** Plan **zweimal** cold-reviewed vor Umsetzungsstart
(2× fremder Opus-Agent, Plan-Text + Live-Repo, keine Konversationshistorie,
S5-S13-Verfahren) — Runde 1 fand 6 Blocker + 9 Should-Fix, Runde 2 (nach
Einarbeitung) fand 1 Blocker + 13 Should-Fix, alle mechanisch/lokal begrenzt
(kein dritter Entwurf nötig, analog S13). WP1 (gebündelte Live-Verifikation
gegen `demo-test5.odoo.com`) lief **vor** Runde 1 und deckte dabei zwei
Fehler in diesem Dokuments eigenem R12/R18-Text auf (siehe unten) — beide
Cold-Review-Runden wurden entsprechend gebrieft, den ROADMAP-Text als
unzuverlässig zu behandeln, nicht als Bestätigung.

**Zwei Korrekturen an R12/R18s ursprünglichem Text, live gefunden (WP1):**
1. **`quality.check`/`quality.point.measure_on` existiert auf `demo-test5`
   (saas-19.4) auf **keinem** der beiden Modelle** — R18s ursprünglicher Text
   ("required + readonly, vom `point_id` abgeleitet") stammte aus dem
   `odoo-fields`-MCP-Tool-Cache, dessen Antwort zusätzliche Felder
   (`spreadsheet_id`, `obox_device_id`, `measure_frequency_*`) enthält, die
   auf eine vollständigere Quality-Control-Installation hindeuten als das,
   was auf `demo-test5` tatsächlich läuft. Live per echtem `fields_get`
   zweifach verifiziert. Nie setzen, nie in einem `search_read`-`fields`-
   Parameter anfragen (bricht sonst den ganzen Call).
2. **R18s bestehender `quality.point`-Erzeugungspfad in `mrp.py` schlägt
   heute live fehl**, nicht nur mit einem falschen Wert: `test_report_type:
   "none"` (heutiger Code) → `500`, `"Wrong value for
   quality.point.test_report_type: 'none'"`. Der Fehler wird von einem
   `except Exception`-Block geschluckt, nie sichtbar, weil der einzige Test
   den Pfad standardmäßig deaktiviert lässt. R18 ist deshalb kein "erweitere
   einen funktionierenden Pfad", sondern "repariere einen Pfad, der nie
   erfolgreich lief".

**Weitere zentrale Design-Entscheidungen:**
- `stock.warehouse.orderpoint.warehouse_id` wird **nie** gesetzt — live
  bestätigt, Odoo leitet es korrekt aus `location_id` her; im Code existiert
  ohnehin keine Quelle dafür (`get_default_warehouse` liefert nur die
  Location-ID zurück).
- `quality.point.bom_id` ist eine Compute-Fassade (live bestätigt: wird beim
  Schreiben kommentarlos verworfen, auch mit nicht-existenter ID) — der
  echte, schreibbare Verknüpfungspfad ist `apply_to='products'` +
  `product_ids`.
- R12s Zielmenge ist `(RunContext.new_product_ids | ctx.component_ids) ∩
  storable` — nicht nur `new_product_ids` wie ursprünglich geplant.
  `ctx.component_ids` wird nie aus Bestandsdaten vorbefüllt, jeder Eintrag
  ist also per Konstruktion ein Produkt, das `mrp.py` in diesem Lauf gerade
  erst angelegt hat — genauso kollisionssicher gegen `stock.warehouse.
  orderpoint`s live bestätigte Uniqueness-Constraint auf
  `(product_id, warehouse_id, location_id)`.
- `mrp.py`s Quality-Block wird von der Fertigungsauftrags-Erzeugung
  strukturell entkoppelt (`if created_bom_ids:` statt
  `if num_manufacturing_orders > 0 and created_bom_ids:`) — Quality Points
  brauchen keine MOs, die Kopplung war ein Verschachtelungs-Nebenprodukt.
  Zwei getrennte `try/except`-Blöcke (MO-Erzeugung, Quality-Erzeugung), damit
  ein MO-Fehlschlag Quality nicht mitreißt und umgekehrt.
- Zwei vorbestehende, unabhängige Bugs im selben Codebereich mitgefixt (nicht
  S14-Scope, aber in der Region, die WP3 ohnehin umbaut): `confirmed_mo_ids`
  `UnboundLocalError` bei leerem `to_confirm`; `test_mrp.py`s
  `CAPTURE_FIELDS`-Testzweig ließ den Quality-Block trotz Flag-Freischaltung
  nie erreichen (`num_manufacturing_orders` blieb 0).

| WP | Inhalt | 🔒 | Voraussetzung |
|---|---|---|---|
| **WP1** ✅ | Gebündelte Live-Verifikation gegen `demo-test5.odoo.com` (`quality.point`/`quality.check`-Vals inkl. `measure_on`-Fund, `stock.warehouse.orderpoint`-Name-Autofill + Uniqueness-Constraint, `bom_id`-Compute-Fassaden-Fund) | nein | — |
| **WP2** ✅ | R12 Nachbestellregeln: `stock.warehouse.orderpoint`-Erzeugung in `inventory.py`, eigenständige `orderpoint_min_qty`/`orderpoint_max_qty` (nicht von `avg_qty` abgeleitet), Funktionsende als zwei unabhängige Blöcke (Quant-Tail/Orderpoint-Batch) | nein | WP1 |
| **WP3** ✅ | R18 Quality Checks: `test_report_type`-Bugfix, `quality.check`-Erzeugung (neu), MO-Entkopplung, `data_factory.assign_quality_state` (neu, analog `assign_tracking`) | nein | WP1 |
| **WP4** ✅ | Peer-Review vor Merge (S5-S13-Verfahren), grüner Live-`test_suite.py` | — | WP2-WP3 Code steht |

**WP2-Ergebnisse (2026-09-03):** wie geplant umgesetzt, inkl. der
`Befund 6`-Präzisierung aus WP1 (Orderpoint-Zweig darf nicht mit dem
Quant-Zweig an `ctx.company_ids` sterben — `company_id` kommt für
Orderpoints aus `get_main_company_id`, nie aus `ctx.company_ids`). Volle
Pattern-1/3/5/7/8-Testabdeckung (Unit + 1 neuer Live-Integrationsschritt).

**WP3-Ergebnisse (2026-09-03):** `test_report_type: "none"` durch `"pdf"`
ersetzt (Befund 2 aus WP1 — Pfad war vorher noch nie erfolgreich
gelaufen), `bom_id` durch `apply_to='products'`+`product_ids` ersetzt
(Compute-Fassade), Quality-Block strukturell von der MO-Erzeugung gelöst
(`if created_bom_ids:` statt `if num_manufacturing_orders > 0 and
created_bom_ids:`), `quality.check`-Erzeugung neu, `confirmed_mo_ids`-
UnboundLocalError (vorbestehend) mitgefixt. `test_mrp.py`s
`CAPTURE_FIELDS`-Testlücke geschlossen (`num_manufacturing_orders` jetzt
auch >0 unter Capture) **und** ein unbedingter Live-Testschritt ergänzt,
der nicht hinter `ODOO_GENERATOR_CAPTURE_FIELDS` hängt — genau diese
Lücke war der Grund, warum der `test_report_type`-Bug nie auffiel.

**WP4-Ergebnisse (2026-09-03):** `advisor()`-Review (sieht die volle
Konversation, kein unabhängiger Cold-Review-Agent diesmal) fand 3 echte
Should-Fixes — alle behoben: (1) `static/app.js` fehlte komplett für die
vier neuen Config-Keys (`orderpoints_pct`/`orderpoint_min_qty`/
`orderpoint_max_qty`/`quality_fail_pct`) — `run_config.py` konnte sie
parsen, aber die UI konnte sie nie senden; live per Browser bis zum
`/api/preflight`-Response durchgetestet. (2) `quality_state` wurde direkt
in die `create()`-Vals geschrieben statt über Odoos eigene `do_pass`/
`do_fail`-Aktionen (live bestätigt: existieren, batch-fähig, setzen
`control_date`/`user_id` korrekt) — Bruch mit diese Codebase eigener
Native-vor-Manuell-Konvention, jetzt behoben. (3) `estimate_record_counts`
hatte keinen Eintrag für Quality Points/Checks. `ODOO_GOTCHAS.md` um
S14s Live-Befunde ergänzt. Unit-Suite 419/419 grün, Live-`test_suite.py`
92/92 grün (zweimal, ein bekannter Rate-Limit-Flake in `ODOO_ACTIONS`
tauchte einmal auf und verschwand beim Zweitlauf). Branch
`s14-prozess-tiefe` bereit zum Merge nach `main` (Freigabe ausstehend).

**Pro Arbeitspaket verbindlich:** dieselben Testing Design Patterns wie jedes
bisherige Sprintpaket (siehe CLAUDE.md) — Pattern 1 (Empty-Pool-Guards),
Pattern 3 (Prozent=0/Flag-aus-Skip), Pattern 4 (Read-back auf allen neuen
Feldern), Pattern 5 (fehlende Prerequisites → Skip, inkl. der neuen
`company_ids`-Guard-Präzisierung — Orderpoint-Zweig darf nicht mit dem
Quant-Zweig sterben), Pattern 7 (`orderpoints_pct`- **und**
`quality_state`-Verteilung, letztere über eine eigenständige, isoliert
testbare `data_factory`-Funktion), Pattern 8 (Batch-Call-Count für
Orderpoints/Quality-Points/-Checks).

### S15 — WP-Sequenz (Analytic Accounting: R20)

**Stand 2026-09-03.** WP1 (gebündelte Live-Verifikation gegen
`demo-test5.odoo.com`) gelaufen — Ergebnisse und die beiden Korrekturen am
ursprünglichen R20-Text stehen oben im R20-Abschnitt selbst (nicht hier
verdoppelt). **Beide Cold-Review-Runden gelaufen** (2× fremder Opus-Agent, Plan-Text +
Live-Repo, keine Konversationshistorie, S5-S14-Verfahren) — Runde 1 fand
1 echten Blocker + 5 Should-Fixes, Runde 2 (nach Einarbeitung) fand 0
Blocker + 3 weitere Should-Fixes, alle unten eingearbeitet (kein dritter
Durchgang nötig, analog S13). Plan ist damit freigegeben zur
Implementierung.

**Zentrale Design-Entscheidung (Grund für den eigenen WP2/WP3-Schnitt):**
R20 ist — anders als R12/R18 in S14 — **kein neues orchestriertes Modul**.
Es erweitert drei bestehende (`sale.py`, `purchase.py`, `expenses.py`) um
ein zusätzliches Feld auf bereits erzeugten Zeilen, ohne eigene
`WANTED_MODULES`/`MODULE_RUN_ORDER`/`orchestrator.py`-Eintrag — analog zu
R16 Produkt-Ebene (additiv in `master_data.py`), nicht analog zu R19
(eigenes Modul). Die "Referenz — Registrierungskette für ein neues
orchestriertes Modul" oben gilt deshalb **nicht** vollständig; nur die
Punkte 4-6 (Config-Parsing, Vorschau-Zeile, UI) sind einschlägig — von
Cold-Review 1 gegen `orchestrator.py`/`run_config.py` bestätigt, kein
Blocker.

Die Kostenstellen-Erzeugung (Plan + `account.analytic.account`s) braucht
genau einmal pro Lauf zu passieren, aber wird potenziell von drei
unabhängig gateten Modulen gebraucht (`sale`/`purchase`/`hr_expense`) —
ein Modul könnte laufen, ohne dass die anderen beiden es tun. Statt sich
auf `orchestrator.py`s tatsächliche Reihenfolge zu verlassen (`sale` liefe
zuerst, aber das zu einer stillen Voraussetzung zu machen wäre brüchig),
bekommt jedes der drei Module über einen gemeinsamen, lazy+memoized
`odoo_actions`-Helper Zugriff (`ctx.analytic_account_ids` als Cache,
gleiches Muster wie `mrp.py`s `_get_company_id()`, nur über `ctx` geteilt
statt über eine lokale Closure, weil hier mehrere getrennte Modul-
Funktionen denselben Zustand brauchen, nicht nur mehrere Call-Sites
innerhalb einer Funktion).
**Cold-Review-Should-Fix 2 (`ctx.analytic_account_ids`-Memoization):**
`mrp.py`s `_get_company_id()`-Vorbild memoized über eine Wrapper-Liste
gerade damit ein legitimes leeres Ergebnis trotzdem als "schon versucht"
zählt — ein `RunContext.analytic_account_ids: List[int]` per
Wahrheitswert geprüft würde bei einem legitim leeren ersten Versuch die
Kostenstellen-Erzeugung (und damit einen zweiten `account.analytic.plan`)
bei jedem weiteren Modul-Aufruf wiederholen. Feld wird stattdessen
`Optional[List[int]] = None`, geprüft über `is None`, nicht
Wahrheitswert.

**Cold-Review-Blocker 1 (kritisch, ändert WP3s Sale-Ansatz):** die
"nie einen bereits gesetzten Wert überschreiben"-Garantie lässt sich an
der ursprünglich geplanten Stelle in `sale.py` **nicht** einhalten.
`sale.py` baut Order-Zeilen als `(0,0,{...})`-Command-Tupel **inline** in
einem einzigen `sale.order.create()`-Aufruf (kein separater
`sale.order.line.create()`-Schritt) — nichts ist zu diesem Zeitpunkt
persistiert, und die verfügbaren Produkte werden nur mit `id` abgefragt,
`service_tracking` gar nicht. Odoos eigene serverseitige Analytic-
Ableitung für `service_tracking='task_in_project'`-Zeilen (S7/R8) läuft
aber erst bei `action_confirm` — zum Vals-Bau-Zeitpunkt ist "hat diese
Zeile schon einen Wert" für jede Zeile trivial falsch, die Prüfung hat
also nichts zu prüfen. WP1s Live-Test (manuell gesetzter Wert überlebt
`action_confirm` unverändert) deckt außerdem nicht ab, was bei einer
**echten** `task_in_project`-Zeile passiert, wenn dort vorher schon
manuell etwas gesetzt wurde — überschreibt Odoos native Ableitung dann,
wird sie übersprungen, oder gibt es einen Konflikt? Ungetestet, und genau
das Szenario, das dieses Item explizit vermeiden soll.

**Fix (übernimmt Cold-Review-Should-Fix 3s Vorschlag, ändert `sale.py`s
Ansatz auf das `assign_quality_state`-Muster statt `assign_tracking`):**
`sale.py` erzeugt und bestätigt Order-Zeilen **unverändert wie heute**,
ohne `analytic_distribution` im Erzeugungs-Vals. **Nach**
`confirm_sale_orders` (aber weiterhin vor `accounting.py`, da `sale.py`
selbst an Pipeline-Position 3 läuft) liest ein neuer Schritt die
tatsächlichen `analytic_distribution`-Werte der bestätigten Order-Zeilen
zurück (`ctx.confirmed_order_ids`, dort bereits am Ende der Funktion
vorhanden), filtert auf noch-leere Zeilen, zieht einen konfigurierbaren
Anteil davon, gruppiert die gezogenen Zeilen nach zufällig zugewiesener
Kostenstelle (wenige Gruppen, z. B. 3 Konten) und schreibt pro Gruppe
**einen** `write()`-Aufruf (Pattern 8: wenige Batch-Writes, nicht ein
`write()` pro Zeile). Robuster als ein Vorab-`service_tracking`-Produkt-
Ausschluss: lässt Odoos native Ableitung erst wirklich passieren, prüft
danach den echten Zustand, statt ihn vorherzusagen — entspricht der
bestehenden "native vor manuell"-Konvention dieser Codebase
(`action_apply_inventory`/`action_confirm`/`do_pass`/`do_fail` u. a.).
`purchase.py`/`expenses.py` haben dieses Problem **nicht** — Odoo leitet
für Purchase-/Expense-Zeilen nichts automatisch her, beide bleiben beim
einfacheren Vals-vor-`create_batch`-Muster (`assign_analytic_distribution`
als geteilte `data_factory`-Funktion, analog `assign_tracking`).

**Cold-Review-Should-Fix 6 (Batch-Form-Unterschied, kein Blocker, nur
Dokumentationslücke):** `sale.py` erzeugt Orders einzeln in einer Schleife
(`client.create`), `purchase.py` batcht auf Order-Ebene
(`client.create_batch`, `purchase.py:217`) — der geteilte
`data_factory`-Helper passt trotzdem für beide (er operiert auf der
jeweiligen `lines`-Liste vor der Erzeugung, unabhängig davon ob die
äußere Order-Erzeugung selbst gebatcht ist), aber WP3 muss das explizit
so benennen statt einheitliches Batching zu unterstellen.

**Cold-Review-Should-Fix 5 (live nachgeprüft, erledigt):**
`account.analytic.account`s einzige Pflichtfelder sind `name` und
`plan_id` (live per `fields_get` bestätigt) — `company_id` ist **nicht**
erforderlich und wird beim Fehlen automatisch auf die aktuelle Firma
gesetzt (live per Create-ohne-`company_id`+Read-back bestätigt). Der
WP2-Helper muss `company_id` also nicht selbst auflösen.

**Cold-Review-Should-Fix 4 (Test-Erinnerung für WP2):**
`tests/unit/test_run_config_unit.py`s `_FULL`-Payload (Exact-Equality-
Check) braucht einen neuen `"analytic"`-Eintrag, sobald
`build_selections` `sel.analytic` setzt — derselbe Test, den S14/WP2
schon einmal wegen eines neuen `stock`-Keys anpassen musste.

**Cold-Review Runde 2 (2026-09-03, zweiter fremder Opus-Agent, Plan-Text +
Live-Repo, keine Konversationshistorie) — 0 Blocker, 3 Should-Fixes, alle
eingearbeitet:**

1. **Load-bearing und ungetestet:** Blocker-1-Fix hängt daran, dass
   `write()` auf einer bereits **bestätigten** (`state='sale'`)
   `sale.order.line` funktioniert — WP1 hatte das nur für eine bereits
   **gebuchte** `account.move.line` bestätigt, ein anderes Modell in
   anderem Lebenszyklus-Zustand. **Live nachgeprüft (2026-09-03):** Order
   erzeugt, bestätigt (`state` → `sale`), `write()` mit
   `analytic_distribution` auf die jetzt bestätigte Zeile — `True`,
   Read-back zeigt den neuen Wert. Blocker-1-Fix ist damit vollständig
   live abgesichert, kein Restrisiko mehr.
2. **`purchase.py`s Vals-Form passt nicht direkt.** `purchase.py:196-206`
   baut `lines` als Liste von `(0, 0, {...})`-Tupeln **innerhalb** der
   Order-Schleife, nie als eigene flache Liste — anders als `expenses.py`
   (dort passt `assign_analytic_distribution` direkt auf den flachen
   `vals_list`). Fix: beim Bau jeder Zeile das innere Dict zusätzlich in
   eine separate `po_line_vals_list` einhängen (Python hält nur eine
   Referenz — Mutation dieses Dicts über die separate Liste mutiert
   dasselbe Objekt, das auch im `(0,0,dict)`-Tupel steckt), danach
   **einmal** `assign_analytic_distribution(po_line_vals_list, ...)`
   aufrufen, bevor `client.create_batch('purchase.order', po_vals_list)`
   läuft. `expenses.py` bleibt beim direkten flachen Aufruf.
3. **`ModuleSelections.analytic` hatte keine Shape-Skizze** — jedes
   andere Dict-Feld (`stock`, `mrp`, `hr_expense`, `documents`) trägt
   einen `# shape: {...}`-Kommentar (`config.py:31-75`). Nachgetragen:

   ```python
   analytic: dict = field(default_factory=dict)
   # analytic shape: {"sale_pct": int, "purchase_pct": int, "expense_pct": int}
   # (S15/R20 additive) — jeder Key wird unabhängig von sale.py/purchase.py/
   # expenses.py gelesen, nicht unter dem enabled-Gate eines einzelnen
   # Eltern-Moduls verschachtelt (anders als crm_lost unter crm) — näher an
   # documents' Top-Level-Form. 0 auf einem Key ist dessen eigener
   # Aus-Schalter, gleiches Präzedens wie S14s orderpoints_pct/
   # quality_fail_pct — kein separates enabled-Bool.
   ```

Zeitablauf (`sale` vor `account`) und die "eine `write()` pro
Kostenstellen-Gruppe"-Batching-Idee wurden in Runde 2 gegen den echten
Code bestätigt, keine Änderung nötig.

**Offene Ergonomie-Frage für WP2 (kein Blocker, bei Implementierung final
zu entscheiden):** eigene kompakte GUI-Karte "Kostenrechnung" mit drei
Reglern (Verkauf/Einkauf/Spesen %) vs. je ein Regler direkt in den
bestehenden Verkauf-/Einkauf-/Spesen-Karten. Entwurf setzt auf die eigene
Karte — Analytic Accounting ist konzeptionell ein eigenständiges Feature,
kein Bestandteil von Sales/Purchase/Expenses selbst, und drei über
unzusammenhängende Karten verstreute Regler wären schwerer zu finden als
eine benannte Karte.

| WP | Inhalt | 🔒 | Voraussetzung |
|---|---|---|---|
| **WP1** ✅ | Gebündelte Live-Verifikation gegen `demo-test5.odoo.com` (`analytic_distribution`-Format auf allen 5 Zielmodellen, Plan-1-Sättigung, Wizard-Propagation, gebuchte-Move-Line-Schreibbarkeit, `hr.department`-Bestand, `account.analytic.account`-Pflichtfelder, `write()` auf bestätigter `sale.order.line`) | nein | — |
| **WP2** ✅ | Infrastruktur: `odoo_actions.get_or_create_analytic_accounts` (neuer Plan + Kostenstellen, lazy+memoized über `ctx.analytic_account_ids: Optional[List[int]] = None`, `is None`-Check), `data_factory.assign_analytic_distribution` (Vals-Mutations-Form, analog `assign_tracking` — für `expenses.py` direkt, für `purchase.py` über eine separate flache Line-Vals-Liste, siehe Runde-2-Fund 2), `config.py`/`run_config.py`-Wiring (`ModuleSelections.analytic`, Shape siehe Runde-2-Fund 3, `RunContext.analytic_account_ids`, `build_selections`, `estimate_record_counts`, `test_run_config_unit.py`s `_FULL`-Payload), `static/app.js`-UI (eigene Karte, siehe Ergonomie-Frage oben) | nein | WP1 |
| **WP3** ✅ | Einbindung: `purchase.py`/`expenses.py` nutzen den WP2-`data_factory`-Helper (Details siehe Runde-2-Fund 2). `sale.py` bekommt einen eigenen Read-nach-Confirm-dann-write-Schritt (siehe Blocker-1-Fix oben, gruppiertes `write()` pro Kostenstelle, live abgesichert) — kein Vals-Mutations-Aufruf des WP2-Helpers | nein | WP2 |
| **WP4** ✅ | Peer-Review vor Merge (S5-S14-Verfahren), grüner Live-`test_suite.py` | — | WP2-WP3 Code steht |

**WP2/WP3-Ergebnisse (2026-09-03):** wie im zweifach cold-reviewten Plan
umgesetzt. Live per Browser bis zum `/api/preflight`-Response
durchgetestet (UI → Payload → `estimate_record_counts`): "Kostenrechnung"-
Karte, alle drei Prozent-Regler, alle vier neuen Vorschau-Zeilen
(`Kostenrechnungs-Zeilen Verkauf/Einkauf/Spesen (ca.)`, `Kostenstellen`)
erscheinen korrekt. Unit-Suite 448/448 grün (23 neue Tests), Live-
`test_suite.py` 95/95 grün (3 neue Live-Endpunkte, je einer pro Modul,
gegen `demo-test5.odoo.com`).

**WP4-Ergebnisse (2026-09-03):** unabhängiger Cold-Review-Agent (Diff
statt Plan-Text, gleiches Verfahren wie S12-S14/WP5) fand **0 Blocker, 0
Should-Fixes** — bestätigte explizit, dass beide vorherigen Cold-Review-
Runden-Funde (Blocker-1-Redesign in `sale.py`, `is None`-Memoization,
`purchase.py`s Referenz-Trick) korrekt im Code ankamen, sowie die
Referenz-Semantik der `po_line_vals_list`-Mutation, die Gruppierungslogik
von `sale.py`s `write()`-Aufrufen gegen Odoos "identische Vals pro Call"-
Regel, und die Key-Namen-Konsistenz zwischen `static/app.js`,
`run_config.py` und den drei konsumierenden Modulen. Ein Randfall notiert
(kein Blocker): `get_or_create_analytic_accounts` gated nicht explizit auf
`'account' in ctx.installed_modules` — in der Praxis eine harte Odoo-
Abhängigkeit aller drei aufrufenden Module, und der eigene `try/except`
degradiert ohnehin sauber auf `[]`. Branch
`s15-analytic-accounting-planning` bereit zum Merge nach `main`
(Freigabe ausstehend).

**Pro Arbeitspaket verbindlich:** dieselben Testing Design Patterns wie
jedes bisherige Sprintpaket (siehe CLAUDE.md) — Pattern 1 (leerer
Kostenstellen-Kandidatenpool), Pattern 3 (Prozent=0/kein Modul gewählt →
Skip), Pattern 4 (Read-back auf allen drei Zielmodellen, inkl. des
Wizard-Übertragungspfads und `sale.py`s Read-nach-Confirm-Schritts),
Pattern 5 (fehlende Prerequisites → Skip), Pattern 7
(`assign_analytic_distribution`s Anteils-Verteilung UND `sale.py`s
eigener Zieh-Anteil, isoliert testbar), Pattern 8 (Batch-Call-Count für
die Kostenstellen-Erzeugung UND `sale.py`s gruppierte
Kostenstellen-`write()`-Aufrufe — wenige, nicht einer pro Zeile).

### S16 — WP-Sequenz (Multicompany: R17)

**Stand 2026-09-04.** WP1 (Architektur-Spike, gebündelte Live-Verifikation gegen
`demo-test5.odoo.com`) gelaufen — Ergebnisse und die daraus resultierende
Design-Entscheidung (neues Feld statt Rename) stehen oben im R17-Abschnitt
selbst (nicht hier verdoppelt). **Cold-Review Runde 1 gelaufen** (unabhängiger
Opus-Agent, nur Plantext + Live-Repo, keine Konversationshistorie, S5-S15-Verfahren)
— fand **6 Blocker + 12 Should-Fixes**, alle eingearbeitet (u. a. eine
fehlende Registrierungskette, die die Karte unsichtbar/unklickbar gemacht hätte;
ein D7-Cleanup-Loch, das pro Lauf eine unlöschbare Firma+Kontenplan auf der
Live-Instanz hinterlassen hätte; das Kontenplan-Laden selbst dafür gestrichen
statt gefixt, da sein einziger Nutzen kosmetisch war). **Cold-Review Runde 2
gelaufen** (zweiter unabhängiger Opus-Agent, gleiches Verfahren) — fand
**4 weitere Blocker + 7 Should-Fixes**, alle unten eingearbeitet. Wichtigster
Fund: Runde 1s eigener Live-Befund zur `company_ids`-Record-Rule-Maskierung
widersprach sich selbst intern — per Live-Nachtest (2026-09-04) aufgelöst:
**es gibt für den API-Key-User keinerlei Maskierung**, gefiltert oder nicht
(Detail siehe R17-Abschnitt Punkt 3). Das kehrt den ursprünglichen Grund für
"`company_ids`-Erweiterung nicht empfohlen" um: nicht weil eine Erweiterung
ein Leck öffnen würde, sondern weil das Leck (`connect_service.fetch_existing_data`
ungefiltert nach `company_id`) bereits **heute** existiert, sobald dieser Sprint
das erste Firma-2-Produkt anlegt — das macht den Filter in `fetch_existing_data`
zu einem echten WP2-Pflichtschritt, kein optionales Zukunfts-Item mehr. 0
Blocker in Runde 2 wären nötig gewesen für eine dritte Runde; da alle 4 Runde-2-
Blocker mechanische Text-/Logikkorrekturen ohne neue offene Live-Fragen sind
(die eine echte Live-Frage, B1, wurde noch in dieser Runde durch einen
zusätzlichen Spike aufgelöst), gilt der Plan nach Einarbeitung als
freigegeben zur Implementierung — kein dritter Durchgang nötig, analog S13/S15.

**Zentrale Design-Entscheidung (Grund für den eigenen `modules/multicompany.py`-
Zuschnitt statt additiver Erweiterung wie R20):** anders als R20 (nur ein
zusätzliches Feld auf bereits erzeugten Zeilen bestehender Module) erzeugt R17
echte **neue** Records eines Typs, den kein bestehendes Modul kennt (eine
zweite `res.company` plus ausschließlich für sie erzeugte Partner/Produkte/ein
Warehouse) — das ist strukturell näher an R19 (Expenses, eigenes Modul) als an
R20. `modules/multicompany.py` bekommt daher einen echten
`WANTED_MODULES`/`MODULE_RUN_ORDER`/`orchestrator.py`-Eintrag 🔒, keine
Gate-only-Erweiterung eines bestehenden Moduls.

**Pipeline-Platzierung (🔒, Architekten-Freigabe Teil dieses Plans):** letzter
Schritt, nach `documents` (aktuell der letzte Eintrag in `orchestrator.py`s
`module_order`). Nicht auf `installed_modules` gegated (keine echte,
probebare Odoo-App — gleiches Muster wie `documents` selbst: hardcoded
`True` im `module_order`-Tupel, interner Skip über
`ctx.module_selections.multicompany.get("enabled")`, Pattern 3). Begründung
für "zuletzt": die neue Firma und ihre Records dürfen von keinem früher
laufenden Modul (alle bisherigen Module arbeiten ausschließlich gegen Firma 1)
versehentlich konsumiert werden können — Platzierung an letzter Stelle macht
das strukturell unmöglich, statt es nur durch Disziplin an jeder Call-Site zu
vermeiden.

**Pool-Isolation (load-bearing, Grund für das eine neue `RunContext`-Feld):**
`master_data.py` befüllt `ctx.company_ids` (Partner-Ids) und `ctx.product_ids` —
beide von `sale.py`/`purchase.py`/`accounting.py`/etc. als Quelle für
Firma-1-Belege gelesen. Würden die neuen Firma-2-Partner/-Produkte in diese
geteilten Pools gemischt, könnte ein früher laufendes Modul (bei anderer
Pipeline-Reihenfolge, oder bei künftigem Code, der diese Prämisse nicht kennt)
versehentlich ein Firma-2-Produkt in einen Firma-1-Beleg ziehen — Odoo würde
das beim `write()`/`action_confirm()` zwar ablehnen (Punkt 2 im R17-Spike-
Befund), aber ein Laufzeitfehler ist kein Ersatz für einen Pool, der die
Verwechslung von vornherein unmöglich macht. **Cold-Review-Korrektur (S12):**
der ursprüngliche Plan sah dafür zwei zusätzliche `RunContext`-Felder vor
(`multicompany_partner_ids`/`multicompany_product_ids`) — Review-Einwand: in
diesem Minimal-Scope liest nichts diese Felder nach der Erzeugung wieder
(`multicompany.py` läuft zuletzt, nichts Nachgelagertes existiert), sie wären
also reiner Schreib-ohne-Leser-Zuwachs an einer 🔒-Datei. Gestrichen — die
erzeugten Partner-/Produkt-Ids bleiben lokale Variablen innerhalb
`modules/multicompany.py`, kein `ctx`-Feld nötig. Nur `ctx.res_company_ids`
bleibt (hat einen klaren zukünftigen Leser: jeder spätere Code, der wissen
muss, ob/welche zweite Firma existiert).

```python
# config.py — RunContext, ein neues Feld (S16/R17):
res_company_ids: List[int] = field(default_factory=list)
# Echte res.company-Ids, die dieser Lauf angelegt hat — disambiguiert von
# company_ids oben, das trotz seines Namens res.partner-Ids hält. Minimal-
# Scope: höchstens ein Eintrag, befüllt von genau einer Stelle
# (modules/multicompany.py, ein Aufruf pro Lauf über orchestrator.py). Flache
# Liste statt second_company_id, damit ein künftiger N-Firmen-Scope keine
# weitere Schema-Änderung braucht. WICHTIG: bewusst kein
# Optional[List[int]]-Memoization-Sentinel wie ctx.analytic_account_ids (das
# Muster existiert dort, weil DREI unabhängige Module denselben Helfer lazy
# aufrufen könnten — hier gibt es nur einen Aufrufer). Sollte je ein zweiter
# Aufrufer hinzukommen, MUSS zuerst auf den Optional[...]-Sentinel
# umgestellt werden, sonst entsteht bei jedem weiteren Aufruf eine dritte
# Firma.

# ModuleSelections, neues Feld:
multicompany: dict = field(default_factory=dict)
# multicompany shape: {"enabled": bool, "partner_count": int, "product_count": int}
# (S16/R17). Eigenes orchestriertes Modul (modules/multicompany.py, eigener
# WANTED_MODULES/MODULE_RUN_ORDER/orchestrator.py-Eintrag) — läuft ZULETZT
# (nach "documents"). Kein Kontenplan-Bedarf (siehe R17 Punkt 1) — die neue
# Firma bleibt ohne Chart of Accounts, kein Kontenplan-Laden im Scope.
```

**API-User-`company_ids`-Erweiterung: bewusst NICHT im Scope, weder
automatisiert noch als manueller Schritt (Cold-Review-Korrektur — der
ursprüngliche Plan hatte hier sowohl eine falsche Prämisse als auch ein
übersehenes echtes Risiko, siehe R17-Abschnitt Punkt 3 für Details).**
Konsequenz: die neue Firma ist für reguläre Odoo-Nutzer nicht automatisch im
Firmenumschalter sichtbar — akzeptierte, rein kosmetische Einschränkung des
Minimal-Scopes, keine Funktionseinbuße für die per JSON2 erzeugten Records
selbst (die entstehen unabhängig davon korrekt mit `company_id`=neue Firma).

| WP | Inhalt | 🔒 | Voraussetzung |
|---|---|---|---|
| **WP1** ✅ | Architektur-Spike: gebündelte Live-Verifikation gegen `demo-test5.odoo.com` (Kontenplan-Ladepfad inkl. Country/Template-Kompatibilität — Ergebnis: nicht im Scope, siehe R17 Punkt 1 —, Referenzierte-Records-Umhäng-Verweigerung, `get_main_company_id`-Blast-Radius über alle 4 Call-Sites + 3 weitere `res.company`-Helper). **Nachtrag aus Cold-Review Runde 2:** dreifach-parallele Read-Back-Probe (id-Domain/`company_id`-Domain/komplett ungefiltert) klärte einen Selbstwiderspruch im ursprünglichen "Record-Rule-Maskierung"-Befund endgültig — keine Maskierung, siehe R17 Punkt 3. Separat: `write(active=False)` auf eine Firma, die bereits ein Warehouse hält, live bestätigt (stützt WP2s `ARCHIVE_FALLBACK_MODELS`-Ergänzung) | nein | — |
| **WP2** | Infrastruktur — **vollständige Registrierungskette** (Cold-Review-Blocker B1/B2 Runde 1, analog §3s "Referenz — Registrierungskette", hier mit pseudo-Modul-Abweichung): `config.py` (`RunContext.res_company_ids` + `ModuleSelections.multicompany`, siehe oben) 🔒; `run_config.py`s `PSEUDO_MODULES` (`"multicompany"` ergänzen, **nicht** `WANTED_MODULES` — keine echte Odoo-App, kein `installed_modules`-Gating nötig, Runde 2 bestätigt: `active_progress_keys` hat einen eigenen pseudo-Zweig, gated nur auf Auswahl) + `MODULE_LABELS` + `MODULE_RUN_ORDER` (nach `"documents"`, **exakt** das Literal `("multicompany"` ohne Leerzeichen nach der Klammer — `test_run_config_unit.py:99`s Invariante sucht `src.index(f'("{key}"')` im rohen `orchestrator.py`-Text, Should-Fix S4 Runde 2) + `build_selections` + `estimate_record_counts` (**eigene, von "Kontakte"/"Produkte" verschiedene Labels** wie `"Kontakte (2. Firma)"`/`"Produkte (2. Firma)"` — `estimate_record_counts` ist ein einfaches Dict, ein kollidierender Key überschreibt master_datas Zahl kommentarlos, Should-Fix S1 Runde 2) + `test_run_config_unit.py`s **zwei** betroffene Stellen: Zeile mit `selected == _ALL_INSTALLED \| {"documents", "analytic"}` wird `\| {"documents", "analytic", "multicompany"}` (nur `_FULL`s Auswahl-Dict bekommt den Block, **`_ALL_INSTALLED` selbst NICHT** — das würde eine fiktive echte Odoo-App vortäuschen, Should-Fix S3 Runde 2), UND separat `test_run_config_unit.py:240`s `keys == ["stammdaten", "crm", "sale", "documents"]`-Assertion (S16 landet dort ebenfalls, weil `MODULE_RUN_ORDER`-Mitgliedschaft + Auswahl reicht — anders als `analytic`, das bewusst nie in `MODULE_RUN_ORDER` steht, Should-Fix S3 Runde 2); `static/app.js:257`s hardcodierte `isPseudo`-Prüfung um `"multicompany"` erweitern (zweite hardcodierte Pseudo-Modul-Stelle, sonst bleibt die Karte dauerhaft deaktiviert — B2 Runde 1) **plus** einen `ICONS`-Eintrag für den neuen `MODULE_DEFS`-Key ergänzen (`iconSvg` fällt bei fehlendem Key still auf ein leeres `<svg>` zurück, kein Fehler — Should-Fix S7 Runde 2; Runde 2 bestätigt: sonst nichts in `app.js` hardcodet ein Modul-Set, `renderModuleGrid`/`activeModuleKeys`/`buildPayload` iterieren durchgängig `MODULE_DEFS`); `odoo_actions.MODEL_ACCESS_PROBES["multicompany"] = ["res.company"]` + `probe_model_access`s hardcodiertes Pseudo-Modul-Tupel (`odoo_actions.py:317`) um `"multicompany"` erweitern — **und den Probe-Wert tatsächlich in `modules/multicompany.py` konsumieren** (`if not ctx.model_access.get('res.company', True): ctx.skipped_modules.add("multicompany"); return`, analog `modules/documents.py:285-288` — ohne diesen expliziten Check meldet die Progress-Zeile "Fertig" für ein Modul, das nichts getan hat, Should-Fix S2 Runde 2; `res.company`-Anlegen ist ein echtes, oft eingeschränktes Recht, verwandelt einen harten Fehlschlag in einen sauberen Pattern-3-Skip); `odoo_actions.create_second_company` (Firma anlegen — nur `name` aus `ctx.name_banks.get('company_names')` per deterministischem Index gewählt, **einheitlich** `idx = ctx.criteria.num_companies + len(ctx.res_company_ids)` (Blocker B3 Runde 2 — Erstentwurf nannte in WP2 und WP3 zwei widersprüchliche Indexformeln, die zweite kollidierte garantiert mit master_datas erster Partnerfirma; Modulo + `_unique_name`-artiges Suffix-Fallback für den Fall `num_companies >= len(pool)`, insbesondere gegen den nur 5 Einträge großen `fallback_data.FALLBACK_COMPANIES`-Pool bei UI-Maximum 20 Firmen), plus `currency_id` von Firma 1 übernommen; **kein** `country_id`/Kontenplan, siehe R17 Punkt 1; **kein Barcode** auf den WP3-Produkten — `master_data.py:59-62`s Barcode-Dedup liest ungefiltert, Runde 2s B1-Nachtest zeigt zwar keine Maskierung mehr, ein Barcode wäre also technisch sichtbar, aber ungeprüft gegen Eindeutigkeits-Constraints über Firmen hinweg — einfach vermeiden); **`connect_service.fetch_existing_data`s zwei Domains (`:109-124`) um einen `company_id`-Filter ergänzen** (`['|', ['company_id','=',False], ['company_id','=', <Firma-1-Id>]]`) — **echter WP2-Pflichtschritt, kein optionales Future-Item** (Runde 2, Konsequenz aus R17 Punkt 3: da keine Record-Rule-Maskierung existiert, zieht jeder spätere `use_existing`-Lauf sonst Firma-2-Produkte/-Partner ungefiltert in den geteilten `ctx.product_ids`/`ctx.company_ids`-Pool, sobald dieser Sprint das erste Firma-2-Produkt anlegt); `run_journal.py`s `ARCHIVE_FALLBACK_MODELS` um `"res.company"` ergänzen (D7-Cleanup-Netz — `res.company` lässt sich oft nicht `unlink`en sobald sie referenziert wird; Archivieren als Fallback **jetzt live bestätigt**: `write(active=False)` auf eine Firma, die bereits ein Warehouse hält, funktioniert, 2026-09-04 nachgeprüft, war zuvor unverifiziert); neues `modules/multicompany.py`-Grundgerüst + `orchestrator.py`-Registrierung als letzter Pipeline-Schritt 🔒 (Platzierungs-Begründung oben); `static/app.js`-UI-Karte | ja | WP1 |
| **WP3** | Befüllung: `modules/multicompany.py` erzeugt `partner_count` Partner + `product_count` Produkte (`company_id=ctx.res_company_ids[0]`, batched, lokale Variablen — siehe Pool-Isolation oben) + ein Warehouse für die neue Firma. `odoo_actions.create_second_warehouse` (R14, `odoo_actions.py:113`) nimmt `company_id` bereits als Parameter — `inventory.py:55` übergibt heute nur zufällig `get_main_company_id(client)`, kein Refactor an R14-Code nötig, nur `create_second_warehouse(client, ctx.res_company_ids[0])` aufrufen. **Kein Namens-Parameter (Blocker B2 Runde 2 — Erstentwurf verlangte "einen passenden Namen übergeben" bei gleichzeitiger "kein Refactor nötig"-Aussage, ein Widerspruch: die Funktion hat keinen Namens-Parameter, `"Lager 2 (<suffix>)"`/Code `WH2<suffix>` sind intern gebaut).** Default-Name bleibt — Name/Code sind pro Firma eindeutigkeitsgeprüft, `"Lager 2 (...)"` unter Firma 2 ist bereits gültig, kein Aufwand für kosmetische Umbenennung gerechtfertigt. **Vor Umsetzung live prüfen:** legt Odoo beim Anlegen einer `res.company` bereits automatisch ein Standard-Warehouse an? Ein `search_read('stock.warehouse', [['company_id','=', <neue id>]])` direkt nach der Firmen-Erzeugung klärt das — falls ja, entfällt dieser Schritt oder wird zu "zweitem Warehouse für Firma 2". `create_second_warehouse` selbst ist bereits live getestet (`tests/integration/test_inventory.py`s "Step 4 — S13/R14", `modules/inventory.py:68`) — WP3 braucht dafür **keinen eigenen neuen** Live-Test, nur den bestehenden, andersparametrisierten Call. Produktnamen-Quelle: `data_factory.build_products`s kategorisierte Form (`{"services": [...], ...}`) kommt aus `creative_atoms`, das nur lokal in `orchestrator.run()` existiert und nie auf `ctx` landet — `multicompany.py` erreicht es nicht. Stattdessen `ctx.name_banks.get('product_names')` (flache Liste) + `fallback_data.FALLBACK_PRODUCTS`, Vals selbst gebaut (einfache Produkte ohne Barcode, siehe WP2-Notiz oben, keine Service/Consumable/Storable-Kategorisierung nötig für diesen Scope). `'stock' in ctx.installed_modules`-Gate für den Warehouse-Schritt ergänzen (fehlte im Erstentwurf) | nein | WP2 |
| **WP4** | Peer-Review vor Merge (S5-S15-Verfahren, Diff statt Plan-Text), grüner Live-`test_suite.py`. `create_second_company`/Firmenerzeugung bekommt **nur** Unit-Test-Abdeckung (gemockter Client). **Begründung korrigiert (Blocker B4 Runde 2 — der Erstentwurf berief sich auf ein falsches Präzedens):** `create_second_warehouse` (R14/S13) hat entgegen der ursprünglichen Behauptung sehr wohl einen Live-Integrationstest (`tests/integration/test_inventory.py:145-190`, 4 Grep-Treffer) — dessen eigener Kommentar akzeptiert dort sogar ausdrücklich anfallenden Warehouse-Rückstand auf der geteilten Instanz. Der echte Grund, hier **anders** zu entscheiden als S13: eine im Firmenumschalter sichtbare, dauerhaft archivierte `res.company` ist deutlich schwereres Live-Restmaterial als ein zusätzliches Warehouse, und war (bevor das Kontenplan-Laden gestrichen wurde) ohnehin schon der teuerste Teil dieses Sprints — WP1s einmalige manuelle Live-Verifikation reicht als Nachweis, dass der Erzeugungspfad funktioniert, ein sich bei jedem Suite-Lauf wiederholender Live-Test nicht. Test-Runner-Registrierung (oft vergessenes Detail, R19-Präzedens `ad26baa`): neues Testmodul in **beiden** Runnern eintragen — `tests/unit/unit_suite.py` (Import + `_MODULES`) UND `tests/integration/test_suite.py` (Import + Suite-Liste) | — | WP2-WP3 Code steht |

**Bekannte, akzeptierte Einschränkungen:**
- **(mode-Gate)** `build_selections` gibt bei `mode != "both"` früh zurück
  (`run_config.py:206-207`), `static/app.js` blendet den Modul-Bereich
  außerhalb "both" aus — ein "Nur Stammdaten"-Lauf erzeugt daher nie eine
  zweite Firma. Bewusste, nicht versehentliche Entscheidung: Multicompany ist
  inhaltlich Stammdaten-nah, aber ohne einen "both"-artigen Lauf ergibt eine
  zweite, leere Firma ohnehin wenig Demo-Wert.
- **(un-journallierter Kontakt, Should-Fix S6 Runde 2)** `res.company.create()`
  legt serverseitig automatisch einen zugehörigen `res.partner` an
  (`partner_id` ist `required`) — dieser läuft nie durch `JournalingClient.create`
  und ist strukturell derselbe Cleanup-Rest wie der gestrichene Kontenplan,
  nur proportional harmlos (ein Kontakt statt eines ganzen Kontenplans).
  Akzeptiert, nicht weiter behandelt.

**Pro Arbeitspaket verbindlich:** dieselben Testing Design Patterns wie jedes
bisherige Sprintpaket (siehe CLAUDE.md) — Pattern 1 (leerer
Partner-/Produkt-Namenspool), Pattern 2 (LLM liefert `None`/leer für
`company_names`/`product_names` → Fallback), Pattern 3
(`multicompany.enabled=False` → keine API-Calls), Pattern 4 (Read-back auf
Partnern/Produkten/Warehouse nach Erzeugung — **nicht** auf einem Kontenplan,
der nicht mehr im Scope ist), Pattern 5 (fehlende Prerequisites, z. B. leere
`ctx.name_banks['company_names']`, → Skip mit Fallback-Namen statt Crash),
Pattern 8 (Partner-/Produkt-Erzeugung batched, nicht in einer Schleife pro
Record).

## 5. Umsetzungsreihenfolge

Jedes Paket endet mit grüner `test_suite.py` gegen die Live-Instanz (CLAUDE.md-Pflicht). Empfohlene Sprints:

| Sprint | Inhalt | Begründung |
|---|---|---|
| **S1 — Bugfixes kritisch** ✅ | B1, B2, B3 (+ B16 als Beifang) | Kleine, isolierte Fixes; B1 schaltet verlorene Features frei — abgeschlossen, verifiziert 2026-09-02 (siehe `ROADMAP_ARCHIVE.md`) |
| **S2 — Datenqualität** ✅ | B4, B5, B6, B9, B12, B13 | Sichtbare Qualität der Demo-Daten; keine Strukturänderungen — abgeschlossen, verifiziert 2026-09-02 (siehe `ROADMAP_ARCHIVE.md`) |
| **S3 — LLM-Minimalismus** ✅ | A1 (`data_factory` + `static_data`), A2, A3 | Kern-Maxime; baut auf stabilem Fundament aus S1/S2 — abgeschlossen, verifiziert 2026-09-02 (siehe `ROADMAP_ARCHIVE.md`) |
| **S4 — Architektur** ✅ | D1, D2, D3, B11, B14, B15 (2026-08-03/04); B7/B8 GUI-Config-Felder + B10-Architekten-Entscheidung (2026-08-04, Folgesprint) | Callback + Logging + Batching vor weiterem Feature-Ausbau — abgeschlossen |
| **S5 — API-Versions-Schicht (R5), Tier 1** ✅ | Versions-Erkennung (`get_server_version`), `fields_get`-Warnliste (`check_field_compatibility`) (2026-08-04) | Beide ohne 🔒-Berührung, unabhängig testbar; siehe R5-Statusblock für die Tier-2-Zurückstellungs-Begründung |
| **S6 — PDF (R1/P1+P2)** ✅ | `pdf_factory`, `modules/documents`, GUI-Optionen, `RunContext.applicant_ids` (Voraussetzungs-Fix in `recruiting.py`) (2026-08-04) | Erster Roadmap-Ausbau, größter Demo-Effekt — siehe `SPRINT_LOG.md` für Peer-Review-Ergebnis und den live gefundenen `ir.attachment`-Feldnamen-Bug |
| **S7 — Prozessketten-Kontinuität (R8)** ✅ | Universelles Service-Produkt-Tagging, billable-lines-first Zeiterfassung, Wizard-basierte Fakturierung, `orchestrator.py`-Reorder 🔒 (2026-08-05) | Umnummeriert von "S7 = Purchase+Inventory" — Prozessketten-Kontinuität ist Voraussetzung, nicht parallel; siehe `ROADMAP_ARCHIVE.md`s R8-Statusblock für Details, Peer-Review-Verlauf (2× fremder Opus-Agent, Plan+Repo-Kontext) und den Hero→Universal-Kurswechsel |
| **S8 — Purchase + Inventory (R2, R3)** ✅ | `modules/purchase.py`, `modules/inventory.py` (neu), `odoo_actions.py`-Erweiterung, `orchestrator.py`-Anhang 🔒 (2026-08-28) | War ursprünglich S7; siehe `ROADMAP_ARCHIVE.md`s R2/R3-Statusblöcke für Details, zwei Peer-Review-Durchläufe (Plan-Agent + fremder Cold-Review-Agent, gleiches Verfahren wie S5-S7) und live gefundene Bugs (`ctx.company_ids`-Namenskollision, `action_create_invoice`s fehlendes `invoice_date`) |
| **S9 — Webserver-Deployment (R9)** ✅ | `web/` (FastAPI, Guards, Session, Queue, SSE), `connect_service.py`/`run_config.py` (D4), `run_journal.py` (D7), `static/` (index/app.js/app.css), Docker-Compose, `gui.py` gelöscht (2026-08-28) | Ersetzt den Aufrufer, nicht die Pipeline — `orchestrator.py` bleibt unberührt (kein `mode`-Parameter, 🔒 nicht angefasst). Siehe `ROADMAP_ARCHIVE.md`s R9-Statusblock für den gestrichenen Vorschau-Umfang, die korrigierte LLM-Invariante und die fünf live gefundenen Punkte |
| **S10 — Live-Testphase-Feedback (R10)** ✅ | Phase A (2026-08-29): `has_access`-Zugriffsproben (F6), Fehlerbericht-Entrauschung (F7) 🔒, `mrp.py`-`company_ids`-Fix (F9). Phase B (2026-08-29): DB-Name aus URL (F2), Weiter/Nav-Gate als Latch + Ansicht 03 gestrichen (F3/F5), Einstiegs-Tutorial (F1), 5 PDF-Layout-Varianten (F4) — 301/301 Unit-, 71/76 Live-Integrationsschritte grün | Feedback aus dem ersten echten Gebrauch. Beide Phasen peer-reviewed (je 1 fremder Opus-Agent, Plantext + Live-Repo, keine Konversationshistorie) vor der Umsetzung — Phase A 10 Blocker, Phase B 6 Blocker eingearbeitet. Die 5 verbleibenden Live-Fehlschläge sind durchgängig derselbe vorbestehende, unabhängige `hr.job`-Feldbug (ausgelagert). F8 (Payload-Form-Memo) zurückgestellt — 🔒-Berührung ohne belegten Nutzen, siehe `ROADMAP_ARCHIVE.md`s R10-Statusblock |
| **S11 — API-Versions-Kompatibilität (R5) + Feedback-Logs (D9)** ✅ (2026-09-02) | Phase A (kein 🔒): WP2 (Zugriffs-Ebenen komponieren), WP1 (dynamisches Feld-Manifest — fand live einen echten, unabhängigen Bug: `hr.applicant.applicant_skill_ids` gehört zu `hr_recruitment_skills`, nicht `hr_recruitment`, gefixt als Beifang), WP4 (`LAST_VERIFIED_VERSION`). Phase B (Cold-Review vor Umsetzung, S5-S10-Verfahren): WP5 (`scripts/check_compat.sh`), D9 (Lauf-Log lokal persistiert über `logging_setup.run_log_capture`, Issue trägt nur `run_id`-Referenz — archiviert, siehe `ROADMAP_ARCHIVE.md`s D9-Statusblock). **WP3 (Übersetzungs-Registry) zurückgestellt** — Review fand die 🔒-Berührung an `odoo_client.py` für den einzigen (unbelegten, nur Test-seitig gelesenen) Präzedenzfall nicht gerechtfertigt; siehe WP3-Statusblock für die vier Blocker und den Wiederaufnahme-Auslöser. | Nutzer-Vorgabe 2026-09-02: hohe Priorität, vor der bereits vorgesehenen Quick-Wins-Sprint eingeplant (die dafür zu S12 verschoben wird). Ersetzt die alte, verworfene "S5 Tier 2"-Zeile (JSON-Mapping-Dateien) komplett — siehe R5-Statusblock. R1 (PDF P3/P4) ist ebenfalls 🟠, aber unberührt von dieser Entscheidung — bewusst nicht mit reingezogen. Unit 351/351, Live-Integration 80/80 (`demo-test5.odoo.com`) grün. [PR #28](https://github.com/pahuodoo/odoo-daten-generator/pull/28) nach `main` gemerged (2026-09-02). |
| **S12 — Quick Wins** ✅ (2026-09-02) | R11 (Lost Opportunities, archiviert — `ROADMAP_ARCHIVE.md`s R11-Statusblock), R16 Produkt-Ebene (Barcode, WP1 erledigt, Location-Ebene bleibt in S13 offen — R16-Abschnitt bleibt daher hier), R19 (Expenses, archiviert — `ROADMAP_ARCHIVE.md`s R19-Statusblock) — WP3✅→WP1✅→WP2✅→WP4✅→WP5✅, siehe eigener Abschnitt "S12 — WP-Sequenz" direkt vor §5 | Drei kleine Erweiterungen, aber R19s Registrierungskette und R11s `orchestrator.py`-Einfügung stellten sich im Cold-Review als echte Blocker heraus (4 gefunden, alle eingearbeitet) — "additiv, Freigabe ist Formsache" war zu pauschal. R11s `orchestrator.py`-Einfügung (zwischen `sale`/`hr`) hat jetzt echte Architekten-Freigabe (2026-09-02), kein reiner Anhang wie bei R19/S6-S8. WP3 live verifiziert: beide offenen Verhaltensfragen (hr.expense `approval_state`, crm.lead `won_status`) brauchen nur `write()`, keine Action-Methoden. WP5s Vor-Merge-Review fand 2 weitere Blocker (genehmigte `hr.expense` blockierte `delete_run` komplett, `linked_opportunity_ids` ohne Testabdeckung auf dem echten Code-Pfad), beide live gefixt. Unit 371/371, Live-Integration 87/87 grün — WP1s/WP4s Läufe hatten zusätzlich den pre-existing, unabhängigen `ODOO_ACTIONS`-Rate-Limit-Flake (D10), inzwischen als Backlog-Item erfasst. Sprint abgeschlossen, [PR #29](https://github.com/pahuodoo/odoo-daten-generator/pull/29) nach `main` gemerged (2026-09-02) |
| **S13 — Lager-Tiefe** 🆕 | R14 (Multi-Warehouse), R15 (Lagerplätze, inkl. R16 Location-Ebene), R13 (Seriennummern-/Chargenverfolgung, MRP-Anbindung gestrichen — siehe R13) | Alle drei bauen auf `inventory.py`/`stock.*`-Modellen auf. R13 braucht R15 nicht zwingend (`stock.lot.location_id` ist optional), profitiert aber von den gleichzeitig entstehenden Sub-Locations — ein Sprint für den gesamten Lager-Realismus-Ausbau |
| **S14 — Prozess-Tiefe** 🆕 | R12 (Nachbestellregeln, in `inventory.py`), R18 (Quality Checks, Erweiterung des bestehenden `mrp.py`-Pfads) | Beide sind eher "MRP/Inventory-Investition aus S1/S8 weiter ausnutzen" als "auf S13 aufbauen" (Peer-Review-Korrektur: `quality.point` hat kein Location-Feld, "an `wh_qc_stock_loc_id` andocken" war keine reale Mechanik) — dennoch sinnvoll in einem Sprint gebündelt, da beide dieselbe operative Prozess-Ebene vertiefen |
| **S15 — Analytic Accounting (R20)** 🆕 | `account.analytic.plan`/`account.analytic.account` + `analytic_distribution`-Wiring über `sale.py`/`purchase.py`/`accounting.py`/`expenses.py` | Cross-cutting (4+ Dateien) bewusst isoliert in eigenem Sprint, damit der Review-Diff überschaubar bleibt; profitiert von R19 (Expenses, S12), falls dessen Zeilen mit-verkabelt werden sollen |
| **S16 — Multicompany (R17)** 🆕 | Architektur-Spike ✅ abgeschlossen (2026-09-04) + Minimal-Scope: zweite `res.company` (ohne Kontenplan), neues `RunContext.res_company_ids` 🔒, neues `modules/multicompany.py`, `connect_service.fetch_existing_data`-Company-Filter | Höchste Komplexität/Blast-Radius aller neuen Items — bewusst zuletzt, damit alle anderen Module (Warehouses, Quality, Analytic) schon stehen, wenn die zweite Firma befüllt wird. Beide Cold-Review-Runden gelaufen (S5-S15-Verfahren) — Runde 1: 6 Blocker + 12 Should-Fixes, Runde 2: 4 Blocker + 7 Should-Fixes, alle eingearbeitet (siehe "S16 — WP-Sequenz" in `ROADMAP.md` und R17-Abschnitt). Plan freigegeben zur Implementierung, kein dritter Durchgang nötig |

**Pro Arbeitspaket verbindlich** (aus CLAUDE.md Testing Design Patterns):
- Empty-Pool-Guards (P1) für jede neue `random.choice/sample`-Stelle
- LLM-None-Guards (P2) für jeden neuen/geänderten LLM-Pfad
- Feature-Flag-Skip (P3) für jede neue GUI-Option
- Read-Back-Validierung (P4) in jedem neuen Integrationsschritt
- 🔒-Punkte (Pipeline-Reihenfolge, JSON2-Fallbacks, Config-Schema, Cache-Namen) vor Umsetzung explizit freigeben lassen
