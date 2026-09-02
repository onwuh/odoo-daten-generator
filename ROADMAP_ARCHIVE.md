# Roadmap-Archiv — erledigte Punkte

Abgeschlossene Bugs/Design-Punkte/Roadmap-Items aus `ROADMAP.md`, hier archiviert um die Hauptdatei klein zu halten. Inhalt unverändert aus `ROADMAP.md` übernommen (Stand vor Archivierung: 2026-09-02). Sprint-für-Sprint-Narrativ steht separat in `odoo-daten-generator/SPRINT_LOG.md`.

---

## 2. Bugs & Logikfehler
### B7 ✅ Erledigt — `accounting.py` — Mindestens 10 Eingangsrechnungen, immer

War: `num_bills = max(10, num_invoices // 2)` erzwang immer ≥10 Vendor Bills. Core-Bug bereits am 2026-08-03/04 behoben (`max(1, num_invoices // 2)`); GUI-Konfigurierbarkeit (`ModuleSelections.account_bills: Optional[int] = None`, GUI-Feld "Anzahl Eingangsrechnungen" in `_sub_account`, min 0) im S4-Folgesprint ergänzt — `num_bills` wird jetzt vor dem `purchase_pool`-Gate berechnet, damit ein `0`-Override auch `_create_suppliers` überspringt. Getestet in `tests/unit/test_accounting_batch_unit.py` (B7-Tests) und `tests/integration/test_accounting.py` (Step 9, Pattern 4 Read-Back).

### B8 ✅ Erledigt — `sale.py` — Bestätigung hart auf 5 Aufträge begrenzt

War: `orders_to_confirm = ctx.order_ids[:max(1, min(5, len(ctx.order_ids)))]` bestätigte bei 200 Aufträgen genau 5. Core-Bug bereits am 2026-08-03/04 behoben (`_DEFAULT_CONFIRM_PCT = 65`-Konstante, skaliert mit Auftragsanzahl); GUI-Konfigurierbarkeit (`ModuleSelections.sale_confirm_pct: int = 65`, GUI-Slider "Bestätigt (%)" in `_sub_sale`, analog `validate_pct`) im S4-Folgesprint ergänzt — die Modul-Konstante ist entfernt, `sale.py` liest `ctx.module_selections.sale_confirm_pct`. Getestet in `tests/unit/test_sale_unit.py` und `tests/integration/test_sale.py` (Step 7, Pattern 4 Read-Back).

### B10 ✅ Erledigt (dokumentiert, kein Code-Change) — `installed_modules` enthielt *ausgewählte*, nicht installierte Module

War: `installed_modules=selected_modules if mode_val == "both" else set()` — Namenskonflation zwischen "installiert" und "ausgewählt". Core-Bug bereits am 2026-08-03/04 behoben: `gui.py` befüllt `ctx.installed_modules` wieder aus `self.installed_modules` (dem echten Screen-2-Odoo-Probe); nicht ausgewählte Module bleiben bei ihrem `ModuleSelections`-Default (0/leeres dict), sodass das bestehende Truthiness-Gate im Orchestrator sie korrekt überspringt (verifiziert u. a. durch den bestehenden "B10 Pattern 4"-Test in `test_accounting_batch_unit.py`).

Architekten-Review (2026-08-04, S4-Folgesprint) hat den ursprünglich vorgeschlagenen Mechanismus (`RunContext.selected_modules: Set[str]` + explizites Orchestrator-Gate) bewusst **nicht** umgesetzt: kein Modul liest `selected_modules` heute, ein zusätzliches 🔒-Config-Schema-Feld ohne Konsument wäre totes Gewicht, und ein explizites Zweit-Gate (Option 2) würde eine zweite "was soll laufen"-Eingabe einführen, die mit den bestehenden Zählfeldern nicht zwangsläufig synchron bleibt — ein Aufrufer, der Zählwerte setzt aber `selected_modules` vergisst, würde still gar nichts ausführen. Bewusste Entscheidung gegen weiteren Umbau; siehe Sprint-S4-Notiz unten.

### B11 ✅ Erledigt — `odoo_client.py` — Letzter `call_method`-Fallback wirft Argumente weg 🔒

War: Fallback 3 postete `{}` (nur Context) unabhängig vom Inhalt von `args`/`kwargs`/`ids`. Behoben (2026-08-03/04): Guard `if ids or args or kwargs: raise` (odoo_client.py, in `call_method`) lässt Fallback 3 nur noch feuern, wenn wirklich nichts zu senden war. Getestet in `tests/unit/test_odoo_client_unit.py`.

### B14 ✅ Erledigt — `sale.py` — Order↔Opportunity-Verknüpfung ignoriert Partner

War: `zip(ctx.order_ids, ctx.opportunity_ids)` verknüpfte positionsweise. Behoben (2026-08-03/04): `create_sale_data` gruppiert Opportunities nach `partner_id` und ordnet jeder Order nur eine Opportunity desselben Kunden zu (kein Match → keine Verknüpfung). Getestet in `test_sale_unit.py` und `tests/integration/test_sale.py` (Step 6, bewusst umgekehrte Opp-Reihenfolge zur Regressionsprüfung gegen positionsbasiertes `zip`).

### B15 ✅ Erledigt — `mrp.py` — `max(1, num_workcenters)` machte 0 unmöglich

War: `num_workcenters = max(1, int(...))` erzwang ≥1 auch bei deaktivierten Routings. Behoben (2026-08-03/04): `max(0, ...)`. Getestet in `tests/unit/test_mrp_batch_unit.py`.

### B17 ✅ Erledigt (2026-08-04) — `hr.py:33` — `hr.work.entry.type.shortcut_behavior` existiert nicht auf saas-19.4

