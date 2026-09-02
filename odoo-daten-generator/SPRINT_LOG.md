# Sprint Log — odoo-daten-generator

Sprint-für-Sprint-Narrativ, ausgelagert aus `CLAUDE.md` (2026-09-02) um dessen Pro-Session-Ladekosten klein zu halten. Kurzstatus und Backlog-Kandidaten stehen weiter in `CLAUDE.md`s "Current Sprint"-Abschnitt und in `ROADMAP.md` §5; hier steht die volle Historie inkl. Peer-Review-Ergebnissen und live gefundenen Bugs pro Sprint.

---

## Sprint-Historie (S1–S10)

Sprint S4 aus `ROADMAP.md` abgeschlossen (2026-08-03/04): D1 (Fortschritts-Callback
in `orchestrator.run()`, kein Monkeypatch mehr), D2 (`logging_setup.py` statt `print`/
`sys.stdout`-Umleitung), D3 (`create_batch` an 18 Call-Sites über `modules/*.py`), B11
(`call_method`-Fallback-Guard), B14 (Order↔Opportunity partnerbasiert verknüpft), B15
(`num_workcenters` erlaubt 0) landeten am 2026-08-03/04. B7/B8/B10 hatten zu dem Zeitpunkt
bereits ihren Kernbug behoben; im direkten Folgesprint (2026-08-04) ergänzt: B7
(`ModuleSelections.account_bills`, GUI-Feld "Anzahl Eingangsrechnungen"), B8
(`ModuleSelections.sale_confirm_pct`, GUI-Slider "Bestätigt (%)"), B10 (Architekten-Review
entschied gegen den ursprünglich vorgeschlagenen `RunContext.selected_modules`-Umbau — der
Bug war bereits funktional behoben und kein Modul braucht das Feld; nur dokumentiert, kein
Code-Change). S3 (A1–A3), S2 (B4/B5/B6/B9/B12/B13) und S1 (B1–B3, B16) waren bereits vorher
erledigt.

Sprint S5 aus `ROADMAP.md` (2026-08-04) — **Tier 1 abgeschlossen, Tier 2
zurückgestellt**, nach Peer-Review eines vorab geschriebenen Plans durch einen fremden
Opus-Agenten (Kontext nur Plan + Live-Repo, keine Konversationshistorie): `odoo_actions.
get_server_version()` (Versions-Erkennung, GUI-Statuszeile "Odoo-Version" in Screen 2,
nicht-blockierend) + `odoo_actions.check_field_compatibility()` (`fields_get`-Warnliste
gegen ~16 kuratierte Modell/Feld-Paare, log-only). Beide ohne 🔒-Berührung. Tier 2
(`api_versions/*.json`-Mapping-Dateien + Client-Adapter in `odoo_client.py` 🔒) bewusst
zurückgestellt: die Review deckte auf, dass die ursprüngliche Kanonische-Version-Annahme
(19.2) veraltet war (tatsächlich 19.4, siehe unten) **und** dass zwischen den einzigen zwei
bekannten Versionen (19.2/19.4) kein einziger belegter Feld-/Methoden-Rename existiert, nur
eine belegte Feld-Entfernung (`hr.leave.allocation.allocation_type`), die keine Mapping-Zeile
brauchte. Eine Adapter-Infrastruktur ohne einen einzigen realen Delta-Fall wäre nur gegen
synthetische Testdaten prüfbar — Tier 2 wird gebaut, sobald eine zweite reale Zielversion
einen konkreten Rename liefert. Details, Korrekturen und die volle Review-Zusammenfassung:
`ROADMAP.md` §4 R5-Statusblock. Nebenbefund aus dem ersten Live-Lauf der neuen
Warnliste: neuer Bug B17 (`hr.py:33`, `shortcut_behavior` existiert nicht auf saas-19.4) —
dokumentiert, nicht im S5-Scope gefixt.

Odoo-Zielversion-Korrektur bestätigt durch diesen Sprint: `ir.module.module`/`base`/
`latest_version` liefert live `"saas~19.4.1.3"` (nicht `"saas~19.2.1.0.0"` wie ursprünglich
angenommenes Format — eine Dot-Segment-Anzahl weniger; Parser darf keine feste Segmentzahl
voraussetzen).

