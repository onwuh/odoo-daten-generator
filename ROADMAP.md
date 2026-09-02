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

### R5 🟡 API-Versions-Schicht — Feld-/Methoden-Mapping pro Odoo-Release

**S5-Status (2026-08-04, siehe CLAUDE.md "Current Sprint" für Details):** Sprint in zwei
Tiers gesplittet, nach Architekten-Review eines vorab peer-reviewten Plans (fremder
Opus-Agent, Kontext nur Plan+Live-Repo). **Tier 1 — Baustein 1 (Versions-Erkennung) +
`fields_get`-Warnliste — implementiert und getestet.** **Tier 2 — Baustein 2 (Mapping-Dateien)
+ Baustein 3 (Client-Adapter, 🔒 `odoo_client.py`) — bewusst zurückgestellt**, siehe
Begründung unten. Zwei Korrekturen an diesem Abschnitt gegenüber dem Original-Stand
(2026-07-20):
1. **Kanonische Version ist 19.4, nicht 19.2** — der Live-Instanz-Stand hat sich seit
   Schreiben dieses Abschnitts geändert (siehe CLAUDE.md-Update 2026-08-03), und der Code
   schreibt bereits 19.4-Feldnamen. Jede Mapping-Datei muss Deltas relativ zu 19.4
   beschreiben, nicht relativ zu 19.2 wie unten ursprünglich skizziert.
2. **Die "realen Beispiele" in der Problem-Beschreibung unten sind teils nicht belegt:**
   `work_entry_type_id` ist laut Projekt-Memory auf 19.2 *und* 19.4 identisch (kein Delta);
   `action_confirm` vs. `button_confirm` ist eine JSON/2-API-vs.-ORM-Unterscheidung, kein
   Versions-Delta. Einzig belegter Delta zwischen 19.2 und 19.4: `hr.leave.allocation.
   allocation_type` existierte auf 19.2, wurde auf 19.4 entfernt — und brauchte keine
   Mapping-Zeile, weil der Code das Feld nie referenziert. **Tier 2 wurde deshalb
   zurückgestellt: eine Adapter-Infrastruktur ohne einen einzigen belegten Rename lässt sich
   nur gegen synthetische Test-Daten prüfen, nicht gegen echtes Odoo-Verhalten — Bausteine 2+3
   werden gebaut, sobald eine zweite reale Ziel-Version einen konkreten Rename liefert.**
   Baustein 1 + die `fields_get`-Warnliste liefern schon heute echten, live-geprüften Wert
   ohne dieses Risiko (`odoo_actions.get_server_version`, `odoo_actions.
   check_field_compatibility`, `FIELD_COMPAT_WHITELIST` — alle ohne 🔒-Berührung).
   Nebenbefund aus dem ersten Live-Lauf der neuen Warnliste: `hr.work.entry.type.
   shortcut_behavior` (`modules/hr.py:33`) existiert auf saas-19.4 nicht (74 Live-Felder
   geprüft, keine Nähe-Übereinstimmung) — bisher unbemerkt, weil der `create()`-Zweig in
   `get_or_create_annual_leave_type` nur bei komplett leerer `hr.work.entry.type`-Tabelle
   feuert. Nicht Teil von S5 behoben (Scope-Grenze) — siehe Bug-Liste.

**Problem:** Zwischen SaaS-Releases werden Felder umbenannt oder gestrichen. Heute sind
Feld-/Methodennamen hart in den Modulen codiert — jedes Release erzwingt Programmänderungen.

**Ziel:** Release-Unterschiede so weit wie sinnvoll in *nutzer­editierbare Konfiguration* verlagern — und die Grenze klar ziehen, ab der Code die richtige Antwort ist (siehe Tabelle unten).

#### Baustein 1: Versions-Erkennung (automatisch, überschreibbar) ✅ Erledigt (Tier 1)

`odoo_actions.get_server_version(client)` — Live-bestätigtes Format auf saas-19.4:
`"saas~19.4.1.3"` (Segmentanzahl nach dem Punkt variiert, Parser darf sie nicht annehmen):

```python
rec = client.search_read('ir.module.module', [['name', '=', 'base']],
                         fields=['latest_version'], limit=1)
# "saas~19.4.1.3" → normalisiert "19.4"
```

GUI: neue Statuszeile "Odoo-Version" in Screen 2 (`gui.py`), nicht-blockierend (kein Gate auf
"Weiter" bei Erkennungsfehler, analog zum bestehenden "Vorhandene Stammdaten"-Verhalten).
Auf einen manuellen Override-Dropdown wurde **bewusst verzichtet** (analog zur B10-
Architekten-Entscheidung: ein Override-Wert ohne Konsumenten — Tier 2/der Adapter, der ihn
lesen würde, ist zurückgestellt — wäre totes Gewicht).

