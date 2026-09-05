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

**Was hier bewusst NICHT steht** (2026-09-05 nachgemessen, damit es niemand "repariert"): der Import-Graph ist ein sauberer Stern — **kein Modul importiert ein anderes Modul**, alle 12 `modules/*.py` hängen nur an `{config, odoo_actions, data_factory, fallback_data, pdf_factory}`, keine Zyklen, Pipeline-Reihenfolge an genau einer Stelle (`orchestrator.py:44`). Und `RunContext` ist kein Gott-Objekt: 23 Schreibstellen, 18 davon mit genau einem Besitzer (die 5 Ausnahmen siehe D8). Beides ist tragfähig.

### D5 🟠 Typisierte Modul-Configs statt roher Dicts 🔒

`ModuleSelections.mrp/hr_recruitment/hr_timeoff/crm_chatter/crm_activities` sind untypisierte Dicts mit Shape-Kommentaren. Fehlerklasse: Tippfehler im Key fällt erst zur Laufzeit (oder nie) auf.

**Fix (nach Architekten-Freigabe, da Config-Schema):** je ein Dataclass (`MrpConfig`, `TimeoffConfig`, …) mit Defaults; `ModuleSelections` referenziert `Optional[MrpConfig]`. GUI erzeugt die Objekte direkt. `enabled`-Bools entfallen (Objekt vorhanden = aktiv).

**Belege nachgemessen 2026-09-05** — Umfang seit der Erfassung gewachsen, Priorität von 🟡 auf 🟠 angehoben:

- Nicht mehr 5, sondern **10** `dict`-typisierte Felder (`grep -c ": dict = field" config.py`): dazu `crm_lost`, `documents`, `stock`, `hr_expense`, `analytic`.
- `config.py` besteht zu **57 %** aus Kommentar (105 von 185 Zeilen). Der Kommentar ersetzt den Typ: er beschreibt in Prosa, was eine Dataclass erzwingen würde.
- Die Falle, vor der die Kommentare warnen, ist real und namentlich benannt — `ModuleSelections.get(module_code)` ist `getattr(self, module_code)`, also ein String-Key-Lookup gegen `orchestrator.py`s `module_order`. `config.py`s eigener `stock`-Kommentar: *"would look up a nonexistent attribute and always skip silently"*. Mit Dataclasses ist diese Fehlerklasse konstruktionsbedingt weg.
- Größter Vereinfachungs-Gewinn pro geänderter Zeile im gesamten Repo: löscht ~60 Kommentarzeilen, ersetzt sie durch prüfbare Typen.

### D6 🟡 Namens-Hygiene: `gemini` → `llm`

Parameter heißt in allen Modulsignaturen `gemini`, Provider ist primär Groq; `RunContext.gemini_model_name` ist ungenutztes Erbe. Umbenennen (`llm`, `llm_model_name`), rein mechanisch.

### D8 ⚪ Kleinigkeiten

**Teilstatus, verifiziert 2026-09-02** — 2 von 5 erledigt, 3 offen. **2026-09-05 um drei Punkte erweitert** (Code-Vereinfachungs-Review):

- ⚪ **Offen:** `test_mrp_live.py` steht weiterhin im Wurzelverzeichnis von `odoo-daten-generator/` → nach `tests/integration/` verschieben oder löschen.
- ⚪ **Offen (neu 2026-09-05):** `.claude/worktrees/docker-autoupdate/` enthält eine vollständige Zweitkopie der Codebase (jede `wc -l`-Auswertung über das Repo zählt sie doppelt mit). Branch `docker-autoupdate` **ist** in `main` gemerged (`git branch --merged main` bestätigt) → `git worktree remove` gefahrlos.
- ⚪ **Offen (neu 2026-09-05):** Fünf `RunContext`-Felder werden von mehr als einem Modul beschrieben, ohne dass irgendwo steht, wem sie gehören: `supplier_ids` (accounting+purchase), `bill_ids` (purchase+accounting), `confirmed_order_ids` (sale+accounting), `product_ids` (master_data+mrp+orchestrator), `company_ids` (master_data+orchestrator). Die übrigen 18 Schreibstellen haben je genau einen Besitzer — also kein Gott-Objekt, kein Umbau nötig. Fix: eine `# Besitzer:`-Zeile pro Mehrfach-Feld in `config.py`.
- ⚪ **Geprüft und verworfen (2026-09-05):** Lint-Ausbau `ruff --select B,C901` gemessen — 97 Treffer (56 × C901, 41 × B: B905 19, B008 10, B904 7, B007 3, B023 2). **Kein einziger echter Bug** darunter: C901 feuert flächig auf die absichtlich langen prozeduralen Modul-Dateien (400–550 Zeilen), beide B023-Fälle liegen in Tests und sind harmlos (Closure wird noch in derselben Schleifen-Iteration aufgerufen). Kein eigenes Item — `ruff.toml`s `select = ["F"]`-Begründung bleibt gültig. Hier notiert, damit der Vorschlag nicht ungemessen wiederkehrt.
- ✅ **Erledigt (durch Umbau, nicht gezielt):** die ursprüngliche `odoo_client._post:46`-Stelle mit dem immer-wahren `response is not None`-Check existiert so nicht mehr — `odoo_client.py`s Fehlerbehandlung wurde in S9/S10 komplett umgebaut (`_record_failure`-Frame-Stack). Die verbleibenden `response is not None`-Checks (z. B. `create_batch`s HTTPError-Handler, `has_create_access`) sind echte Null-Checks, keine toten Bedingungen mehr.
- ✅ **Erledigt:** `LLMService.ping()` existiert (`llm_service.py:262`) und wird von `connect_service.py:245` verwendet — ohnehin gegenstandslos, da `gui.py` seit S9 komplett entfernt ist.
- ⚪ **Offen:** Provider-Erkennung ist weiterhin Prefix-Sniffing — `connect_service.py:126`: `"groq" if llm_key.startswith("gsk_") else "gemini"`. Kein explizites Dropdown/Feld für die Provider-Wahl im Web-Frontend.
- ⚪ **Offen:** `orchestrator.py:54` — `gemini.fetch_name_suggestions(...)` läuft weiterhin unconditional bei jedem Lauf, außerhalb des `skip_master_data`-Gates (Zeile 46). Lädt Namensbänke auch dann, wenn kein Modul sie braucht.