S5 Tier 2 bleibt im Backlog bis zum oben genannten Auslöser. (Sprint-Reihenfolge ab hier
umnummeriert — siehe S7-Block unten: S7 wurde zu Prozessketten-Kontinuität/R8, S8 ist jetzt
Purchase+Inventory/R2+R3.)

Sprint S6 (2026-08-04) — **abgeschlossen**, nach Peer-Review eines vorab geschriebenen Plans
durch einen fremden Opus-Agenten (Kontext nur Plan + Live-Repo, keine Konversationshistorie,
gleiches Verfahren wie S5): `pdf_factory.py` (neu, reines Python/`fpdf2`, keine Odoo-/
Netzwerk-Calls — `build_vendor_bill_pdf`/`build_cv_pdf`), `modules/documents.py` (neu,
Pipeline-Schritt P1 Eingangsrechnungs-PDFs an `account.move` ohne neuen LLM-Call, P2
Bewerbungs-CVs an `hr.applicant` mit einem gebündelten LLM-Call `fetch_cv_bullet_points_batch`,
nicht gecacht wie `crm_chatter`), `RunContext.applicant_ids` + `ModuleSelections.documents`
(🔒 `config.py`, rein additiv), ein Voraussetzungs-Fix in `recruiting.py`
(`_create_applicants` gab die `create_batch`-IDs bisher nirgendwo zurück — ohne Fix hätte P2
keine Bewerber-ID zum Anhängen gehabt), `orchestrator.py` (🔒, rein additiv — ein Tupel ans
Ende von `module_order` angehängt, `is_installed=True` hartkodiert statt an
`installed_modules` gekoppelt, da `ir.attachment` kernfunktional ist und "documents" zufällig
auch der technische Name von Odoos echter Documents-App ist), GUI-Anbindung in `gui.py`
(die Peer-Review fand hier einen echten Blocker: `documents` ist kein von Odoo geprüftes Modul,
lief also nie durch das `if key in installed`-Gate in `module_defs` — als eigenständiger,
ungegateter `_module_block`-Aufruf plus eigenständiger Zweig in `_on_generate` und
Sonderfall in Screen 4s `module_order_keys` gelöst, nicht als Eintrag in `module_defs`).

**Live gefundener Bug (nicht vom Plan vorhergesehen):** `ir.attachment`s Inhaltsfeld heißt auf
dieser Instanz `raw`, nicht `datas` — `datas` existiert als Feld auf `ir.attachment` gar nicht
(`search_read` wirft `Invalid field 'datas'`), aber `create()` nimmt einen `datas`-Key
stillschweigend an und verwirft ihn (200 OK, aber kein Inhalt gespeichert, kein Fehler). Auch
`db_datas` (existiert laut `fields_get`) wird beim direkten Schreiben verworfen — Anhänge liegen
auf dieser Instanz im Filestore, nicht in der DB-Spalte. Nur `raw` rundet Inhalt beim
Create/Read korrekt. Erst durch den vollen Live-Suite-Lauf aufgefallen (Unit-Tests mit
gemocktem Client hätten das nie gefangen) — dokumentiert in CLAUDE.md „Verified field
gotchas". GUI-Anbindung zusätzlich headless verifiziert (echte `App`-Instanz, echte
Screen-3-/Screen-4-Widgets, `CTkCheckBox.select()`/`CTkRadioButton.select()` +
`button.cget("command")()`, kein sichtbares Fenster nötig) — kein manueller Klick-Durchlauf
im Browser/Desktop, aber Test deckt exakt den von der Peer-Review gefundenen Blocker ab.
Vollständige Test-Suite (`tests/integration/test_suite.py`): 151/151 Unit- + 61/61
Live-Integration-Schritte grün, inkl. neuer `test_documents_unit.py`/`test_documents.py`.