Entdeckt 2026-08-04 durch S5s neue `fields_get`-Warnliste (`odoo_actions.
check_field_compatibility`), live gegen `demo-pahu-test1.odoo.com` verifiziert: 74
Live-Felder auf `hr.work.entry.type` geprüft, keines heißt oder ähnelt `shortcut_behavior`.
`get_or_create_annual_leave_type` (`modules/hr.py:18-39`) sendet das Feld im `create()`-Call
für einen neuen Urlaubstyp — bisher unbemerkt, weil der `create()`-Zweig nur feuert, wenn
**keine** `hr.work.entry.type` mit `requires_allocation=True` existiert; auf der Live-Instanz
existiert durch frühere Läufe bereits einer, der Zweig wird im aktuellen Test-Lauf also nie
mehr durchlaufen. Auf einer frischen/leeren Odoo-Instanz (erster Lauf überhaupt) würde
`create()` mit einem unbekannten Feld gegen die Instanz gehen — Odoo verwirft unbekannte
Keys teils still, teils mit Fehler, je nach ORM-Version; nicht abschließend getestet, da auf
der einzigen Live-Instanz kein leerer `hr.work.entry.type`-Zustand mehr herstellbar ist, ohne
bestehende Daten zu löschen.

**Fix:** `'shortcut_behavior': 'add',` aus dem `create()`-Call in `modules/hr.py:33` entfernt
(kein Ersatzfeld gefunden — kein `shortcut_behavior`-ähnliches Feld im Live-`fields_get`).
**Test:** `tests/unit/test_hr_unit.py` ("B17: get_or_create_annual_leave_type omits
shortcut_behavior") — Mock-Client mit leerem `search_read` (erzwingt den `create()`-Zweig),
Assert `'shortcut_behavior'` nicht in den übergebenen `vals`. Live-`create()`-Zweig bleibt
auf der aktuellen Instanz nicht direkt testbar (bereits ein `hr.work.entry.type` mit
`requires_allocation=True` vorhanden aus früheren Läufen) — Unit-Test ist der einzig
mögliche Regressionsschutz dafür, siehe Bug-Beschreibung oben.

---

## 3. Architektur- & Design-Verbesserungen
### D1 ✅ Erledigt — Fortschritts-Callback statt Monkeypatching

War: `gui.py` patchte `orchestrator._run_module` zur Laufzeit. Behoben (2026-08-03/04): `orchestrator.run(client, gemini, ctx, on_module_start=None, on_module_done=None)` mit `_run_module(name, handler, ..., on_start=None, on_done=None)` — kein Monkeypatch mehr, `gui.py` übergibt Thread-sichere `self.after(0, ...)`-Wrapper. Getestet in `tests/unit/test_orchestrator_unit.py` (D1a–e, inkl. Regressionsguard, dass Callbacks an **beiden** Call-Sites feuern — master_data-Sonderfall und Modul-Schleife).

### D2 ✅ Erledigt — `logging` statt `print` + stdout-Umleitung

War: `QueueWriter` bog global `sys.stdout` um. Behoben (2026-08-03/04): `logging_setup.py` (`configure_logging()` für einen Konsolen-`StreamHandler`, `QueueLogHandler` fürs GUI-Log-Textfeld); alle Module nutzen `logger.info/warning` statt `print` (0 verbleibende `print()`-Aufrufe in `orchestrator.py`, `llm_service.py`, `odoo_client.py`, `gui.py`, `modules/*.py` — nur noch in Test-Runnern/`test_mrp_live.py`, außerhalb des D2-Scopes). Emoji-Präfixe bleiben über den `"%(message)s"`-Formatter erhalten. Getestet in `tests/unit/test_logging_setup.py`.

### D3 ✅ Erledigt — Batch-Erstellung konsequent nutzen

War: `create_batch` (`odoo_client.py`) wurde nur von `master_data._create_products` genutzt, alle anderen Stellen N+1. Behoben (2026-08-03/04): 18 `create_batch`-Call-Sites über `modules/master_data.py`, `crm.py`, `project.py`, `mrp.py`, `accounting.py`, `hr.py`, `recruiting.py` verteilt — inkl. `mrp.bom`-BOM-Lines via `bom_line_ids: [(0,0,...)]` inline im Create, und `accounting`-Vendor-Bills via einmaligem `action_post`-Batch statt `post_invoices` pro Bill. Getestet in `test_master_data_unit.py`, `test_crm_batch_unit.py`, `test_project_batch_unit.py`, `test_mrp_batch_unit.py`, `test_accounting_batch_unit.py`, `test_hr_batch_unit.py`, `test_recruiting_batch_unit.py` (je Pattern 5 + Call-Count-Assertion).

### D4 ✅ Erledigt (2026-08-28, als Teil von Sprint S9) — `gui.py` aufteilen

**Anders gelöst als unten skizziert:** Statt `gui.py` in ein `gui/`-Paket zu zerlegen,
wurde die Datei mit S9 **gelöscht**. Die wiederverwendbaren Teile liegen jetzt
framework-frei neben den Modulen: `connect_service.py` (Screen-2-Checkliste, inkl.
`feature_flags` und der Abfrage vorhandener Stammdaten) und `run_config.py`
(Payload→`DemoCriteria`/`ModuleSelections`-Abbildung, `WANTED_MODULES`, `MODULE_LABELS`,
Fortschritts-Reihenfolge, Pre-Flight-Zahlen). Der Widget-Zustand in Closures, den die
Zielstruktur unten entwirren sollte, existiert damit nicht mehr. Ursprünglicher Entwurf zur
Nachvollziehbarkeit:

1136 Zeilen, vier Screens, Formular-Zustand in Closures. Ziel-Struktur:

```
gui/
  app.py            # App-Klasse, Screen-Wechsel, geteilter Zustand
  screen_connect.py # Screen 1 + 2
  screen_config.py  # Screen 3 (inkl. _sub_* Panels)
  screen_run.py     # Screen 4
  widgets.py        # _spin_row, _section_label, Slider-Row-Helper
```

Die dreifach duplizierte Slider+Prozentlabel-Konstruktion (`v_act_past`, `v_act_today`, `v_validate_pct`, `past_future`) wird ein `_pct_slider_row(parent, label, default) -> IntVar`-Helper in `widgets.py`.

### D7 ✅ Erledigt (2026-08-28, als Teil von Sprint S9) — Lauf-Markierung & Aufräum-Funktion

Umgesetzt in `run_journal.py` als die unten selbst empfohlene, modellunabhängige Variante:
`RunJournal` schreibt nach jedem Create ein `seeds/runs/<run_id>.json` mit allen
`(model, id)`-Paaren (eager persistiert — der Sinn ist, einen Prozess zu überleben, der
mitten in der Pipeline stirbt), `JournalingClient` ist eine dünne `OdooJson2Client`-Unterklasse,
die `create`/`create_batch` überschreibt (🔒 bleibt unberührt), und `delete_run()` arbeitet
das Journal rückwärts ab. **Prinzipbedingt Best-Effort:** Odoo verweigert das Löschen
gebuchter Belege, ihrer Partner/Produkte und fakturierter Zeiterfassungen; `delete_run`
storniert vorher, überspringt Wizard-Datensätze und meldet jede Verweigerung pro Modell.
Ursprüngliches Konzept:

Generierte Daten sind nicht von echten Daten unterscheidbar. Für Demo-Systeme ist "alles vom letzten Lauf löschen" die meistgewünschte Funktion.

**Konzept:**
- Pro Lauf eine Run-ID (`demo-20260720-1432`)
- `res.partner`/`crm.lead`/`project.project`: Tag/Kategorie mit Run-ID (`category_id` / `tag_ids`), wo kein Tag-Feld existiert: `ref`-/`x_`-Feld oder zentrales Journal `seeds/runs/<run_id>.json` mit allen erstellten `(model, id)`-Paaren (einfachste, modellunabhängige Lösung — Datei schreibt der Orchestrator ohnehin fast nebenbei)
- Neuer GUI-Screen/Knopf "Letzten Lauf löschen": Journal rückwärts abarbeiten (Reihenfolge invers zur Pipeline!), `unlink` mit try/except pro Modell

## 4. Weiterentwicklung / Roadmap
### R2 ✅ Erledigt (2026-08-28, als Sprint S8) — Purchase-Modul

Vendor Bills entstanden vorher ohne Bestellhistorie. Neu (`modules/purchase.py`):
Lieferanten (geteilter Pool über `RunContext.supplier_ids` mit `accounting.py`) →
`purchase.order` (Komponenten aus `ctx.component_ids`) → `button_confirm`¹ →
`action_create_invoice`¹ (Fallback: manueller `account.move`-Nachbau, gleiches
Per-Order-Isolationsmuster wie R8s `create_invoices_from_orders`) → `odoo_actions.
post_invoices`. Kein Wareneingangs-Validierungsschritt (`stock.picking`) diesen Sprint —
eine bestätigte PO erscheint bereits nativ als offener Wareneingang in Odoos Inventory-App,
ausreichend für Demo-Wert.
¹ Live gegen `demo-pahu-test1.odoo.com` bestätigt: `button_confirm` (stabiler, versionsunabhängiger
Odoo-Core-Name, anders als `sale.order`s `action_confirm`), `action_create_invoice` (öffentlich,
Bill über `invoice_ids`-Reverse-Link lesbar). Live-Fund: `action_create_invoice` lässt
`invoice_date` unbelegt — `action_post` scheitert ohne expliziten Write davor (siehe
CLAUDE.md „Verified field gotchas").

### R3 ✅ Erledigt (2026-08-28, als Sprint S8) — Lager/Inventory

Neu (`modules/inventory.py`): für `storables`/MRP-Komponenten (`modules/mrp.py` setzt
`is_storable=True` auf Komponenten/Rohteile seit diesem Sprint — vorher nicht lagerfähig)
`stock.quant` mit `inventory_quantity` gesät, `action_apply_inventory`¹ angewendet.
Unabhängig von R2 (keine geteilten Felder, kein Doppelzählungsrisiko).
¹ Live bestätigt. `stock.quant.company_id`/`in_date` sind laut `fields_get` `readonly`,
runden aber beim `create()` trotzdem korrekt durch (kein `ir.attachment.datas`-artiger
Silent-Drop).

**Beide gemeinsam — Plan-Review-Fund vor Implementierung:** der 2026-08-04-Erstentwurf sah
`ModuleSelections.stock_avg_qty: int` vor; da `orchestrator.py`s Modul-Gate
`ModuleSelections.get(module_code)` reines `getattr` ist und `module_code="stock"` lautet,
hätte das Feld nie gegriffen — R3 wäre auf jedem Lauf still übersprungen worden. Gefixt
durch dict-Form (`stock: dict = {"avg_qty": int}`), analog `mrp`/`documents`.

**Live-Fund während der Umsetzung (von keinem der beiden Pläne vorhergesehen):**
`RunContext.company_ids` enthält trotz des Namens `res.partner`-IDs, nie eine echte
`res.company`-ID (siehe CLAUDE.md „Verified field gotchas" für Details) — `ctx.company_ids[0]`
als `company_id` hätte `get_default_warehouse` für beide neuen Module immer `None` liefern
lassen. Neuer `odoo_actions.get_main_company_id()`-Helper behebt es; `mrp.py` hat denselben
fehlerhaften Zugriff bereits länger (durch try/except maskiert), nicht in diesem Sprint
gefixt (siehe CLAUDE.md „Current Sprint").

Details, Plan-Review-Verlauf (zwei Durchläufe, gleiches Verfahren wie S5-S7) und Testzahlen:
`CLAUDE.md` „Current Sprint" S8-Block.

### R9 ✅ Erledigt (2026-08-28, als Sprint S9) — Webserver-Deployment statt Desktop-Wizard

**Hinzugefügt:** 2026-08-28, als eigenständiges Roadmap-Item nachgetragen — S9 wurde
umgesetzt, bevor `IMPLEMENTIERUNGSPLAN.md` überhaupt eine Zeile dazu hatte (Nachtrag ist
Teil des Sprints selbst).

Ziel: Das Werkzeug läuft als Webanwendung, `gui.py` (CustomTkinter) entfällt vollständig —
nicht parallel weitergepflegt. Ausgangspunkt war ein vom Nutzer gelieferter HTML-Entwurf
(`demodatenkonsole.html`, 1716 Zeilen), der die API-Oberfläche festlegte; das Backend wurde
daraus abgeleitet, nicht unabhängig entworfen.

**Umfang (eine Auslieferung):** Kern-Extraktion (**D4**), FastAPI-App, geteilter Zugangscode,
sitzungsgebundene Zugangsdaten (nur Arbeitsspeicher), Job-Queue mit Lauf-Journal (**D7**),
SSE-Fortschritt, Guard A + Guard B, CSP/CSRF, lauf-gebundenes Logging, Cache-Pfad und
-Atomarität, Abhängigkeits-Pinning, Docker-Compose mit `local`/`server`-Profil sowie die
S8-Carry-overs (`purchase`/`stock` in `WANTED_MODULES`, `feature_flags` im Connect-Ergebnis).

**Bewusst nicht enthalten:** Datensatz-Vorschau und -Bearbeitung samt Backend-Gerüst. Der
erste Entwurf hatte einen Zwei-Phasen-Lauf (PLAN → prüfen → COMMIT) auf einem
aufzeichnenden Client als Rückgrat. Die Peer-Review deckte auf, dass dessen Lesepolitik
**still** fehlschlägt: ein Modul, das schreibt und dann zurückliest, bekommt `[]` — also
genau den Pattern-5-Skip-Pfad, den jedes Modul implementiert. Nachgewiesene Kaskaden:
`mrp.py:20-30 get_product_template_id` → `None` → alle Hauptprodukte übersprungen →
`ctx.component_ids` leer → `purchase.py:142` überspringt das ganze Modul und
`accounting.py:371` tauscht still seinen Produkt-Pool; `inventory.py:50-58` fehlt der
`or candidate_ids`-Fallback, den `sale.py:59` hat; `documents.py` bezieht 100 % seiner
Eingabe aus Rückleseoperationen. Die Reparatur hätte einen Write-Through-Lesecache plus
Plan/Live-Aufspaltungen in 7–8 Modul-Dateien bedeutet.

**Warum der Schnitt richtig war** (die Begründung wird erfahrungsgemäß erneut vorgeschlagen):
Odoo ist selbst der bessere Datensatz-Browser; Ziel ist ohnehin eine Wegwerf-`demo-*`-DB,
in der ein schlechter Lauf einen erneuten Lauf kostet; und seit S7/R8 entstehen die
interessantesten Datensätze (Rechnungen, automatisch erzeugte Projekte/Aufgaben, gelieferte
Mengen) **nativ in Odoo** — eine Vorschau hätte die einfache Hälfte gezeigt und für die
schwierige nach Odoo verwiesen. Der Sicherheitsfall („schreibe ich in die falsche
Datenbank?") ist vollständig durch Guard A plus die Pre-Flight-Zusammenfassung abgedeckt.

**Zwei unabhängige URL-Guards, gleiches Regex, verschiedene Zwecke, beide Pflicht:**
- **Guard A (falsches Ziel):** nur `^demo-[a-z0-9-]+\.odoo\.com$`. Das
  API-Key-Rechtemodell ersetzt das **nicht** — ein Schlüssel trägt die Rechte seines
  Erstellers, und Berater haben Schreibrechte auf Kundenproduktivsysteme.
- **Guard B (SSRF):** der Server stellt die Anfrage selbst, aus dem Heimnetz des Betreibers.
  Zusätzlich: nur https, keine eingebetteten Zugangsdaten, kein Port-Override, **keine
  Weiterleitungen** (drei `session.post`-Aufrufstellen in `odoo_client.py` — `requests.Session`
  hat **kein** `allow_redirects`-Attribut, ein Session-weites Setzen wäre ein stiller No-op)
  und Redaktion des Fehlerkörpers an **beiden** Kopien (Log **und** `self.errors`, das die
  Lauf-Zusammenfassungs-API speist).

**LLM-/Kundendaten-Invariante — korrigiert:** die frühere Behauptung „genau ein Pfad schickt
Inhalte der Zieldatenbank an ein LLM" war **falsch**. `crm.py:187 _fetch_partner_names` und
`documents.py:129/141` reichen ebenfalls gelesene Werte in Prompts. Die Compliance-Absicht
bleibt (diese Datensätze hat *derselbe Lauf* erzeugt, anders als die Branchen-Vorbefüllung,
die vorhandene Kundendaten las), aber die korrekte Formulierung lautet: *„kein Wert aus
einem Datensatz, den dieser Lauf nicht selbst erzeugt hat, erreicht einen Prompt."* Das
braucht Provenienz-Verfolgung und ist **kein** billiger CI-Check —
`determine_industry_from_company_name` wurde entfernt, die Invariante selbst bleibt offen.

**Live gefundene Punkte (nicht vom Plan vorhergesehen):**
1. Groq hat `llama-3.3-70b-versatile` außer Dienst gestellt — der Repo-Default liefert 404.
   Neuer Default `qwen/qwen3.8-27b` (`openai/gpt-oss-120b` bricht die JSON-Antworten ab).
2. Odoo SaaS stellt manchen Fehlermeldungen unsichtbare Zeichen voran (Zero-Width-Joiner,
   Variation Selectors, Tag-Zeichen — eine Tracing-Wasserzeichnung), die die eigentliche
   Meldung im Log unlesbar machen. `odoo_client._printable` filtert sie.
3. Odoos JSON/2-Fehlerobjekt ist `{"name","message","arguments","timestamp","context","debug"}` —
   `debug` enthält einen vollständigen serverseitigen Traceback mit Dateipfaden. Die
   Redaktion übernimmt ausschließlich `message`.
4. `call_method` läuft eine Payload-Format-Fallback-Kette ab, sodass die schließlich
   geworfene Ausnahme die **letzte, uninformativste** ist („422 Client Error") statt der
   ersten („You can not delete a confirmed sales order"). `run_journal.delete_run` liest
   deshalb den ersten neu aufgezeichneten Fehler aus `client.errors`.
5. Das D7-Aufräumen ist prinzipbedingt Best-Effort: Odoo verweigert das Löschen gebuchter
   Belege, ihrer Partner und Produkte, bereits fakturierter Zeiterfassungen und alles
   Weitere unter seinem Prüfpfad. `delete_run` storniert vorher (`action_cancel`/
   `button_draft`+`button_cancel`), überspringt Wizard-Datensätze und meldet jede
   Verweigerung pro Modell, statt abzubrechen.

**Offen / bewusst außerhalb:** Provenienz-Invariante (siehe oben), Odoo-Modul-Paketierung,
Mehrbenutzer-Konten/SSO (vetoiert), unternehmensseitiger LLM-Schlüssel (nur Naht vorgesehen,
nichts gebaut), S5 Tier 2.

### R8 ✅ Erledigt (2026-08-05, als Sprint S7) — Prozessketten-Verknüpfung fehlte (kein durchgängiger Demo-Faden)

**Hinzugefügt:** 2026-08-04, aus erstem R7-Spike-Durchlauf. **Umgesetzt:** 2026-08-05 als
Sprint **S7** — Sprint-Reihenfolge wurde außerhalb dieser Session neu priorisiert:
Prozessketten-Kontinuität ist Voraussetzung für R2/R3, nicht parallel dazu, daher rückte R8
auf S7 vor und Purchase+Inventory (R2/R3) auf **S8** (siehe §5-Tabelle unten und Draft-Plan
`/Users/paul/.claude/plans/continue-implementation-with-the-woolly-toast.md`).

**Kurswechsel während der Planung (wichtig für künftige ähnliche Aufgaben):** der erste
Plan-Entwurf markierte genau *ein* "Hero"-Serviceprodukt pro Lauf und fädelte 5 neue
`hero_*`-IDs durch `RunContext`/Testsuite, inkl. eines von Hand nachgebauten Reverse-Links
für `account.move.line.sale_line_ids` (readonly). Nutzer-Feedback nach Plan-Review: wenn der
Odoo-Mechanismus für ein Produkt funktioniert, funktioniert er für alle — kein Grund, einen
einzelnen Datensatz zu privilegieren statt das zugrunde liegende Verhalten für jedes
Serviceprodukt und jeden Auftrag zu beheben. Die finale, umgesetzte Version wendet den Fix
**universell** an und ist dadurch *kleiner* als die Hero-Version (keine neuen
`RunContext`-Felder, `sale.py`/`config.py` komplett unangetastet) — siehe
[[feedback_native_over_manual]] (Claude-Memory) für das daraus abgeleitete, dauerhafte
Prinzip.

**Umsetzung (Kernmechanismus):**
- `modules/master_data.py._create_products`: **jedes** Serviceprodukt bekommt
  `service_tracking='task_in_project'`, `invoice_policy='delivery'`,
  `service_type='timesheet'` — gated auf `'project' in ctx.installed_modules and
  'hr_timesheet' in ctx.installed_modules` (Installations-, keine Lauf-Auswahl-Frage, da
  `'task_in_project'` ohne die `project`-App kein gültiger `service_tracking`-Wert ist).
  Damit erzeugt Odoo bei `action_confirm` automatisch Projekt+Aufgabe pro Service-Zeile —
  **kein neuer Pipeline-Schritt nötig**, der Mechanismus hängt am bereits produktiv
  genutzten `action_confirm`-Aufruf.
- `modules/project.py.create_timesheet_data`: fragt `sale.order.line`s mit gesetztem
  `task_id` aus `ctx.confirmed_order_ids` ab ("billable lines") und bedient das
  `hr_timesheet`-Budget zuerst daraus (`so_line`/`task_id`/`project_id` gesetzt — treibt
  Odoos eigene `qty_delivered`-Berechnung), Rest-Budget füllt den bestehenden
  Zufalls-Pool aus `ctx.project_ids` unverändert auf. Nebenbefund/Fix: der alte
  Early-Return (`not ctx.project_ids`) hätte Zeiterfassung bei "Zeiterfassung+Verkauf aber
  nicht Projekte" komplett übersprungen, obwohl billable Tasks existieren — behoben.
- `modules/accounting.py.create_invoices_from_orders`: fakturiert jetzt **pro Auftrag** über
  Odoos eigenen `sale.advance.payment.inv`-Wizard (`advance_payment_method='delivered'`,
  öffentliche `create_invoices`-Methode — dieselbe, die der "Rechnung erstellen"-Button in
  Odoo öffnet) statt manuellem `account.move`-Nachbau. Der Wizard respektiert pro Zeile die
  jeweilige `invoice_policy` (delivery vs. order) und setzt `sale_line_ids` nativ — der alte
  manuelle Aufbau bleibt als `_create_invoices_from_orders_manual` reiner Fallback pro
  Auftrag (nicht Batch-weit — ein Auftrag ohne fakturierbare Menge darf nicht alle anderen
  in den Fallback mitziehen, live bestätigt: ein Wizard-Aufruf mit nur einem leeren Auftrag
  wirft eine Exception, ein Batch mit mind. einem echten Auftrag daneben nicht).
- `orchestrator.py` 🔒: `module_order` umsortiert zu `mrp → crm → sale → hr → project →
  hr_timesheet → account → hr_recruitment → documents` (war: `account` an Position 4) —
  eine Service-Zeile braucht ihre Zeiterfassung, bevor sie fakturiert werden kann.

**Live-Spike-Befunde (11 Checks, alle bestanden, `demo-pahu-test1.odoo.com` saas-19.4):**
`service_tracking`/`invoice_policy` allein reichen **nicht** — `product.product.service_type
='timesheet'` ist das dritte, unabhängige Feld, das `qty_delivered_method` tatsächlich auf
`'timesheet'` setzt (leicht zu übersehen, keines der drei Felder folgt zwingend aus den
anderen beiden). Zwei Service-Zeilen auf demselben Auftrag teilen sich EIN
Odoo-generiertes Projekt (unterschiedliche Aufgaben) — kein Duplikat-Projekt pro Zeile.
`account.move.line.sale_line_ids` ist readonly (gleiche Fallenklasse wie
`ir.attachment.datas`/`stock.quant.quantity`) — der Wizard setzt ihn serverseitig, kein
manueller `write` nötig.

**Datenerzeugungs-Audit (auf Nutzer-Anfrage, vor Plan-Freigabe):** alle `client.create`/
`create_batch`-Aufrufstellen der 9 implementierten Module gegen bekannte Odoo-native
Automatiken geprüft. `modules/mrp.py` explizit auditiert: keine Kollisionsgefahr, da nie
`route_ids` (Fertigungsroute) auf Produkten gesetzt wird — Auftragsbestätigung eines
MRP-Fertigprodukts löst deshalb heute keine native Fertigungsauftrags-Automatik aus, die mit
`create_mrp_data`s eigener manueller MO-Erstellung kollidieren könnte. Bulk-Projekte
(`create_project_data`) und auftragsgetriebene Projekte koexistieren bewusst (getrennter
ID-Raum, unterschiedlicher Zweck) — keine Duplizierung.

Getestet: 157/157 Unit- + 65/65 Live-Integration-Schritte grün (inkl. 7 neuer R8-Schritte:
Produkt-Tagging, geteiltes Projekt bei zwei Service-Zeilen, billable-lines-first
Zeiterfassung, Wizard-Fakturierung mit exaktem Mengen- und `sale_line_ids`-Abgleich).

Befund: ein realer KI-generierter Demo-Plan (Quote-to-Cash-Workbook, R7-Spike) erwartet
einen einzelnen durchgängigen Faden Opportunity → Order → Projekt → Aufgabe →
Zeiterfassung → Rechnung → Bankabgleich. Der Generator erzeugt pro Modul unabhängige
Bulk-Daten ohne Cross-Modul-Verknüpfung:

- `service_tracking` auf Serviceprodukten wird nirgends gesetzt (0 Treffer im Repo) —
  Odoo-native Automatik "Produkt → Projekt/Aufgabe bei Auftragsbestätigung" greift nicht.
- `create_project_data` (`modules/project.py`) erzeugt Projekte ohne jede Verknüpfung zu
  `sale.order`/`crm.lead`/Partner.
- Timesheets landen auf zufälligen, unverbundenen Projekten (`project.py:179`,
  `i % len(ctx.project_ids)`).

Betrifft **nicht nur R7** — dieselbe Lücke existiert identisch bei rein manueller
GUI-Bedienung, kein Regler behebt sie. Ist Voraussetzung dafür, dass irgendein Plan
(KI-generiert oder manuell) einen präsentierbaren Einzel-Ablauf liefert statt nur
statistisch plausibler Bulk-Daten.

Aufwand: groß. Mind. nötig: (a) `service_tracking`/`invoice_policy`-Konfiguration auf
erzeugten Serviceprodukten, (b) optionale Sale→Project-Verknüpfung (Odoo-native Automatik
für mind. einen "Hero"-Datensatz pro Lauf nutzen statt der unabhängigen Zufallslogik in
`create_project_data`), (c) ggf. Timesheet→Task→Order-Linie für Delivered-Qty-Invoicing.
Nicht Teil von S3/S4 — eigene Aufwandsschätzung vor Einplanung nötig.


### R10 ✅ Live-Testphase-Feedback (Sprint S10, abgeschlossen 2026-08-29)

Erste Live-Testphase der S9-Webkonsole auf `demo-test5`. Neun Feedbacks (F1–F9), gesammelt
vor jeder Umsetzung. Vollständiger Plan mit Umsetzungsdetail:
`/Users/paul/.claude/plans/moonlit-napping-whale.md`.

**Phase A — Korrektheit** (peer-reviewed durch fremden Opus-Agenten, nur Plantext +
Live-Repo, kein Konversationskontext — Verfahren wie S5–S8; 10 Blocker + 11
Should-fix-Befunde eingearbeitet):

- **F6 / WP1 — Zugriffsproben auf die richtige Operation.** `get_enabled_features` prüft
  *Lesbarkeit*, die Module machen aber `create`; auf `demo-test5` meldete die Sonde
  `mrp_routings = True` trotz ausgeschaltetem Einstellungshaken, der Lauf startete und
  scheiterte garantiert. Neu: `POST /json/2/<model>/has_access {"ids": [], "operation":
  "create"}` (live verifiziert; `check_access_rights` existiert nicht mehr, `operation` muss
  benannter Payload-Schlüssel sein, `args=[…]` scheitert). Ein Aufruf deckt drei heute
  getrennt behandelte Fälle ab: Modul nicht installiert · Einstellungshaken aus · Benutzer
  ohne Rechtegruppe. **Nur eine eindeutige Antwort ergibt `False`** — 429/5xx/Timeout →
  `True` plus WARNING, sonst schaltet ein Rate-Limit während der Sondierung ganze Module
  still ab (B1-Fehlerklasse durch die eigene Korrektur wieder eingeführt).
  Zweiter, unabhängiger Teil: `hr.leave`/`hr.work.entry.type` stammen aus
  `hr_holidays`/`hr_work_entry`, nicht aus `hr` — bisher ohne jede Sonde, Ursache der
  Live-Fehler [9]–[14].
- **F7 / WP2 — Fehlerbericht entrauschen.** 8 von 14 gemeldeten Fehlern waren keine: die
  Fallback-Kette probiert 3 Pfadformen × 2 Schrägstrich-Varianten, jeder echte Fehler
  erschien ~6×, geplante 404-Abtastungen als eigene Fehler. Künftig ein Eintrag pro
  gescheiterter *logischer Operation*, mit der ersten strukturierten Odoo-Meldung statt des
  letzten, nichtssagenden 422.
- **F8 — Payload-Form merken: zurückgestellt, erst messen.** Die Review zeigte, dass ein
  Memo die 🔒-gesperrte Kettenreihenfolge ändern müsste und der Nutzen unbelegt ist (auf dem
  Erfolgspfad gewinnt bereits das erste Kettenglied). Die belegte Verschwendung liegt in
  `check_field_compatibility`: 17 Modelle, 6 POSTs je nicht installiertem Modell — wird in
  S10 gegated. WP2 liefert den Versuchszähler als Messgrundlage.
- **F9 / WP3 — `mrp.py`s `ctx.company_ids`-Bug** (`:282`, `:334`) vorgezogen, weil WP1
  dieselbe Datei anfasst. Nicht „zwei Zeilen": mit einer echten `res.company`-ID liefert
  `get_manufacturing_picking_type_id` erstmals wieder einen Wert, wodurch `mrp.py:339-394`
  nach längerer Zeit wieder live läuft — neue Live-Befunde einplanen.

**Phase A — Status: ✅ abgeschlossen (2026-08-29).** Alle WP1–WP3 umgesetzt exakt wie oben
und im vollen Plan detailliert; 294/294 Unit-Schritte grün (von 237). Live-Integrationslauf
gegen `demo-test5` bestätigte das Kernproblem live: `hr_holidays`/`hr_work_entry` stehen dort
tatsächlich auf `state=uninstalled` — genau der von F6 vermutete Fall. Die neue Sonde/das neue
Gate reagieren korrekt (sauberer Skip statt 404), einzig `tests/integration/test_hr.py` rief
die low-level Urlaubs-Helfer bisher ungegatet auf und musste selbst ein
`ctx.installed_modules`-Gate bekommen (nicht im ursprünglichen Plan vorgesehen, live entdeckt).
71/76 Live-Integrationsschritte grün; die verbleibenden 5 sind ein **vorbestehender, von S10
unabhängiger** Bug — `hr.job.payment_interval` existiert auf `demo-test5` nicht,
`modules/recruiting.py` wurde in S10 nicht angefasst — als eigene Aufgabe ausgelagert
(siehe CLAUDE.md, Abschnitt „Verified field gotchas" für den Befund; Fix nicht Teil von S10).
Details, Live-Diagnose und die neuen `has_access`/`/call/`-Erkenntnisse: CLAUDE.md Sprint-S10-
Block und „Odoo API Conventions".

**Phase B — Status: ✅ abgeschlossen (2026-08-29).** Peer-reviewed (fremder Opus-Agent,
Plantext + Live-Repo nach dem Phase-A-Merge, keine Konversationshistorie — gleiches
Verfahren wie Phase A) vor der Umsetzung; 6 Blocker + 9 Should-fix-Befunde eingearbeitet,
u. a.: die Weiter/Nav-Sperre gatet auf `data.ok`, nicht auf „alle Schritte grün" (sonst
schwärzt ein einzelner nicht-fataler roter Schritt die ganze Konsole statt nur das
betroffene Modul zu deaktivieren); die Sperre ist ein Latch (`state.everConnected`), nicht
Live-Zustand (sonst sperrt ein fehlgeschlagener Re-Connect während eines laufenden Laufs
die Generierungsansicht und den Löschen-Knopf aus); `config.ini.example` behält `db` als
optionalen Wert (sonst `KeyError` in `tests/integration/test_suite.py`/`test_mrp_live.py`,
die ihn bisher hart lasen); die PDF-Determinismus-Umsetzung nutzt einen lokalen
`random.Random`, nie `random.seed()` (sonst kontaminiert ein globales Reseed die
nachfolgende CV-PDF-Zufallsziehung im selben Modul). Vollständige Liste im Plan
(`/Users/paul/.claude/plans/moonlit-napping-whale.md`, „Was die Review geändert hat",
Punkte 25–42). Die Review markierte WP5 als größten Einzelposten des Sprints — größer als
Phase A insgesamt — und WP7 als zweitgrößten; beide Einschätzungen bestätigten sich in der
Umsetzung.

- **F2 / WP4** ✅ — Datenbankname entfällt als Eingabefeld; `web/security.
  derive_database_name()` leitet ihn aus dem ersten Host-Label der validierten URL ab.
  Dreigliedrige Kette (Body → `server_config.apply("db", …)` → Ableitung), nicht zwei
  Glieder — der mittlere Link bleibt die Selbst-Hoster-Fluchttür. Live im Status-Streifen
  sichtbar (`connect_service.ConnectResult.database`), da sonst kein Ort mehr existiert, an
  dem die aufgelöste Datenbank angezeigt wird.
- **F3 + F5 / WP5** ✅ — „Weiter zur Konfiguration" und die Nav-Punkte Konfiguration/
  Generierung bleiben gesperrt, bis `data.ok` (Odoo **und** LLM erreichbar) einmal grün war
  — ein Latch, kein Live-Zustand. Ansicht 03 „Prüfen" entfällt vollständig (drei statt vier
  Ansichten); ihre Inhalte (aktive Module, Datensatz-Aufschlüsselung inkl. Gesamtsumme, der
  Hinweis auf Odoos eigene Zusatz-Datensätze) leben jetzt als eigene, live aktualisierte
  Panels direkt in der Konfigurationsansicht, entprellt (400 ms) bei jeder Eingabeänderung
  über das weiterhin rein arithmetische `POST /api/preflight`. Ein einzelner „Lauf
  starten"-Knopf ersetzt die vormals zwei Knöpfe/Handler.
- **F1 / WP6** ✅ — Einstiegs-Tutorial als Overlay (`.overlay`/`.overlay-card`, `z-index`
  über der `position: sticky`-Rail), erscheint nach erfolgreichem Login statt beim bloßen
  Seitenaufruf (die referenzierten Felder sind vorher nicht sichtbar), „?"-Knopf in der
  Rail-Fußzeile zum erneuten Öffnen — bewusst außerhalb der Nav-Sperre, damit die Anleitung
  auch vor einer Verbindung erreichbar bleibt. Der genaue Odoo-Menüpfad zum API-Schlüssel
  blieb unverifiziert (kein Login-Zugriff auf die Zielinstanz) und steht als vorsichtig
  formulierter Fließtext, kein erratener Deep-Link.
- **F4 / WP7** ✅ — 5 Layout-Presets (Schriftfamilie × Kopf-/Tabellen-/Fußzeilenstil,
  `pdf_factory._VARIANTS`), deterministisch je Lieferant über `zlib.crc32` — **nicht**
  `hash()` (prozessweit randomisiert) und **nicht** `random.seed()` (kontaminiert den
  globalen Generator). Spaltenbreiten/Kürzung sind pro Schriftfamilie kalibriert (Courier
  ist als Monospace breiter pro Zeichen als Helvetica/Times); die Seitenzahl-Fußzeile nutzt
  eine echte `FPDF.footer()`-Unterklasse statt eines Inline-Schreibversuchs. Live per
  Sichtprüfung bestätigt (zwei Varianten als PDF gerendert und visuell verglichen) —
  sichtbar unterschiedliche Layouts, gleicher Lieferant zweimal ergab dasselbe Layout.
  `modules/documents.py`s Aufrufstelle berechnet und übergibt `footer_info`
  (`data_factory.build_vendor_footer_info`) — `pdf_factory.py` bleibt bewusst frei von
  `data_factory`-Kopplung.

301/301 Unit- (von 294), 71/76 Live-Integrationsschritte grün — dieselben 5 vorbestehenden,
unabhängigen Fehlschläge wie in Phase A (siehe oben), keine neuen.

Offen und ausdrücklich nicht in S10: R6, R7, S5 Tier 2, Provenienz-Invariante als
CI-Prüfung, F8 (siehe oben), sowie der vorbestehende `hr.job.payment_interval`-Bug in
`modules/recruiting.py` (ausgelagert, siehe CLAUDE.md „Verified field gotchas" — **seitdem
behoben, siehe Nachtrag unten**).

**Nachtrag — `hr.job.payment_interval`-Bug behoben (2026-08-29).** Die ursprüngliche Diagnose
war falsch: kein Feldschema-Mismatch, sondern `hr_recruitment` selbst steht auf `demo-test5`
auf `state=uninstalled` (live per `ir.module.module` bestätigt) — dieselbe Fehlerklasse wie F6
(`hr_holidays`/`hr_work_entry`), nur ohne eigene Sonde bis jetzt unbemerkt, weil
`modules/recruiting.py` selbst nie angefasst wurde. `orchestrator.py:75` gatet
`create_recruiting_data` bereits korrekt auf `"hr_recruitment" in ctx.installed_modules` —
Produktivcode war nie betroffen. Der Bug saß ausschließlich in zwei Live-Integrationstests, die
die low-level Recruiting-Helfer ungegatet aufriefen: `tests/integration/test_recruiting.py`
(Schritte 1/2/4/7) bekam das `ctx.installed_modules`-Gate, das `test_hr.py` in Phase A für
Urlaubsdaten bereits etabliert hatte — eine zweite Prüfung dieses Fixes zeigte, dass
`tests/integration/test_documents.py`s Setup (Bewerber-Erzeugung fürs P2-CV-PDF-Fixture)
denselben ungegateten Aufruf unabhängig ein zweites Mal enthielt und ebenfalls das Gate
brauchte. 306/306 Unit-, 79/79 Live-Integrationsschritte grün (die 3 zusätzlichen Schritte
ggü. den 76 aus Phase A/B sind `test_documents.py`s P1/P2/Pattern-5-Schritte, die durch den
frühen `return` in der defekten Setup-Exception vorher nie gezählt wurden). Separat, zeitgleich
in einer parallelen Session gemerged: CI-Lint-Infrastruktur (`ruff.toml`,
`.github/workflows/ci.yml`, inzwischen auch ein `bandit`-Job) — eigener Commit, nicht Teil
dieses Fixes.

**⚠️ Implementierungshinweis für R12/R18/R19 (aus dem Peer-Review, gilt für jedes neue
Modul):** ein neues Odoo-Feature braucht **fünf** Registrierungspunkte, nicht zwei —
`run_config.py`s `WANTED_MODULES`/`MODULE_LABELS`/`MODULE_RUN_ORDER`/`build_selections`,
`odoo_actions.py`s `MODEL_ACCESS_PROBES`/`PRIMARY_MODEL_PER_MODULE`, sowie die
Frontend-Modulkarte (`static/`). Jeden dieser fünf auslassen heißt: Modul läuft nie, ohne
sichtbaren Fehler (exakt die B1/S8-`stock_avg_qty`-Fehlerklasse, nur eine Stufe höher).
R12 umgeht das bewusst, indem es in ein bestehendes Modul einzieht statt ein eigenes zu
werden — R18 und R19 werden echte neue Module und brauchen alle fünf Punkte.

