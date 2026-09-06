# Roadmap — odoo-daten-generator

**Stand:** 2026-07-20 · **Basis:** Code-Review aller Kernmodule (gui.py, orchestrator.py, llm_service.py, odoo_client.py, modules/*, tests/*)

**Umbenannt von `IMPLEMENTIERUNGSPLAN.md`, 2026-08-29:** Inhalt und Historie unverändert — der Name passt seit S1–S10 (10 abgeschlossene Sprints) besser als "Plan". Gleichzeitig um R11–R20 erweitert (Erweiterungen an bestehenden Modulen + vier neue App-Domänen), Peer-reviewed nach dem etablierten S5-S10-Verfahren (fremder Opus-Agent, Plan-Text + Live-Repo, keine Konversationshistorie) vor dem Commit.

Dieses Dokument ist der Implementierungsrahmen für die nächsten Entwicklungszyklen. Es gliedert sich in:

1. [Leitprinzip: LLM-Minimalismus](#1-leitprinzip-llm-minimalismus)
2. ~~Bugs & Logikfehler~~ — vollständig abgearbeitet, steht in [`ROADMAP_ARCHIVE.md`](ROADMAP_ARCHIVE.md) §2
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

**Nummernvergabe:** neue Punkte laufen ab **D16** weiter. D11–D15 sind übersprungen, weil der S16-Architektur-Spike (heute in [`ROADMAP_ARCHIVE.md`](ROADMAP_ARCHIVE.md) §5) seine sprint-internen Entscheidungen ebenfalls D1–D15 nennt — dieselben Kennungen, andere Bedeutung (§3-D8 = "Kleinigkeiten", S16-D8 = "Bestehende-Firma-Wiederverwendung"). Wie die Kollision aufgelöst wurde: D19. Ab S17 gilt die Präfix-Pflicht `S<N>-D<n>` aus CLAUDE.mds Planning-document rule 2.

**Was hier bewusst NICHT steht** (2026-09-05 nachgemessen, damit es niemand "repariert"): der Import-Graph ist ein sauberer Stern — **kein Modul importiert ein anderes Modul**, alle 12 `modules/*.py` hängen nur an `{config, odoo_actions, data_factory, fallback_data, pdf_factory}`, keine Zyklen, Pipeline-Reihenfolge an genau einer Stelle (`orchestrator.py:74`). Und `RunContext` ist kein Gott-Objekt: 23 Schreibstellen, 18 davon mit genau einem Besitzer (die 5 Ausnahmen siehe D8). Beides ist tragfähig.

### D8 ✅ Kleinigkeiten — abgeschlossen (S18) → [`ROADMAP_ARCHIVE.md`](ROADMAP_ARCHIVE.md)

Alle acht Teilpunkte sind erledigt oder gemessen verworfen. Statusblock im Archiv.

### D21 🟠 `field_manifest.json`-Drift wird nirgends erzwungen — Manifest war vier Sprints blind

**Neu 2026-09-06, live gefunden beim S17-Abschluss.** Das Manifest existiert laut R5/WP1 zur
Drift-Erkennung („optional per CI gegen den committed Stand geprüft", §4/R5/WP1). Der
Vergleich wurde nie gebaut — und die Datei wurde nach ihrer Entstehung **genau einmal**
geschrieben: `6ac7f9c` (S11/WP1), ihr einziger Commit bis S17.

Der erste Capture-Lauf danach (S17, `12682cc`) brachte **71 Zusätze, null Streichungen**:
`account.analytic.account`/`.plan` (S15/R20), `hr.expense` mit 9 Feldern (S12/R19),
`quality.point`/`quality.check` (S14/R18), `crm.lead.active`/`lost_reason_id`/`probability`
(S12/R11), `stock.location`/`.lot`/`.warehouse`/`.warehouse.orderpoint` und
`stock.quant.lot_id` (S13/R13–R15), `mrp.production.company_id` (S10).

**Warum das zählt:** vier Sprints lang hätte ein echter Feld-Rename in einem Odoo-Update in
genau diesen Modellen unbemerkt bleiben können — sie standen im Manifest nicht drin. Das
Instrument, das R5 gegen Versions-Drift gebaut hat, war gegen die Hälfte des Codes blind,
ohne dass irgendetwas darauf hingewiesen hätte. Ein stiller Ausfall einer
Sicherungsmaßnahme ist schlimmer als ihr Fehlen, weil er Vertrauen erzeugt.

**Warum es passiert ist:** `scripts/check_compat.sh` setzt `ODOO_GENERATOR_CAPTURE_FIELDS=1`
und schreibt das Manifest damit bei jedem Lauf neu — aber es **vergleicht nicht** gegen den
committeten Stand, und sein PASS-Text nennt den Diff nur als manuellen Schritt („1. Diff
field_manifest.json against odoo_actions.FIELD_COMPAT_WHITELIST"). Ein manueller Schritt in
einem Skript, das man selten und unter Zeitdruck ausführt, ist kein Mechanismus.

---

#### Zuschnitt: eigener Spike, kein Arbeitspaket (Korrektur 2026-09-06, S18)

Der ursprüngliche Punkt 1 unten las sich als „wenige Zeilen `git diff --quiet` in
`check_compat.sh`". **Das stimmt nicht.** D21 war als WP1 von S18 geplant und wurde nach
drei kalten Review-Runden wieder herausgelöst: 13 harte Blocker insgesamt, **8 davon in
D21s Mechanik**, und Runde 3 fand erneut Blocker in genau dem, was Runde 2 gefixt hatte —
die Abbruchbedingung des `sprint-review`-Skills §4. Die Ursache ist nicht schlechte
Planung, sondern dass hier drei gekoppelte Unbekannte stecken (Vergleichsbasis,
Formatmigration, Verhältnis zur Whitelist), die ein Arbeitspaket nicht auflöst.

**Warum `git diff --quiet` als Mechanismus nicht trägt:** `check_compat.sh` existiert per
Header, um gegen eine *neue/Beta*-Instanz zu laufen. Andere Instanz ⇒ anderes App-Set ⇒
andere erfasste Modelle. Ein Vergleich, der auf jede Abweichung failt, bricht beim ersten
legitimen Einsatz falsch ab — in beide Richtungen. Live belegt: das Manifest ist
**abdeckungs-**, nicht driftbegrenzt. `res.company` fehlt darin, obwohl
`odoo_actions.py:182` es anlegt (S16); `hr.leave*` fehlt, weil `hr_holidays` auf
demo-test5 uninstalled ist. Beides auch nach der S17-Regenerierung.

**Durchgearbeiteter Entwurfsstand (3 Runden geprüft, Startpunkt des Spikes):**

| Fall | Verdikt |
|---|---|
| Neues Feld in einem Modell, das **beide** Manifeste kennen | Exit 1 (echtes Drift-Signal) |
| Modell nur im neuen Lauf | Report, Exit 0 (App-Set-Unterschied) |
| Modell/Feld nur im committeten Stand | Report, Exit 0 (Abdeckung ist instanzabhängig) |
| Manifest-Feld ohne `FIELD_COMPAT_WHITELIST`-Eintrag | Report, Exit 0 (nie failend) |
| Committete Datei in alter flacher Form | Exit 2 (Migration, kein Drift) |

Mechanik: `dump_captured_fields(path, meta=None)` schreibt
`{"_meta": {"odoo_version", "installed_wanted_modules"}, "models": {…}}` — kein Host, kein
DB-Name, kein Zeitstempel (Diff-Rauschen). Zielpfad aus `ODOO_GENERATOR_MANIFEST_OUT`,
relativ zu `_ROOT`; `check_compat.sh` lenkt auf `field_manifest.new.json` (gitignored) und
ruft `scripts/compare_manifest.py` **vor** jeder PASS-Ausgabe, mit
`if ! python3 …; then …; fi` statt auf `set -e` zu bauen. Kein `subprocess` in
`compare_manifest.py` — CI fährt `bandit -r .` über `scripts/`, B404/B603/B607 sind nicht
geskippt. Der Whitelist-Report ersetzt den heutigen manuellen PASS-Schritt 1, der gegen ein
*drittes* Artefakt vergleicht und sonst ersatzlos verschwände.

**Die drei offenen Punkte, an denen Runde 3 hängen blieb — hier beginnt der Spike:**
1. **Migrationsreihenfolge.** Der Migrations-Commit muss **vor** der Abnahmeliste liegen,
   sonst stellt deren `git checkout field_manifest.json` die alte flache Form wieder her.
   Migrationslauf und Exit-2-Abnahmezweig sind zudem dasselbe Ereignis.
2. **Frische-Garantie.** `field_manifest.new.json` ist gitignored und wird nur manuell
   gelöscht. Greift `ODOO_GENERATOR_MANIFEST_OUT` einmal nicht, schreibt der Dump wieder
   in-place und verglichen wird gegen ein altes `.new`: Exit 0, PASS, Vergleichsbasis still
   überschrieben — exakt die Fehlerklasse, gegen die D21 antritt. Zwei Regeln schließen sie:
   `.new` muss von *diesem* Lauf stammen, und ein leeres `models` ist nie ein Erfolg.
3. **`_meta.odoo_version` hat keinen Leser.** `test_suite.py` kennt die Odoo-Version heute
   nicht (`grep -i version` → null Treffer); der Spike beschafft sie per
   `odoo_actions.get_server_version`. Der manuelle `LAST_VERIFIED_VERSION`-Schritt
   (`check_compat.sh:39-42`) bleibt sonst ohne Eingabe.

**Zusätzlich zu klären:** ob Punkt 3 unten (Whitelist ablösen) mit dem Whitelist-Report
zusammenfällt, und ob `model#method`-Schlüssel überhaupt whitelistfähig sind
(`odoo_actions.py:693-712` sagt nein). Gemessen: 18 der 39 Manifest-Schlüssel haben keinen
Whitelist-Eintrag.

---

**Fix (ursprüngliche Reihenfolge nach Kosten, Punkt 1 durch den Zuschnitt oben ersetzt):**
1. ~~`git diff --quiet field_manifest.json`~~ — siehe Zuschnitt oben. Der Vergleich bleibt
   das Ziel, aber asymmetrisch und mit `_meta`, nicht als Einzeiler.
2. Denselben Vergleich in CI, gegen eine Referenzinstanz. Braucht eine Entscheidung, wo die
   Zugangsdaten liegen — siehe die Einschränkung „keine Firmen-IT" — deshalb nicht Teil von 1.
3. Erwägen, ob `FIELD_COMPAT_WHITELIST` (`odoo_actions.py`) noch eigenständig gepflegt werden
   muss, wenn das Manifest verlässlich aktuell ist. R5/WP1 nannte die Whitelist bereits als
   „von Hand kuratiert und nachweislich unvollständig".

**Komplexität:** eigener Spike, nicht klein · **Benefit:** Hoch — stellt eine
Sicherungsmaßnahme wieder her, die es nominell schon gab.

### D20 ⚪ `ModuleSelections.get` strikt machen

**Neu 2026-09-06, aus S17 ausgegliedert.** `ModuleSelections.get(key)` ist
`getattr(self, key, default)` — ein String-Key-Lookup gegen `orchestrator.py`s
`module_order`. D5 (S17) hat die Tippfehler *innerhalb* eines Configs beseitigt, diesen
äußeren Lookup nicht. Ein Modul-Code, der zu keinem Feld passt, wird weiterhin still
übersprungen (B1-Fehlerklasse).

**In S17 bewusst nicht umgesetzt:** ein striktes `get` würde in `orchestrator.py:102` —
🔒-Datei, außerhalb von `_run_module`s `except Exception` — einen stillen Skip in einen
Absturz verwandeln. Das ist eine Verhaltensänderung, und S17s Prämisse war „null sichtbare
Änderung". Stattdessen deckt seit S17 ein **Invariantentest** in
`tests/unit/test_run_config_unit.py` die Fehlerklasse ab: jeder `orchestrator.module_order`-Code
wird gegen `dataclasses.fields(ModuleSelections)` geprüft. Gleicher Schutz, kein Laufzeitrisiko.

Ein striktes `get` bliebe trotzdem sauberer — aber erst zusammen mit einer Entscheidung, was
`orchestrator.py` bei einem unbekannten Modul-Code tun soll. 🔒, Architekten-Freigabe.

### D17 🟡 Breite `except Exception` gezielt verengen

**Neu 2026-09-05.** 81 breite `except Exception` in 10.280 Zeilen Produktionscode. **Kein Flächenbrand** — nur 5 schlucken stumm (`pass`/`continue`), der Rest loggt. Ein loggender Catch ist nicht dieselbe Fehlerklasse; ein pauschaler Umbau wäre hier falsch.

Das Muster hat aber nachweislich einen echten Bug verdeckt (D16s `mrp.py`-`company_id`-Fall, über Monate). Der Schaden entsteht dort, wo ein geschluckter Fehler dem Aufrufer **unsichtbar** bleibt — das Modul meldet danach trotzdem Erfolg an `on_module_done`.

**Fix (gezielt, nicht flächig):** nur die Stellen verengen, an denen der Catch das Erfolgssignal verfälscht — konkrete Exception fangen oder nach dem Loggen re-raisen. Hotspots nach Dichte: `odoo_actions.py` (11), `modules/hr.py` (11), `connect_service.py` (9), `modules/mrp.py` (8), `modules/crm.py` (8).

**Nicht im Scope:** die ~360 breiten Catches in `tests/` — ein Teil davon ist von CLAUDE.md Pattern 1 ausdrücklich vorgeschrieben (*"verify: no exception raised"*). Vor jeder Aussage darüber erst stichprobenartig 5 Stellen ansehen.

### D18 ⚪ Zurückgestellt — Paketstruktur statt 40 `sys.path.insert`-Shims

**Neu 2026-09-05.** 40 Dateien tragen einen `sys.path.insert(...)`-Shim, damit das flache `import config` sowohl lokal als auch unter dem Docker-`WORKDIR` auflöst. Ein echtes Paket (`odoo_generator/…` mit relativen Imports) würde die Shims ersetzen und nebenbei ruffs `E402` wieder benutzbar machen — `ruff.toml` nennt genau diesen Shim als Grund, `E402` abzuschalten.

**Bewusst zurückgestellt, nicht abgelehnt.** CLAUDE.md verlangt nach jeder Code-Änderung die volle `test_suite.py` gegen die Live-Instanz, die bei ~1 req/s limitiert. Eine Änderung, die jeden Import im Repo anfasst, ist genau die teuerste Sorte zu validieren. **Eigenes Arbeitspaket nach dem S16-Merge**, nie als Beifang.



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




---

### Sprint-WP-Sequenzen S12–S15 → `ROADMAP_ARCHIVE.md`

Verschoben 2026-09-05. Die WP-Sequenzen der abgeschlossenen Sprints S12–S15 standen
hier, obwohl dieses Dokument laut Kopf nur offene/geplante Punkte enthält. Sie stehen
jetzt in [`ROADMAP_ARCHIVE.md`](ROADMAP_ARCHIVE.md) §5. Ab sofort gilt: die WP-Sequenz
eines Sprints wandert mit dessen Merge dorthin.


### S16 (Multicompany) → `ROADMAP_ARCHIVE.md`

Verschoben 2026-09-05 nach dem Merge von
[PR #35](https://github.com/pahuodoo/odoo-daten-generator/pull/35). Anforderungen,
Architektur-Spike (Entscheidungen S16-D1–S16-D15) und beide WP-Sequenzen stehen in
[`ROADMAP_ARCHIVE.md`](ROADMAP_ARCHIVE.md) §5, der Verlauf der neun Cold-Review-Runden
in [`SPRINT_LOG.md`](odoo-daten-generator/SPRINT_LOG.md).


## 5. Umsetzungsreihenfolge

Jedes Arbeitspaket endet mit grüner `test_suite.py` gegen die Live-Instanz
(CLAUDE.md-Pflicht). Vor jedem Sprint-Plan und jeder Review-Runde den
`sprint-review`-Skill aufrufen (CLAUDE.md, Planning-document rule 1).

### Abgeschlossen

S1–S18 sind umgesetzt und in `main`. Diese Tabelle nennt nur noch, was ein Sprint
enthielt — Begründung, Review-Verlauf, Testzahlen und PR-Links stehen in
[`SPRINT_LOG.md`](odoo-daten-generator/SPRINT_LOG.md), die Item-Statusblöcke in
[`ROADMAP_ARCHIVE.md`](ROADMAP_ARCHIVE.md).

| Sprint | Inhalt |
|---|---|
| **S1** Bugfixes kritisch | B1, B2, B3 (+B16) |
| **S2** Datenqualität | B4, B5, B6, B9, B12, B13 |
| **S3** LLM-Minimalismus | A1 (`data_factory`+`static_data`), A2, A3 |
| **S4** Architektur | D1, D2, D3, B11, B14, B15, B7/B8, B10 |
| **S5** API-Versions-Schicht Tier 1 | R5 (Versions-Erkennung, `fields_get`-Warnliste) |
| **S6** PDF | R1/P1+P2 — `pdf_factory`, `modules/documents` |
| **S7** Prozessketten-Kontinuität | R8 — Service-Tagging, Wizard-Fakturierung, Pipeline-Reorder 🔒 |
| **S8** Purchase + Inventory | R2, R3 |
| **S9** Webserver-Deployment | R9 — `web/`, Docker, `gui.py` entfernt |
| **S10** Live-Testphase-Feedback | R10 Phase A+B |
| **S11** API-Kompatibilität + Feedback-Logs | R5 WP1/WP2/WP4/WP5, D9 (WP3 zurückgestellt) |
| **S12** Quick Wins | R11, R16 (Produkt-Ebene), R19 |
| **S13** Lager-Tiefe | R14 (Quant-Anteil), R15, R16 (Location-Ebene), R13 |
| **S14** Prozess-Tiefe | R12, R18 |
| **S15** Analytic Accounting | R20 |
| **S16** Multicompany | R17 — N Firmen, Kontext-Scoping, `STATUS_PARTIAL` |
| **S17** Schema-Härtung | D5 (10 typisierte Modul-Configs), D16 (`partner_company_ids`), D8-Teil |
| **S18** Namens-Hygiene | D6 (`gemini` → `llm`, Feld gelöscht), D8 abgeschlossen (D21 nach 3 Review-Runden ausgegliedert) |

### Offene Kandidaten für S19+

Kein Sprint festgelegt. Nach Priorität:

| Prio | Item | Kurz |
|---|---|---|
| 🟠 | **D21** | `field_manifest.json`-Drift erzwingen — **eigener Spike**, siehe Zuschnitt im D21-Abschnitt |
| 🟠 | **R1** | PDF P3/P4 |
| 🟠 | **R5** | Übersetzungs-Registry (WP3, in S11 zurückgestellt) |
| 🟠 | **R14** | Wareneingangs-Anteil (Quant-Anteil erledigt) |
| 🟡 | **D17** | Breite `except Exception` gezielt verengen |
| 🟡 | **R6** | Multi-Country Customer/Supplier |
| ⚪ | **D18** | Paketstruktur — bewusst zurückgestellt, eigenes WP |
| ⚪ | **D20** 🔒 | Striktes `ModuleSelections.get` (aus S17 ausgegliedert) |

**Empfehlung: D21 als eigener Spike, nicht als Arbeitspaket eines gemischten Sprints.**
Die frühere Fassung dieser Empfehlung nannte D21 „wenige Zeilen in
`scripts/check_compat.sh`" — S18 hat das widerlegt: drei kalte Review-Runden, 8 harte
Blocker allein in D21s Mechanik, Abbruchbedingung des `sprint-review`-Skills §4 ausgelöst.
Der durchgearbeitete Entwurfsstand und die drei verbliebenen offenen Punkte stehen im
D21-Abschnitt oben; dort beginnen, nicht neu herleiten.

**Was das für die Reihenfolge heißt:** ein Sprint, der D21 enthält, sollte **nur** D21
enthalten, bis dessen drei offene Punkte entschieden sind. Alles andere in dieser Tabelle
ist unabhängig davon lieferbar.

### Pro Arbeitspaket verbindlich

Aus CLAUDE.mds Testing Design Patterns:

- Empty-Pool-Guards (P1) für jede neue `random.choice/sample`-Stelle
- LLM-None-Guards (P2) für jeden neuen/geänderten LLM-Pfad
- Feature-Flag-Skip (P3) für jede neue GUI-Option
- Read-Back-Validierung (P4) in jedem neuen Integrationsschritt
- 🔒-Punkte (Pipeline-Reihenfolge, JSON2-Format, Config-Schema, Cache-Namen) vor der
  Umsetzung explizit freigeben lassen