Sprint S7 (2026-08-05) — **abgeschlossen**, Prozessketten-Kontinuität (R8). Sprint-Reihenfolge
wurde außerhalb dieser Session neu priorisiert: R8 ist Voraussetzung für R2/R3
(Purchase+Inventory), nicht parallel dazu — S7 = R8, Purchase+Inventory rückt auf **S8** (Draft
bereits vorhanden: `/Users/paul/.claude/plans/continue-implementation-with-the-woolly-toast.md`,
vor Umsetzung gegen aktuellen Code-Stand re-verifizieren). Plan wurde **zweimal** von einem
fremden Opus-Agenten peer-reviewed (Kontext nur Plan+Live-Repo, keine Konversationshistorie,
gleiches Verfahren wie S5/S6) — nach der ersten Review erfolgte ein Nutzer-Kurswechsel: der
Erst-Entwurf markierte genau ein "Hero"-Serviceprodukt pro Lauf (5 neue `RunContext`-Felder,
manueller Reverse-Link-Workaround); Nutzer-Feedback "wenn es für ein Produkt funktioniert,
funktioniert es für alle" führte zu einer zweiten, universellen Version (angewendet auf jedes
Serviceprodukt, keine neuen `RunContext`-Felder, `sale.py`/`config.py` unangetastet — kleinerer
Diff als die Hero-Version). Volle Details, Live-Spike-Ergebnisse und das
Datenerzeugungs-Audit (auf Nutzer-Anfrage: alle `create`/`create_batch`-Aufrufstellen der 9
Module gegen native Odoo-Automatiken geprüft, `mrp.py` explizit als kollisionsfrei bestätigt)
in `ROADMAP.md`s R8-Statusblock (§4). Kern: `service_tracking`/`invoice_policy`/
`service_type` auf allen Serviceprodukten (gated auf `project`+`hr_timesheet` installiert) lässt
Odoo bei Auftragsbestätigung selbst Projekt+Aufgabe erzeugen; Zeiterfassung gegen diese
Aufgaben treibt echte Delivered-Qty-Fakturierung über den nativen
`sale.advance.payment.inv`-Wizard statt manuellem `account.move`-Nachbau.
`orchestrator.py`-Reorder (🔒, architekten-freigegeben im Plan): `account` läuft jetzt nach
`hr`/`project`/`hr_timesheet` statt davor. 157/157 Unit- + 65/65 Live-Integration-Schritte
grün, inkl. 7 neuer R8-Schritte über `test_master_data.py`/`test_sale.py`/`test_project.py`/
`test_accounting.py`.

Sprint S8 (2026-08-28) — **abgeschlossen**, Purchase + Inventory (R2/R3). Plan wurde
zweimal peer-reviewed nach dem etablierten S5-S7-Verfahren: ein Plan-Agent verifizierte den
2026-08-04-Erstentwurf komplett neu gegen den aktuellen Code-Stand (Zeilennummern,
Funktionssignaturen, live via odoo-fields MCP nachgeprüfte Odoo-Feldschemata), danach ein
zweiter, fremder Agent (nur Plan-Text + Live-Repo, keine Konversationshistorie) review'te
das Ergebnis kalt. Kern: `modules/purchase.py` (neu) — `purchase.order` → `button_confirm`
→ `action_create_invoice` (mit manuellem `account.move`-Nachbau als Fallback, gleiches
Per-Order-Isolations-Muster wie `create_invoices_from_orders` post-R8) →
`odoo_actions.post_invoices`; `modules/inventory.py` (neu) — `stock.quant` mit
`inventory_quantity` gesät, `action_apply_inventory` angewendet. `odoo_actions.py` erhielt
drei neue Exports (`create_suppliers`, `post_invoices` — beide aus `accounting.py`
herausgezogen, `accounting.py`s fünf Call-Sites entsprechend aktualisiert — sowie
`get_default_warehouse`); `config.py` additiv um `ModuleSelections.purchase`/
`purchase_confirm_pct`/`stock` und `RunContext.supplier_ids` erweitert;
`orchestrator.py`-Anhang (🔒, additiv) nach `hr_recruitment`, vor `documents`.

**Ein von der Plan-Review selbst gefundener Bug (vor Implementierung gefixt):** der
2026-08-04-Entwurf sah `ModuleSelections.stock_avg_qty: int` vor, gepaart mit
`orchestrator.py`s `module_code="stock"` — da `ModuleSelections.get(module_code)` reines
`getattr(self, module_code)` ist, hätte das Modul auf jedem Lauf still und für immer
übersprungen (kein Attribut namens `stock`). Gefixt durch dict-Form
(`stock: dict = {"avg_qty": int}`), analog zu `mrp`/`documents`.