**Zusätzlich implementiert (Baustein 3 "Ausbaustufe", vorgezogen weil unabhängig von
Bausteinen 2+3):** `odoo_actions.check_field_compatibility(client)` — `fields_get` gegen eine
kuratierte Liste von ~16 Modell/Feld-Paaren (aus echten `client.create`/`create_batch`/
`write`-Call-Sites über `modules/*.py` gezogen, priorisiert nach CLAUDE.md "Verified field
gotchas"). Nicht-fataler Log-Warnung, kein GUI-Gate. Modelle, deren übergeordnetes App nicht
installiert ist, werden still übersprungen (Installations-, kein Versions-Thema).

Screen 2 zeigt die erkannte Version als eigene Statuszeile; daneben ein Dropdown zum manuellen Überschreiben (für Edge-Fälle / Testsysteme). Unbekannte Version → nächstniedrigere bekannte verwenden + deutliche Warnung im Log.

#### Baustein 2: Mapping-Dateien `api_versions/<version>.json` (nutzereditierbar)

Der Code verwendet durchgehend *kanonische* Namen (= aktuelle Zielversion 19.2). Pro abweichender Version existiert eine JSON-Datei, die nur die **Deltas** beschreibt:

```json
{
  "version": "19.4",
  "extends": "19.2",
  "fields": {
    "hr.leave":        { "work_entry_type_id": "leave_type_id" },
    "product.product": { "is_storable": null }
  },
  "methods": {
    "sale.order": { "action_confirm": "button_confirm" }
  },
  "models": {
    "account.bank.statement": "account.statement"
  },
  "defaults": {
    "hr.job": { "payment_interval": "monthly" }
  }
}
```

Semantik: Schlüssel = kanonischer Name, Wert = Name in dieser Version, `null` = Feld existiert nicht mehr → kommentarlos weglassen. `extends` bildet eine Vererbungskette, damit 19.4 nur beschreibt, was sich seit 19.2 geändert hat. `defaults` deckt neue Pflichtfelder mit konstantem Wert ab.

Ein Nutzer kann damit ein neues Release ohne Programmierung bedienen: Datei kopieren, Renames eintragen, fertig.

#### Baustein 3: Adapter im Client (eine Stelle, Module bleiben unberührt) 🔒

`OdooJson2Client` erhält eine `schema_map`; `create`/`write`/`search_read`/`call_method` übersetzen Modell-, Feld- und Methodennamen vor dem Senden — und bei `search_read` die Feldnamen der Antwort **zurück** (Reverse-Map), damit alle Module weiter kanonisch lesen. Genau ein Übersetzungspunkt, keine Änderung in `modules/*`. 🔒 Da `odoo_client.py` betroffen ist: Architekten-Freigabe vor Umsetzung.

**Ausbaustufe (optional):** Beim Verbinden `fields_get` für die ~15 genutzten Modelle abfragen und ungemappte fehlende Felder als Warnung listen ("Feld `X` auf `Y` existiert im Zielsystem nicht und ist nicht gemappt — Eintrag in `api_versions/<version>.json` ergänzen"). Damit sieht der Nutzer *vor* dem Lauf exakt, was er in die JSON eintragen muss — die Mapping-Datei schreibt sich quasi selbst.

#### Grenze: Was geht per Config — was braucht Code

| Änderungstyp im Release | JSON reicht | Begründung |
|---|:---:|---|
| Feld umbenannt (1:1) | ✅ | reine String-Substitution |
| Feld ersatzlos gestrichen | ✅ (`null`) | Weglassen genügt |
| Neues Pflichtfeld mit konstantem Default | ✅ (`defaults`) | statischer Wert |
| Methode umbenannt | ✅ | String-Substitution |
| Modell umbenannt | ✅ | String-Substitution |
| Selection-Wert umbenannt (1:1) | ⚠️ (`values`-Map, falls nötig) | noch Substitution — aber bereits Grenzfall |
| Feld aufgeteilt / Format geändert (z. B. Datetime-Konvention) | ❌ Code | braucht Werttransformation |
| Char-Feld wurde Many2one (Wert muss per Lookup ermittelt werden) | ❌ Code | braucht zusätzlichen API-Call |
| Workflow geändert (z. B. zwei Bestätigungsschritte statt einem) | ❌ Code | braucht Ablauflogik |
| Modell aufgespalten / zusammengelegt | ❌ Code | Strukturänderung |

**Faustregeln (bewusst konservativ):**
1. Config-Customizing endet exakt dort, wo mehr als *String-Ersetzung + statische Defaults* nötig ist. Alles mit Logik (Transformation, Lookup, Bedingung, Mehrschrittigkeit) wird als `VersionAdapter`-Klasse programmiert (`api_versions/adapter_19_4.py` mit Hook `prepare_vals(model, vals)` / `prepare_call(model, method, ...)`) und mit einem Programm-Update ausgeliefert.
2. Braucht eine Versions-JSON mehr als ~20 Einträge oder häufen sich ⚠️-Grenzfälle, ist das ebenfalls das Signal für Code statt Config — die Datei ist dann kein Mapping mehr, sondern ein verstecktes Programm.
3. **Kein** Plugin-System, **keine** Ausdrucks-DSL in der JSON (keine Bedingungen, keine Templates). Das wäre die übertrieben komplizierte Lösung: schwer testbar, vom Nutzer kaum debugbar, und spart gegenüber einer kleinen Adapter-Klasse nichts.

**Tests:** Unit — Mapping-Auflösung (extends-Kette, `null`-Drop, Reverse-Mapping bei `search_read`); Integration — ein Lauf durch den Adapter mit Identitäts-Mapping (19.2→19.2) muss byte-gleiche Payloads erzeugen wie heute (Regressionsschutz).

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

### R11 🆕 Geplant (S11) — Lost Opportunities (CRM)

**Warum:** `modules/crm.py` erzeugt aktuell nur aktive/gewonnene Opportunities — kein
Trichter-Realismus (jede Demo-Pipeline sieht 100% erfolgreich aus).

**Live bestätigt (`crm.lead`, saas-19.4):** `active` (bool), `probability` (float),
`won_status` (readonly, selection `won`/`lost`/`pending`, computed), `lost_reason_id`
(m2n → `crm.lost.reason`).

**Peer-Review-Korrektur — Sequenzierungs-Blocker:** die naheliegende Umsetzung (lost-Anteil
direkt am Ende von `create_crm_data` markieren) ist falsch. `sale.py:78-96` verknüpft
Aufträge über `search_read('crm.lead', ...)`, was `active=False`-Leads unsichtbar macht,
und `sale.py:126-143` schreibt danach die Won-Stage auf verknüpfte Leads — CRM läuft vor
Sales in der Pipeline (Position 3 vor 4), ein in `crm.py` bereits auf verloren gesetzter
Lead würde also entweder fälschlich doch verknüpft (wenn die Lost-Markierung erst NACH dem
Sales-Schritt passiert) oder von `sale.py` schlicht ignoriert (wenn davor) — im schlimmsten
Fall verloren *und* Won-staged gleichzeitig. Fix: Lost-Pass als eigener, später Aufruf aus
`orchestrator.py` **nach** `sale.py`, operiert ausschließlich auf den von `sale.py` **nicht**
verknüpften Lead-IDs (Abgleich der in `crm.py` erzeugten Lead-IDs gegen die von `sale.py`
verknüpften — ggf. muss `ctx` dafür die crm.py-Lead-IDs zusätzlich additiv exponieren,
exakt gegen `sale.py:78-96`/`126-143` verifizieren vor Umsetzung).

**Dev Tasks:**
- Neuer, eigener Aufruf-Schritt nach `sale.py` (nicht Teil von `create_crm_data` selbst,
  siehe Sequenzierungs-Korrektur oben): konfigurierbarer Anteil per
  `client.write('crm.lead', [id], {...})` auf verloren setzen.
- `crm.lost.reason` **suchen, nie erzeugen** — Referenzdaten-Konvention, echtes Vorbild ist
  `mrp.py:376-379` (`quality.alert.team`/`quality.point.test_type` werden gesucht, bei
  leerem Ergebnis wird das Feature komplett übersprungen, nichts wird nachgelegt). **Nicht**
  `hr.skill.level` als Vorbild zitieren — das wird von `recruiting.py:61`
  (`create_skill_level`) tatsächlich erzeugt, ist also kein Search-only-Beispiel. Leerer
  `crm.lost.reason`-Pool → Pattern 1 (Lead bleibt aktiv, Warnung loggen, kein Crash).
- ⚠️ Unverifiziert: ob `probability`/`won_status` beim reinen `write()` serverseitig
  korrekt nachgezogen werden, oder ob Odoos UI-Aktion `action_set_lost` (falls per JSON2
  aufrufbar) nötig ist. Vor Umsetzung live gegen `demo-pahu-test1` prüfen — nicht
  spekulativ `action_set_lost` übernehmen (Lehre aus der `/call`+`/call_kw`-Altlast in
  CLAUDE.md: nur belegte Methodennamen).
- `config.py` 🔒 additiv: **eigenes** neues Feld, z. B. `crm_lost: dict = {"pct": int}` —
  **nicht** `ModuleSelections.crm` (das ist `int = 0`, kein Dict, `crm.get(...)` würde
  crashen) und **nicht** `crm_chatter` (das wird nur befüllt, wenn Chatter aktiv ist, also
  kein zuverlässiger Träger für ein unabhängiges Feature).

**Tests:** Pattern 1 (leerer `crm.lost.reason`-Pool), Pattern 3 (`pct=0` → keine
zusätzlichen `write`-Calls), Pattern 7 (Verteilung über 200 Samples: sowohl aktive als
auch verlorene Leads vorhanden).

**Komplexität:** Niedrig-Mittel (die Sequenzierungs-Korrektur macht es weniger trivial als
zunächst geschätzt) · **Benefit:** Mittel

### R12 🆕 Geplant (S13) — Nachbestellregeln / Replenishment-Planung

**Annahme zur Bezeichnung "MRP Planung":** interpretiert als Odoos Nachbestellregeln
(`stock.warehouse.orderpoint`, im deutschen Inventory-Menü unter "Planung"/"Nachbestellung"),
**nicht** MPS (Master Production Schedule, separates Enterprise-Widget ohne eigenes
Kern-Modell in dieser Feldliste). Falls anders gemeint: vor S13-Start korrigieren.

**Live bestätigt (`stock.warehouse.orderpoint`, saas-19.4):** vollständiges Feldschema
gezogen — u. a. `warehouse_id`/`location_id`/`product_id` (alle `required`),
`product_min_qty`/`product_max_qty` (`required`, float), `trigger` (`auto`/`manual`),
`route_id`, `company_id` (`required`).

**Dev Tasks:**
- **In `inventory.py` einziehen, kein neues Modul** (siehe Implementierungshinweis oben) —
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

### R13 🆕 Geplant (S12) — Seriennummern-/Chargenverfolgung

**Live bestätigt (`stock.lot`, saas-19.4):** vollständiges Feldschema — `product_id`
(`required`), `name` (`required`, "Lot/Serial Number"), `company_id`, `location_id`.
`stock.quant` (S8, bereits im Code) hat laut vorherigem Sprint bereits einen `lot_id`-fähigen
Schreibpfad (nicht explizit genutzt bisher).

**Dev Tasks:**
- `master_data.py` (oder `mrp.py` für Fertigprodukte): `product.template.tracking` auf
  einen konfigurierbaren Anteil der Storables setzen (`'lot'`/`'serial'`/`'none'`) —
  Feldname vor Umsetzung gegen `product.template` (nicht `product.product`) verifizieren.
- `inventory.py`: für **`tracking='lot'`**-Produkte vor/bei `stock.quant`-Erzeugung
  passenden `stock.lot` anlegen (`product_id`, `name` z. B. fortlaufend `LOT-0001`,
  `company_id`) und `lot_id` auf den Quant setzen. Kein Move-basierter Umbau nötig —
  Quant-Ansatz aus S8 bleibt, nur um `lot_id` ergänzt.
- **`tracking='serial'` NICHT über denselben Pfad** (Peer-Review-Korrektur): der
  bestehende Quant-Code (`inventory.py:62-70`) schreibt einen Quant mit Menge 5–15 pro
  Produkt — für Serial-Tracking ist eine Menge > 1 pro Lot ungültig (jede Seriennummer ist
  genau 1 Stück). Serial-getrackte Produkte brauchen eine eigene Schleife: N einzelne
  Quants (Menge je 1) mit je eigenem `stock.lot`, statt eines Bulk-Quants.
- **MRP-Anbindung (`lot_producing_id` bei Fertigungsauftrag-Abschluss) aus dem Scope
  gestrichen** (Peer-Review-Korrektur): `modules/mrp.py` hat aktuell **keinen**
  Abschluss-Call für Fertigungsaufträge — Produktionen werden nur `action_confirm`t, nie
  fertiggemeldet (kein `button_mark_done`/`mark_done` im gesamten `modules/`-Baum). Ohne
  einen Abschlussschritt gibt es keinen Ort, an dem `lot_producing_id` gesetzt würde — R13
  bleibt bei einer echten MRP-Fertigmeldung als Voraussetzung offen (eigenes künftiges
  Item, nicht Teil von S12).
- `purchase.py`: bewusst **nicht** in S12-Scope — Wareneingang läuft dort weiterhin ohne
  Lot-Zuordnung (Move-basierter Pfad, größerer Umbau, kein Demo-Mehrwert ggü. dem
  Quant-Ansatz für den ersten Wurf).

**Tests:** Pattern 1 (keine getrackten Produkte → normaler Quant-Pfad wie bisher, kein
Crash), Pattern 4 (Read-back `stock.lot.product_id`/`name`), Pattern 6 (m2o-Tupel bei
`lot_id`-Read-back).

**Komplexität:** Mittel · **Benefit:** Mittel

### R14 🆕 Geplant (S12) — Multi-Warehouse

**Live bestätigt (`stock.warehouse`, saas-19.4):** vollständiges Feldschema — `name`/`code`
(`required`), `view_location_id`/`lot_stock_id` (`required`, m2o → `stock.location`),
`reception_steps`/`delivery_steps`/`manufacture_steps` (Selections für 1-3-stufige Routen),
`resupply_wh_ids` (m2m, für automatische Resupply-Routen zwischen Lagern).

**Dev Tasks:**
- `inventory.py`: optional ein zweites `stock.warehouse` anlegen (`name`, `code` z. B.
  "WH2"). ⚠️ Unverifiziert, ob `view_location_id`/`lot_stock_id` beim `create()` trotz
  `required: true` serverseitig automatisch befüllt werden (Odoo-Core-Verhalten bei realen
  Warehouses) oder explizit mitgegeben werden müssen — kurzer Live-Spike vor Umsetzung.
- `purchase.py`/`inventory.py`: konfigurierbarer Anteil der Wareneingänge/Quants aufs
  zweite Lager statt immer `get_default_warehouse`.
- `ModuleSelections.stock` (Dict, bereits vorhanden) additiv um `"second_warehouse": bool`
  erweitern.

**Tests:** Pattern 3 (Flag aus → nur ein Warehouse wie bisher, keine zusätzlichen Calls),
Pattern 4 (Read-back zweites Warehouse `code`/`lot_stock_id`).

**Komplexität:** Mittel · **Benefit:** Mittel

### R15 🆕 Geplant (S12) — Lagerplätze (Sub-Locations, Putaway)

**Live bestätigt (`stock.location`, saas-19.4):** vollständiges Feldschema — `usage`
(Selection: `supplier`/`view`/`internal`/`customer`/`inventory`/`production`/`transit`),
`location_id` (Parent), `barcode` (char, R16-Anknüpfung), `putaway_rule_ids`,
`storage_category_id`.

**Dev Tasks:**
- `inventory.py`: mehrere `stock.location`-Kindknoten unter `warehouse["stock_location_id"]`
  anlegen (`usage='internal'`, z. B. "Regal A/Fach 1" … "Regal A/Fach 3"), `barcode` setzen
  (R16).
- Bestehende `stock.quant`-Erzeugung (S8) auf diese Sub-Locations verteilen statt immer
  auf die Warehouse-Root-Location.
- Optional/Stretch: 1-2 `stock.putaway.rule`-Demo-Records (Kategorie → Ziel-Sub-Location) —
  nicht S12-Pflicht.

**Tests:** Pattern 1 (kein Warehouse → skip, bereits vorhandener Guard erweitern),
Pattern 4 (Read-back `complete_name`/`location_id`-Hierarchie).

**Komplexität:** Mittel · **Benefit:** Mittel-Hoch

### R16 🆕 Geplant (S11 Produkt-Ebene, S12 Location-Ebene) — Barcode

**Live bestätigt:** `product.product.barcode` (char) **und** `stock.location.barcode`
(char) existieren beide auf saas-19.4.

**Dev Tasks:**
- Reine Python-Hilfsfunktion (`data_factory.py`, kein Odoo-/Netzwerk-Call, analog
  `pdf_factory.py`-Philosophie): valide EAN-13 generieren (12 Zufallsziffern + Prüfziffer
  nach Standardalgorithmus).
- **Kollisionsvermeidung über Läufe hinweg, nicht nur lauf-lokal** (Peer-Review-Korrektur):
  `product.product.barcode` ist in Odoo eindeutig, und `odoo_client.create_batch` fällt bei
  einem Fehler nur pro Einzelsatz auf 404/422 zurück (`odoo_client.py:408-421`) — ein
  einziges Duplikat lässt sonst den **gesamten** Produkt-Batch scheitern. Ein rein
  lauf-lokaler Zähler/Präfix reicht nicht (zweiter Lauf gegen dieselbe Demo-DB kollidiert).
  Präfix aus `run_id` ableiten **oder** bestehende `product.product.barcode`-Werte einmal
  zu Laufbeginn lesen und dagegen deduplizieren.
- `master_data.py`: `barcode` bei Produkterzeugung mitschreiben (S11).
- `inventory.py`: `barcode` bei den R15-Sub-Locations mitschreiben (S12, da Locations
  vorher nicht existieren).

**Tests:** reiner Unit-Test ohne Odoo-Mock — EAN-13-Prüfziffer-Validierung auf N generierten
Werten.

**Komplexität:** Niedrig · **Benefit:** Niedrig-Mittel (zahlt sich nur aus, wenn later
Odoos eigene Barcode-App/ein Scanner gegen die Demo-DB verwendet wird)

### R17 🆕 Geplant (S15, Architektur-Spike zuerst) — Multicompany

**Live bestätigt:** `res.company.parent_id`/`child_ids` (Firmenhierarchie),
`res.users.company_id`/`company_ids` (Default- + erlaubte Firmen) — Standard-Odoo-Multi-
Company-Modell ist auf dieser Instanz vorhanden und nutzbar.

**⚠️ Warum eigener Architektur-Spike vor Code (🔒-adjacent):** `RunContext.company_ids`
heißt trotz seines Namens **niemals** `res.company`, sondern hält `res.partner`-IDs
(Kunden-/Firmenkontakte aus `master_data.py`) — diese Verwechslung hat bereits einen
echten, monatelang unbemerkten Bug in `mrp.py` verursacht (S8, gefixt in S10). Eine zweite
echte Firma vervielfacht die Gelegenheiten für exakt diese Fehlerklasse über praktisch
jedes Modul hinweg (`sale.py`, `purchase.py`, `inventory.py`, `mrp.py` nutzen `company_id`
an vielen Stellen).

**Empfohlener Minimal-Scope für den ersten Wurf (bewusst klein, nicht die volle
Pipeline verdoppeln):**
- **Nicht** den kompletten 8-Module-Durchlauf pro Firma wiederholen — bei
  ~1 req/s Live-Rate-Limit (siehe CLAUDE.md) und bereits heute mehrstufigen Läufen wäre das
  ein Laufzeit- und Fehlerbudget-Vielfaches ohne klar proportionalen Demo-Mehrwert.
- Stattdessen: **eine** zusätzliche `res.company` anlegen. **Peer-Review-Korrektur:**
  ihr NUR frisch für sie selbst erzeugte Records zuweisen (neue Partner/Produkte/ein neues
  Warehouse aus R14) — **nicht** bereits bestehende, von Company-1-Belegen referenzierte
  Partner/Produkte per `company_id`-Write umhängen (Odoo verweigert das bei referenzierten
  Records ohnehin). Zusätzlich zu klären: eine neue `res.company` hat zunächst **keinen**
  Kontenplan — ob/wie ein Chart-of-Accounts-Setup für sie per JSON2 auslösbar ist, ist Teil
  des Architektur-Spikes, nicht selbstverständlich gegeben.
- **Vor Umsetzung zwingend:** `RunContext.company_ids` entweder umbenennen oder ein neues,
  eindeutig benanntes Feld (`RunContext.res_company_ids`) einführen, um die bestehende
  Verwechslungsgefahr nicht in einer zweiten echten Firma zu verschärfen — das ist eine
  Config-Schema-Änderung 🔒, braucht Architekten-Freigabe nach demselben Verfahren wie
  jede andere 🔒-Stelle.

**Prozess:** wie S5–S10 — Plan zuerst als eigenständiges Dokument, zweimal peer-reviewed
(unabhängiger Opus-Agent, nur Plantext + Live-Repo, keine Konversationshistorie) **vor**
dem ersten Codezeilen.

**Komplexität:** Hoch · **Benefit:** Hoch (langfristig), aber bewusst klein für den
Minimal-Scope des ersten Wurfs — der volle Nutzen hängt vom tatsächlich freigegebenen
Umfang ab, nicht von dieser Schätzung allein.

### R18 🆕 Geplant (S13) — Quality Checks

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
  eigene GUI-Karte), dann **alle fünf** Registrierungspunkte aus dem Implementierungshinweis
  oben beachten — nicht nur `orchestrator.py`/`config.py`.