### D16 🟠 `ctx.company_ids` heißt falsch — umbenennen zu `partner_company_ids` 🔒

**Neu 2026-09-05.** `RunContext.company_ids` hält `res.partner`-Ids (Firmen-*Kontakte* aus `master_data.py`), nie eine `res.company`-Id. Der Name hat bereits einen Live-Bug verursacht (`mrp.py`s Work-Center/Fertigungsauftrag-Erstellung, monatelang von einem `try/except` verdeckt, gefixt in S10 Phase A) und ist in `config.py:110` selbst als irreführend dokumentiert.

Seit `f052fe3` (S16-NEU WP2b) steht `res_company_ids` — die *echten* Firmen — direkt daneben. Zwei Namen, ein Präfix Unterschied, gegensätzliche Bedeutung, in derselben Dataclass.

**Fix:** `company_ids` → **`partner_company_ids`**. Name jetzt festgelegt, damit er beim Aufgreifen nicht neu verhandelt wird — er sagt, was drinsteht (`res.partner`-Ids vom Typ Firma). Rein mechanisch, aber 127 Referenzen in 20 Dateien (inkl. `odoo_actions.py`, `odoo_client.py`, `run_config.py`, `web/app.py`, `web/jobs.py`, `connect_service.py`, `orchestrator.py` und 12 Testdateien).

🔒 **Architekten-Freigabe nötig** — `RunContext` ist Config-Schema (CLAUDE.md "Do Not Touch"), gleiche Lage wie D5.

**Zeitpunkt: vor dem S16-Merge, nicht danach.** Jedes weitere S16-Arbeitspaket, das auf `res_company_ids` aufbaut, vergrößert den Diff und die Verwechslungsfläche. Danach ist es dieselbe Arbeit plus die neuen Aufrufstellen.

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

S1–S16 sind umgesetzt und in `main`. Diese Tabelle nennt nur noch, was ein Sprint
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

### Offene Kandidaten für S17+

Kein Sprint festgelegt. Nach Priorität:

| Prio | Item | Kurz |
|---|---|---|
| 🟠 | **D5** 🔒 | Typisierte Modul-Configs statt 10 roher Dicts |
| 🟠 | **D16** 🔒 | `ctx.company_ids` → `partner_company_ids` |
| 🟠 | **R1** | PDF P3/P4 |
| 🟠 | **R5** | Übersetzungs-Registry (WP3, in S11 zurückgestellt) |
| 🟠 | **R14** | Wareneingangs-Anteil (Quant-Anteil erledigt) |
| 🟡 | **D6** | `gemini` → `llm` |
| 🟡 | **D17** | Breite `except Exception` gezielt verengen |
| 🟡 | **R6** | Multi-Country Customer/Supplier |
| ⚪ | **D8** | Kleinigkeiten (4 offene Punkte) |
| ⚪ | **D18** | Paketstruktur — bewusst zurückgestellt, eigenes WP |

**D5 und D16 hängen zusammen** und berühren beide `config.py` (🔒): D5 ersetzt die
Dict-Felder von `ModuleSelections`, D16 benennt ein `RunContext`-Feld um. Zusammen in
einem Sprint kostet eine Architekten-Freigabe statt zwei und einen Test-Durchlauf statt
zwei.

### Pro Arbeitspaket verbindlich

Aus CLAUDE.mds Testing Design Patterns:

- Empty-Pool-Guards (P1) für jede neue `random.choice/sample`-Stelle
- LLM-None-Guards (P2) für jeden neuen/geänderten LLM-Pfad
- Feature-Flag-Skip (P3) für jede neue GUI-Option
- Read-Back-Validierung (P4) in jedem neuen Integrationsschritt
- 🔒-Punkte (Pipeline-Reihenfolge, JSON2-Format, Config-Schema, Cache-Namen) vor der
  Umsetzung explizit freigeben lassen