**Zwei live gefundene Bugs (nicht vom Plan vorhergesehen, erst beim ersten echten
End-to-End-Lauf entdeckt):**
1. `RunContext.company_ids` heißt zwar "company", enthält aber trotz seines Namens
   `res.partner`-IDs (von `master_data.py` erstellte Kunden-/Firmenkontakte — z.B.
   verwendet `sale.py` `ctx.company_ids[i]` direkt als `partner_id` einer Bestellung),
   **niemals** eine echte `res.company`-ID. Der ursprüngliche Plan übernahm unkritisch
   `mrp.py`s vorhandenes Muster (`company_id = ctx.company_ids[0]`, benutzt für
   `mrp.workcenter.company_id`/`get_manufacturing_picking_type_id`) — das ist dort
   vermutlich selbst bereits ein stiller, durch breites try/except maskierter Bug (Work
   Centers/Fertigungsaufträge könnten deshalb schon länger leise übersprungen werden,
   **nicht in diesem Sprint gefixt**, siehe Backlog-Hinweis unten). Für `purchase.py`/
   `inventory.py` wäre dieser Fehler nicht harmlos maskiert gewesen, sondern hätte
   `get_default_warehouse` immer `None` liefern lassen und beide neuen Module bei jedem
   echten Lauf komplett stillschweigend übersprungen. Gefixt durch neuen
   `odoo_actions.get_main_company_id(client)`-Helper (gleiches Fallback-Muster wie
   `get_main_company_name`: erst `id=1`, dann erste gefundene `res.company`), von beiden
   neuen Modulen statt `ctx.company_ids[0]` verwendet.
2. `purchase.order.action_create_invoice` lässt `account.move.invoice_date` unbelegt
   (`False`) — das Datum ist das externe Lieferantendatum, Odoo setzt hier keinen
   Default. `action_post` scheitert dann mit "Das Datum der Rechnung/Erstattung ist
   erforderlich" (live bestätigt gegen `demo-pahu-test1.odoo.com`). Gefixt: `purchase.py`
   schreibt `invoice_date` (heute) auf die per `action_create_invoice` erzeugten Bills,
   bevor `post_invoices` aufgerufen wird — der manuelle Fallback-Pfad setzte es schon immer
   korrekt.

Alle drei live-unsicheren Methodennamen bestätigt: `purchase.order.button_confirm` (nicht
`action_confirm` nötig — Odoo-Core-Name, stabil, im Gegensatz zu `sale.order`s
`action_confirm`; dual-try trotzdem implementiert als billige Absicherung),
`purchase.order.action_create_invoice` (öffentlich, liefert die Bill über die Reverse-Link
`invoice_ids`), `stock.quant.action_apply_inventory`. `stock.quant.company_id`/`in_date`
sind laut `fields_get` als `readonly` markiert, runden aber trotzdem korrekt beim `create()`
durch (kein `ir.attachment.datas`-artiger Silent-Drop — live verifiziert).

176/176 Unit- + 70/70 Live-Integration-Schritte grün, inkl. 19 neuer S8-Schritte über
`test_purchase_unit.py`/`test_inventory_unit.py`/`test_purchase.py`/`test_inventory.py` plus
einer neuen `is_storable`-Assertion in `test_mrp_batch_unit.py`. GUI-Anbindung bewusst auf S9
verschoben (siehe Plan — `gui.py`s `WANTED_MODULES` kennt `purchase`/`stock` noch nicht,
S9-Implementierer muss das beim HTML-Frontend-Umbau nachziehen, nicht nur die
Test-Infrastruktur-`_WANTED`-Liste).

**Bekannter, nicht in diesem Sprint gefixter Backlog-Punkt:** `mrp.py:282,334` verwendet
denselben fehlerhaften `ctx.company_ids[0]`-als-`res.company`-ID-Zugriff für
`mrp.workcenter.company_id` und `get_manufacturing_picking_type_id` — durch breites
try/except maskiert (kein Crash, nur eine leise Log-Zeile), potenziell seit Einführung
bereits fehlerhaft. Nicht Teil des S8-Scopes; als möglicher Folge-Task vorgemerkt.

Sprint S9 (2026-08-28) — **abgeschlossen**, Webserver-Deployment (neues Roadmap-Item **R9**,
zusammen mit **D4** und **D7**). `gui.py` ist gelöscht, nicht parallel weitergepflegt. Der
Ausgangspunkt war ein vom Nutzer geliefertes HTML-Mockup (`demodatenkonsole.html`), das die
API-Oberfläche festlegte; das Backend wurde daraus abgeleitet. **S9 ersetzt den Aufrufer,
nicht die Pipeline** — `orchestrator.py` wurde nicht angefasst (kein `mode`-Parameter, 🔒
unberührt), weil `orchestrator.run()` seit D1 bereits GUI-frei ist und
`ModuleSelections`/`RunContext` reine Dataclasses ohne GUI-Kopplung sind.