**Tests:** Pattern 1 (kein `test_type_id`/Team → skip, bereits vorhanden), Pattern 3 (Flag
aus → keine Calls, bereits vorhanden), Pattern 7 (Pass/Fail-Verteilung, neu), plus ein
Regressionstest, der `create_quality_points=True` tatsächlich laufen lässt (bisher deckt
`test_mrp.py` das mit `False` ab und hätte den `test_report_type`-Bug nie gefangen).

**Komplexität:** Mittel (kleiner als ursprünglich geschätzt — Fundament existiert bereits)
· **Benefit:** Mittel-Hoch (nutzt die MRP-Investition aus S1 weiter aus, guter visueller
Payoff im Quality-App-Dashboard)

### R19 🆕 Geplant (S11) — Expenses

**Live bestätigt (`hr.expense`, saas-19.4):** `state` (Selection:
`draft`/`submitted`/`approved`/`posted`/`in_payment`/`paid`/`refused` — **kein**
separates `hr.expense.sheet`-Modell mehr auf dieser Version, Status läuft direkt auf
`hr.expense`), aber **`state` selbst ist readonly** — der schreibbare Parallel-Workflow
läuft über `approval_state` (Selection `submitted`/`approved`/`refused`). `employee_id`
(`required`), `analytic_distribution` (json, R20-Anknüpfung), `payment_mode`
(`own_account`/`company_account`/`payslip_account`), `total_amount`.

**Dev Tasks:**
- Neue Datei `modules/expenses.py` — `create_expense_data(client, gemini, ctx)`.
- Ausgabenkategorien (`product.product` mit `can_be_expensed=True`) **suchen, nie
  erzeugen** — Odoo liefert Standardkategorien vorinstalliert; leerer Pool → Pattern 1.
- Kein LLM-Call nötig (LLM-Minimalismus-Prinzip: Beschreibung per Template
  `f"{kategorie} – {ort}"`, keine kreative Textgenerierung erforderlich) — hält das Modul
  praktisch kostenlos.
- Workflow für einen konfigurierbaren genehmigten Anteil — **`approval_state`
  schreiben, nicht `state`** (`state` ist readonly, siehe oben). ⚠️ Unverifiziert, ob
  direktes `write(approval_state=...)` akzeptiert wird oder ob
  `action_submit`/`action_approve`-Methoden nötig sind — **immer erst den aktuellen Status
  lesen**, nie blind transitionieren (gleiche Fallenklasse wie `hr.leave/action_approve`,
  siehe CLAUDE.md).
- `config.py`: additiv `ModuleSelections.expenses: dict = {"count_per_employee": int,
  "approved_pct": int}` (Dict-Form, nicht Skalar — siehe S8-Lehre bei `stock`).