Neu: `web/` (FastAPI-App, Guards A+B, Session-Store, Worker-Queue mit Admission Control,
SSE-Broker), `connect_service.py` + `run_config.py` (D4-Extraktion, framework-frei),
`run_journal.py` (D7), `static/index.html`+`app.js`+`app.css`, `Dockerfile`/
`docker-compose.yml`/`.env.example`. Geändert: `logging_setup.py` (lauf-gebundenes Logging
über `contextvars` + `RunIdFilter` — jedes Modul loggt über `getLogger(__name__)`, Läufe
sind also **nicht** über den Logger-Namen trennbar, und `configure_logging()` hängt beim
Import von `orchestrator.py` genau einen Handler an den **Root**-Logger),
`odoo_client.py` 🔒 (Weiterleitungen an allen drei `session.post`-Stellen abgelehnt,
Fehlerkörper an **beiden** Kopien redigiert — Log **und** `self.errors`, das die
Lauf-Zusammenfassungs-API speist), `llm_service.py` (Cache-Pfad per Env, atomarer
Cache-Write, neues öffentliches `ping()`), `requirements.txt` (gepinnt, `customtkinter`
entfernt), `config.ini.example` (vestigiales `username` entfernt).

**Bewusst gestrichen (Produktentscheidung nach Peer-Review):** Datensatz-Vorschau und
-Bearbeitung samt Backend-Gerüst — vollständige Begründung im R9-Statusblock von
`ROADMAP.md` §4. Sie wird erfahrungsgemäß erneut vorgeschlagen; die Kurzfassung
lautet: Odoo ist der bessere Datensatz-Browser, das Ziel ist eine Wegwerf-`demo-*`-DB, und
seit S7/R8 entstehen die interessantesten Datensätze nativ in Odoo und wären in einer
Vorschau **nie** sichtbar gewesen.

**S8-Carry-overs geschlossen:** `WANTED_MODULES` (jetzt in `run_config.py`, geteilt mit
`tests/integration/test_suite.py` statt dupliziert) enthält `purchase` und `stock`;
`/api/connect` liefert `feature_flags` — ohne sie wären alle MRP-Arbeitszentren,
BOM-Vorgänge und Qualitätsprüfpunkte still nie wieder erzeugt worden (B1-Fehlerklasse).

**LLM-Datenfluss — vollständig nachgezogen (2026-08-28, nach Nutzerfrage).** Jede
`fetch_*`-Aufrufstelle der neun Module wurde daraufhin geprüft, ob ein aus Odoo *gelesener*
Wert in einen Prompt gelangt. Ergebnis: **genau ein Prompt**, `modules/crm.py`s
Chatter-Prompt, und dort zwei Felder — `customer` (ein `res.partner`-Name) und `salesperson`
(ein `res.users`-Name). Alles andere ist unkritisch und soll nicht erneut untersucht werden:
`mrp.fetch_all_bom_components` baut auf `ctx.name_banks` (LLM-erzeugt),
`project.fetch_all_project_stages` auf gerade selbst angelegten Projektnamen,
`recruiting.fetch_job_summaries_batch` auf LLM-Stellentiteln,
`documents.fetch_cv_bullet_points_batch` auf Bewerbern **dieses** Laufs. Vorhandene Produkte
werden ausschließlich als IDs verwendet, nie als Text.

Kritisch wird der Chatter-Prompt erst mit `use_existing`: dann ist `customer` ein *echter
bestehender* Kontakt statt eines vom Lauf erfundenen. Der `salesperson`-Name stammt
**immer** aus `res.users` der Zielinstanz, unabhängig von der Option.

Umgesetzt: eine ausdrückliche Einwilligung in Screen 02. Ohne Antwort weisen sowohl der
Browser als auch `POST /api/runs` den Lauf ab. Ablehnen ist **kein** reiner UI-Zustand —
`ModuleSelections.crm_chatter["use_db_names"]` wird `False` und `crm.py` sendet dann
„Kunde"/„Verkäufer" statt der echten Namen. Die frühere Behauptung „genau ein Pfad, nämlich
die Branchen-Vorbefüllung" war falsch; `determine_industry_from_company_name` ist trotzdem
entfernt, weil sie als Einzige *vorhandene* Kundendaten ungefragt las.