- `orchestrator.py`: additiver Anhang ans Ende von `module_order`.

**Tests:** Pattern 1 (keine Expense-Kategorien → skip), Pattern 3 (Flag aus → keine
Calls), Pattern 5 (keine Mitarbeiter → skip), Pattern 7 (Approved-Anteil-Verteilung).

**Komplexität:** Niedrig-Mittel · **Benefit:** Hoch

### R20 🆕 Geplant (S14) — Analytic Accounting

**Live bestätigt:** `account.analytic.plan` (Hierarchie via `parent_id`/`children_ids`),
`account.analytic.account` (`plan_id` `required`), `analytic_distribution` (json-Feld,
Format vermutlich `{"<analytic_account_id>": <prozent>}`) bereits bestätigt auf
`account.analytic.line` **und** `hr.expense` — auf `sale.order.line`/`purchase.order.
line`/`account.move.line` mit hoher Wahrscheinlichkeit identisch (Odoo-weit
vereinheitlichtes Muster seit Version 17), aber **vor Umsetzung live gegenprüfen, nicht
annehmen**.

**Dev Tasks:**
- Bestehenden Default-Plan **suchen** (`account.analytic.line.account_id`-Domain zeigt
  `plan_id child_of 1` — vermutlich existiert bereits ein Default-Plan id=1 für die
  Projekt-Zeiterfassung aus S7/R8) statt blind einen neuen anzulegen.
- Eine Handvoll `account.analytic.account`-Kostenstellen anlegen (z. B. "Vertrieb",
  "Produktion", "Verwaltung") — optional 1:1 an bestehende `hr.department`-Records aus
  `hr.py` gekoppelt.
- `analytic_distribution` auf einem konfigurierbaren Anteil von Sale-Order-**Zeilen**
  setzen, **bevor** `accounting.py` läuft (Pipeline-Position 4 vs. 8, siehe
  Objekt-Pipeline oben) — Rechnungszeilen übernehmen die Distribution serverseitig vom
  verknüpften `sale.order.line` über den bestehenden `sale.advance.payment.inv`-Wizard
  (S7/R8). **Peer-Review-Korrektur:** `analytic_distribution` lässt sich auf bereits
  **gebuchten** `account.move.line`s nicht mehr schreiben — `accounting.post_invoices`
  bucht sie (Position 8); ein direkter Nachtrag auf Invoice-Zeilen nach dem Post-Schritt
  ist also kein gangbarer Fallback, es muss vorher über die SO-Zeile laufen.
- `analytic_distribution` zusätzlich auf einem Anteil der Purchase-Order-Zeilen und (falls
  R19 bereits gelandet) Expense-Zeilen setzen — beide unabhängig vom Sale/Invoice-Pfad.