Die allgemeine Invariante („kein Wert aus einem Datensatz, den dieser Lauf nicht selbst
erzeugt hat, erreicht einen Prompt") bleibt als automatischer Check offen — sie bräuchte
Provenienz-Verfolgung. Praktisch ist sie jetzt durch die Einwilligung plus die obige
Aufstellung abgedeckt.

237/237 Unit-Schritte grün (von 176), inkl. 4 neuer Testdateien
(`test_web_security_unit.py`, `test_web_api_unit.py`, `test_run_config_unit.py`,
`test_run_journal_unit.py`) plus neu geschriebenem `test_logging_setup.py` (die alte
Idempotenz-Prüfung zählte Root-Handler und testete damit genau das ersetzte Design).
Live-Integration um `tests/integration/test_run_journal.py` erweitert.

Sprint S10 (2026-08-29) — **abgeschlossen**, Live-Testphase-Feedback (neues Roadmap-Item
**R10**). Plan zweimal peer-reviewed, einmal pro Phase (fremder Opus-Agent, Plan+Live-Repo,
keine Konversationshistorie — gleiches Verfahren wie S5–S8; Phase A 10 Blocker + 11
Should-fix, Phase B 6 Blocker + 9 Should-fix, jeweils vor Umsetzung eingearbeitet).
Vollständiger Statusblock in `ROADMAP.md`s R10-Abschnitt; Kurzfassung hier:

`odoo_client.py` 🔒 — Fehleraufzeichnung wandert von `_post` in einen `_record_failure`-
Frame-Stack, der die öffentlichen Methoden umschließt: ein `errors`-Eintrag pro gescheiterter
*logischer Operation* statt einer pro HTTP-Versuch (die Kettenreihenfolge selbst bleibt
unverändert — siehe die zwei neuen Gotchas oben zu `has_access` und `/call/`+`/call_kw/`).
Neue `has_create_access(model)`-Methode. F8 (Payload-Form merken) bewusst **nicht** gebaut —
hätte die 🔒-Kettenreihenfolge geändert, Nutzen unbelegt; siehe R10-Statusblock.
`odoo_actions.py` — `probe_model_access`/`MODEL_ACCESS_PROBES`/`PRIMARY_MODEL_PER_MODULE`,
`get_enabled_features`s `mrp_routings`/`quality` jetzt `has_create_access`-gestützt,
`check_field_compatibility` auf `installed_modules` gegatet (spart die Mehrheit der
Connect-Anfragen). `run_config.py` — `effective_installed_modules()` filtert
`ctx.installed_modules` auf schreibbare Module, eine Funktion für Server und Frontend;
`GATE_ONLY_MODULES` (`hr_holidays`/`hr_work_entry` — probiert, beschriftet, nie eine
Fortschrittszeile). `modules/hr.py`s `create_leave_data` und `modules/documents.py`s
`create_documents` gaten zusätzlich auf `ctx.model_access`; `modules/mrp.py`s
`ctx.company_ids`-Bug (Backlog seit S8) gefixt — `get_main_company_id(client)` statt
`ctx.company_ids[0]`. `config.py` 🔒 additiv: `RunContext.model_access`/`skipped_modules`.
`web/jobs.py` neuer Modulstatus `MODULE_SKIPPED` (additiv). Frontend: neuer Checklistenschritt
„Schreibrechte", Modul-Karten zeigen „keine Schreibrechte" getrennt von „nicht installiert".

**Phase A — live bestätigt (2026-08-29, `demo-test5`, mit frischem API-Schlüssel nach
zwischenzeitlichem Ablauf des vorherigen):** `hr_holidays`/`hr_work_entry` sind auf dieser
Instanz tatsächlich `state=uninstalled` — genau der Fall, den F6 vermutete. Die neue
Sonde/das neue Gate greifen korrekt: `tests/integration/test_hr.py`s Urlaubs-Schritte
melden sauber SKIP statt eines 404-Fehlschlags (die Datei rief die low-level Helfer bisher
ungegatet auf und musste dafür selbst ein `ctx.installed_modules`-Gate bekommen). Gemerged
als [PR #12](https://github.com/pahuodoo/odoo-daten-generator/pull/12).

**Phase B** (F1–F5: Datenbankfeld weg, Weiter/Nav-Gate, Ansicht „Prüfen" streichen,
Einstiegs-Tutorial, PDF-Varianten) — Plan vor Umsetzung peer-reviewed; Kernkorrekturen: das
Gate sperrt auf `data.ok` (Odoo+LLM erreichbar), nicht auf „alle Checklistenschritte grün" —
sonst würde ein einzelner nicht-fataler roter Schritt (z. B. ein blockiertes Modul aus
Phase A) die gesamte Konsole schwärzen statt nur dieses eine Modul zu deaktivieren, was
Phase As eigenem Design widerspräche. Das Gate ist zudem ein **Latch**
(`state.everConnected`), nicht der Live-Verbindungsstatus — sonst würde ein fehlgeschlagener
Re-Connect während eines laufenden Laufs die Generierungsansicht und „Diesen Lauf löschen"
aussperren. `config.ini.example` behält `db` als optionalen Wert, weil
`tests/integration/test_suite.py`/`test_mrp_live.py` ihn bisher als hartes Dict-Subscript
lasen und sonst `KeyError` geworfen hätten. Die PDF-Varianten-Determinismus nutzt einen
lokalen `random.Random(zlib.crc32(...))`, nie `random.seed()` — Letzteres hätte den
globalen Zufallsgenerator kontaminiert und damit auch die im selben Modul später
laufende CV-PDF-Erzeugung unbeabsichtigt deterministisch gemacht (per Test verifiziert, dass
die globale `random`-Sequenz durch einen Rechnungs-PDF-Aufruf unverändert bleibt).

301/301 Unit- (von 294), 71/76 Live-Integrationsschritte grün — dieselben 5 vorbestehenden
Fehlschläge wie in Phase A (`hr.job.payment_interval` existiert auf `demo-test5` nicht,
`modules/recruiting.py` unverändert seit vor S10; als eigene Aufgabe ausgelagert, nicht Teil
von S10), keine neuen. Live per Browser verifiziert: kompletter Verbindungs→Konfiguration→
Lauf-Zyklus inkl. Live-Zusammenfassung, Nav-Sperre, Tutorial-Overlay (erscheint einmalig,
persistiert über `localStorage`, per „?"-Knopf erneut aufrufbar) und PDF-Varianten
(zwei Layouts als PDF gerendert und visuell verglichen — sichtbar unterschiedlich, gleicher
Lieferant zweimal ergab dasselbe Layout).

Nächster Sprint: offen. Backlog-Kandidaten: R6 (Multi-Country), R7 (JSON-Demo-Plan),
S5 Tier 2, Provenienz-Invariante (§R9), F8 (Payload-Form merken, siehe oben).

**`hr.job.payment_interval`-Bug — behoben (2026-08-29).** Ursprüngliche Diagnose
(Feldschema-Mismatch) war falsch — korrigierte Diagnose und Fix-Umfang siehe „Verified field
gotchas" oben. 306/306 Unit-, 79/79 Live-Integrationsschritte grün (von 71/76 — die 3
zusätzlichen Schritte sind `test_documents.py`s P1/P2/Pattern-5-Schritte, die im kaputten
Zustand durch einen frühen `return` in der Setup-Exception nie gezählt wurden). Separat,
zeitgleich in einer parallelen Session gemerged: CI-Lint-Infrastruktur (`ruff.toml`,
`.github/workflows/ci.yml`) — eigener Commit, nicht Teil dieses Fixes.

**Prozess-Hinweis (2026-08-04):** Dieser Abschnitt lag zeitweise eine ganze Session hinter dem
tatsächlichen Code-Stand zurück — D1/D2/D3/B11/B14/B15 waren bereits implementiert und getestet,
aber hier noch als "nächster Sprint" gelistet, und `ROADMAP.md` zitierte `gui.py`-
Zeilennummern, die nicht mehr existierten. Vor dem Vertrauen auf die Sprint-Status-Prosa hier:
gegen den tatsächlichen Datei-Inhalt/Zeilennummern verifizieren, nicht nur gegen diesen Text.

Neuer Backlog-Punkt seit S3-Review: R6 — Multi-Country Customer/Supplier Generation (siehe
§4 Roadmap). `static_data.py` ist bereits länderweise (DE/AT/CH) strukturiert, damit weitere
Märkte eine reine Datenergänzung sind.