- **Explizit NICHT anfassen:** `project.py`s bestehende Timesheet-Analytic-Anbindung
  (S7/R8). **Peer-Review-Korrektur zur Begründung:** `project.py:210-244` setzt beim
  Anlegen von `account.analytic.line`-Timesheet-Einträgen nur `project_id`/`task_id`/
  `so_line` — **nicht** `account_id` selbst (Odoo leitet die Analytic-Account serverseitig
  aus dem Projekt ab). Der Grund, das nicht anzufassen, ist also nicht "wäre bereits
  korrekt gesetzt", sondern: ein zusätzlicher expliziter `account_id`-Write würde die
  bestehende, funktionierende serverseitige Ableitung überschreiben/duplizieren, ohne
  Mehrwert — dieses Item bleibt additiv auf andere Belegarten beschränkt.

**Tests:** Pattern 3 (Flag aus → keine `analytic_distribution`-Writes), Pattern 4
(Read-back `analytic_distribution`-JSON-Struktur pro Belegzeile).

**Komplexität:** Hoch (4+ Dateien: `sale.py`, `purchase.py`, `accounting.py`,
`expenses.py`) · **Benefit:** Mittel-Hoch

---

## 5. Umsetzungsreihenfolge

Jedes Paket endet mit grüner `test_suite.py` gegen die Live-Instanz (CLAUDE.md-Pflicht). Empfohlene Sprints:

| Sprint | Inhalt | Begründung |
|---|---|---|
| **S1 — Bugfixes kritisch** ✅ | B1, B2, B3 (+ B16 als Beifang) | Kleine, isolierte Fixes; B1 schaltet verlorene Features frei — abgeschlossen, verifiziert 2026-09-02 (siehe `ROADMAP_ARCHIVE.md`) |
| **S2 — Datenqualität** ✅ | B4, B5, B6, B9, B12, B13 | Sichtbare Qualität der Demo-Daten; keine Strukturänderungen — abgeschlossen, verifiziert 2026-09-02 (siehe `ROADMAP_ARCHIVE.md`) |
| **S3 — LLM-Minimalismus** ✅ | A1 (`data_factory` + `static_data`), A2, A3 | Kern-Maxime; baut auf stabilem Fundament aus S1/S2 — abgeschlossen, verifiziert 2026-09-02 (siehe `ROADMAP_ARCHIVE.md`) |
| **S4 — Architektur** ✅ | D1, D2, D3, B11, B14, B15 (2026-08-03/04); B7/B8 GUI-Config-Felder + B10-Architekten-Entscheidung (2026-08-04, Folgesprint) | Callback + Logging + Batching vor weiterem Feature-Ausbau — abgeschlossen |
| **S5 — API-Versions-Schicht (R5), Tier 1** ✅ | Versions-Erkennung (`get_server_version`), `fields_get`-Warnliste (`check_field_compatibility`) (2026-08-04) | Beide ohne 🔒-Berührung, unabhängig testbar; siehe R5-Statusblock für die Tier-2-Zurückstellungs-Begründung |
| S5 Tier 2 (zurückgestellt) | `api_versions/*.json`, Client-Adapter (🔒) | Erst mit einem echten, belegten Rename zwischen zwei Live-Versionen — siehe R5 |
| **S6 — PDF (R1/P1+P2)** ✅ | `pdf_factory`, `modules/documents`, GUI-Optionen, `RunContext.applicant_ids` (Voraussetzungs-Fix in `recruiting.py`) (2026-08-04) | Erster Roadmap-Ausbau, größter Demo-Effekt — siehe `SPRINT_LOG.md` für Peer-Review-Ergebnis und den live gefundenen `ir.attachment`-Feldnamen-Bug |
| **S7 — Prozessketten-Kontinuität (R8)** ✅ | Universelles Service-Produkt-Tagging, billable-lines-first Zeiterfassung, Wizard-basierte Fakturierung, `orchestrator.py`-Reorder 🔒 (2026-08-05) | Umnummeriert von "S7 = Purchase+Inventory" — Prozessketten-Kontinuität ist Voraussetzung, nicht parallel; siehe `ROADMAP_ARCHIVE.md`s R8-Statusblock für Details, Peer-Review-Verlauf (2× fremder Opus-Agent, Plan+Repo-Kontext) und den Hero→Universal-Kurswechsel |
| **S8 — Purchase + Inventory (R2, R3)** ✅ | `modules/purchase.py`, `modules/inventory.py` (neu), `odoo_actions.py`-Erweiterung, `orchestrator.py`-Anhang 🔒 (2026-08-28) | War ursprünglich S7; siehe `ROADMAP_ARCHIVE.md`s R2/R3-Statusblöcke für Details, zwei Peer-Review-Durchläufe (Plan-Agent + fremder Cold-Review-Agent, gleiches Verfahren wie S5-S7) und live gefundene Bugs (`ctx.company_ids`-Namenskollision, `action_create_invoice`s fehlendes `invoice_date`) |
| **S9 — Webserver-Deployment (R9)** ✅ | `web/` (FastAPI, Guards, Session, Queue, SSE), `connect_service.py`/`run_config.py` (D4), `run_journal.py` (D7), `static/` (index/app.js/app.css), Docker-Compose, `gui.py` gelöscht (2026-08-28) | Ersetzt den Aufrufer, nicht die Pipeline — `orchestrator.py` bleibt unberührt (kein `mode`-Parameter, 🔒 nicht angefasst). Siehe `ROADMAP_ARCHIVE.md`s R9-Statusblock für den gestrichenen Vorschau-Umfang, die korrigierte LLM-Invariante und die fünf live gefundenen Punkte |
| **S10 — Live-Testphase-Feedback (R10)** ✅ | Phase A (2026-08-29): `has_access`-Zugriffsproben (F6), Fehlerbericht-Entrauschung (F7) 🔒, `mrp.py`-`company_ids`-Fix (F9). Phase B (2026-08-29): DB-Name aus URL (F2), Weiter/Nav-Gate als Latch + Ansicht 03 gestrichen (F3/F5), Einstiegs-Tutorial (F1), 5 PDF-Layout-Varianten (F4) — 301/301 Unit-, 71/76 Live-Integrationsschritte grün | Feedback aus dem ersten echten Gebrauch. Beide Phasen peer-reviewed (je 1 fremder Opus-Agent, Plantext + Live-Repo, keine Konversationshistorie) vor der Umsetzung — Phase A 10 Blocker, Phase B 6 Blocker eingearbeitet. Die 5 verbleibenden Live-Fehlschläge sind durchgängig derselbe vorbestehende, unabhängige `hr.job`-Feldbug (ausgelagert). F8 (Payload-Form-Memo) zurückgestellt — 🔒-Berührung ohne belegten Nutzen, siehe `ROADMAP_ARCHIVE.md`s R10-Statusblock |
| **S11 — Quick Wins** 🆕 | R11 (Lost Opportunities), R16 Produkt-Ebene (Barcode), R19 (Expenses) | Drei kleine, in sich unabhängige Erweiterungen — guter Einstiegssprint nach S10. R11 und R19 sind additiv 🔒 (`config.py`-Felder, R19 zusätzlich ein `orchestrator.py`-Anhang) — gleiches Muster wie jeder bisherige Sprintanhang seit S6, Freigabe ist Formsache, kein Blocker (Peer-Review-Korrektur: "ohne 🔒-Berührung" war zu pauschal formuliert) |
| **S12 — Lager-Tiefe** 🆕 | R14 (Multi-Warehouse), R15 (Lagerplätze, inkl. R16 Location-Ebene), R13 (Seriennummern-/Chargenverfolgung, MRP-Anbindung gestrichen — siehe R13) | Alle drei bauen auf `inventory.py`/`stock.*`-Modellen auf. R13 braucht R15 nicht zwingend (`stock.lot.location_id` ist optional), profitiert aber von den gleichzeitig entstehenden Sub-Locations — ein Sprint für den gesamten Lager-Realismus-Ausbau |
| **S13 — Prozess-Tiefe** 🆕 | R12 (Nachbestellregeln, in `inventory.py`), R18 (Quality Checks, Erweiterung des bestehenden `mrp.py`-Pfads) | Beide sind eher "MRP/Inventory-Investition aus S1/S8 weiter ausnutzen" als "auf S12 aufbauen" (Peer-Review-Korrektur: `quality.point` hat kein Location-Feld, "an `wh_qc_stock_loc_id` andocken" war keine reale Mechanik) — dennoch sinnvoll in einem Sprint gebündelt, da beide dieselbe operative Prozess-Ebene vertiefen |
| **S14 — Analytic Accounting (R20)** 🆕 | `account.analytic.plan`/`account.analytic.account` + `analytic_distribution`-Wiring über `sale.py`/`purchase.py`/`accounting.py`/`expenses.py` | Cross-cutting (4+ Dateien) bewusst isoliert in eigenem Sprint, damit der Review-Diff überschaubar bleibt; profitiert von R19 (Expenses, S11), falls dessen Zeilen mit-verkabelt werden sollen |
| **S15 — Multicompany (R17)** 🆕 | Architektur-Spike (Pflicht vor Code) + Minimal-Scope: zweite `res.company`, `RunContext.company_ids`-Namenskonflikt auflösen 🔒 | Höchste Komplexität/Blast-Radius aller neuen Items — bewusst zuletzt, damit alle anderen Module (Warehouses, Quality, Analytic) schon stehen, wenn die zweite Firma befüllt wird. Eigener Architekten-Freigabe-Schritt vor S15-Start, gleiches Zwei-Pass-Peer-Review-Verfahren wie S5-S10 |

**Pro Arbeitspaket verbindlich** (aus CLAUDE.md Testing Design Patterns):
- Empty-Pool-Guards (P1) für jede neue `random.choice/sample`-Stelle
- LLM-None-Guards (P2) für jeden neuen/geänderten LLM-Pfad
- Feature-Flag-Skip (P3) für jede neue GUI-Option
- Read-Back-Validierung (P4) in jedem neuen Integrationsschritt
- 🔒-Punkte (Pipeline-Reihenfolge, JSON2-Fallbacks, Config-Schema, Cache-Namen) vor Umsetzung explizit freigeben lassen
