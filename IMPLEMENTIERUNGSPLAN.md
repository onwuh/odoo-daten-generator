# Implementierungsplan — odoo-daten-generator

**Stand:** 2026-07-20 · **Basis:** Code-Review aller Kernmodule (gui.py, orchestrator.py, llm_service.py, odoo_client.py, modules/*, tests/*)

Dieses Dokument ist der Implementierungsrahmen für die nächsten Entwicklungszyklen. Es gliedert sich in:

1. [Leitprinzip: LLM-Minimalismus](#1-leitprinzip-llm-minimalismus)
2. [Bugs & Logikfehler](#2-bugs--logikfehler) (priorisiert, mit konkretem Fix)
3. [Architektur- & Design-Verbesserungen](#3-architektur--design-verbesserungen)
4. [Weiterentwicklung / Roadmap](#4-weiterentwicklung--roadmap) (inkl. PDF-Generierung)
5. [Umsetzungsreihenfolge](#5-umsetzungsreihenfolge)

Kennzeichnung: 🔴 kritisch · 🟠 hoch · 🟡 mittel · ⚪ niedrig · 🔒 = berührt "Do Not Touch"-Bereich aus CLAUDE.md → Architekten-Freigabe erforderlich.

---

## 1. Leitprinzip: LLM-Minimalismus

**Maxime:** Das LLM liefert ausschließlich *atomare kreative Bausteine* (Namen, Bezeichnungen, Textkörper), niemals fertige Importstrukturen. Alles Strukturelle — Adressen-Zusammenbau, E-Mails, Telefonnummern, Preise, Mengen, Datumswerte, Record-Verschachtelung — wird deterministisch im Code erzeugt.

**Begründung:**
- Weniger Output-Tokens (Kosten, Latenz, Timeout-Risiko)
- Keine invaliden Felder mehr aus dem LLM (das aktuelle Filtern von `uom`, `vat`, `detailed_type` etc. wird überflüssig)
- Strukturfehler (fehlende Pflichtfelder, falsche Kontakt-Typen) unmöglich, weil Struktur im Code liegt
- Reproduzierbarkeit und Testbarkeit steigen (Struktur ist unit-testbar ohne LLM)

### 1.1 Ist-Analyse der LLM-Calls

| Call (`llm_service.py`) | Liefert heute | Bewertung |
|---|---|---|
| `fetch_creative_data` (Z. 183) | **Komplette Importstruktur**: Firmen mit voller Adresse, verschachtelte Kontakte (delivery/invoice/contact) mit Adressen, Produkte mit Preisen | ❌ Hauptverstoß gegen die Maxime — umbauen (→ 1.2) |
| `fetch_recruiting_data` (Z. 272) | Jobtitel ✅, Kandidatennamen ✅, **E-Mails ❌, Telefonnummern ❌**, Skill-Taxonomie ✅ | Teilverstoß: E-Mails/Telefone sind aus Namen ableitbar bzw. rein zufällig generierbar |
| `fetch_name_suggestions` | Namensbanken (atomar) | ✅ konform |
| `fetch_job_summaries_batch` | Fließtext-Beschreibungen | ✅ echte Kreativleistung, behalten |
| `fetch_all_project_stages` | Phasennamen-Sets | ✅ konform |
| `fetch_workcenter_data` | Stationsnamen + Beschreibung + Operationsnamen | ✅ konform (atomar genug) |
| `fetch_crm_chatter_messages` | E-Mail-/Notiztexte | ✅ echte Kreativleistung, behalten |
| `fetch_all_bom_components` | Komponentennamen | ✅ konform |
| `determine_industry_from_company_name` | Ein Wort | ✅ konform |

### 1.2 Arbeitspaket A1 — `fetch_creative_data` ersetzen 🟠

**Neu:** `fetch_creative_atoms(criteria)` liefert nur noch:

```json
{
  "company_names": ["...", "..."],
  "street_names": ["Industriestraße", "Am Technologiepark", "..."],
  "product_names": {"services": [...], "consumables": [...], "storables": [...]},
  "product_descriptions": {"Produktname": "1 Satz Beschreibung"}
}
```

(Optional lassen sich `street_names` sogar aus einer statischen Liste ziehen — dann entfällt auch das.)

**Neu:** Modul `data_factory.py` (kein LLM-Zugriff!) baut daraus die Records:

```python
# data_factory.py — deterministische Record-Assemblierung
def build_company(name: str, street: str, city_entry: dict) -> dict:
    """city_entry aus static_data.CITIES: {"city": "Köln", "zip_prefix": "50", "country_code": "DE"}"""
    return {
        "name": name,
        "street": f"{street} {random.randint(1, 199)}",
        "zip": f"{city_entry['zip_prefix']}{random.randint(100, 999)}",
        "city": city_entry["city"],
        "email": _email_from_name(name),          # "info@<slug>.example.com"
        "phone": _phone_for_country(city_entry),  # "+49 221 ..."
        "website": f"https://www.{_slug(name)}.example.com",
        "is_company": True,
    }

def build_contacts(company: dict, n_delivery, n_invoice, n_other, name_bank) -> list:
    """Kontaktstruktur ist reine Regel-Logik — heute steht sie als Prosa im Prompt (llm_service.py Z. 193-204)."""
    ...
```

**Neu:** `static_data.py` mit konsistenten Stadt/PLZ-Paaren (DACH, ~50 Einträge), Vorwahlen, Straßen-Fallbacks. Wichtig: PLZ muss zur Stadt passen — genau das kann eine statische Tabelle garantieren, das LLM nicht zuverlässig.

**Preise:** vollständig in `data_factory` (die Logik existiert bereits als Fallback in `master_data.py:47-50` — sie wird zur einzigen Quelle).

**Erwartete Ersparnis:** ~70–80 % der Output-Tokens des größten Calls; `_INVALID_PRODUCT_FIELDS`-Filterung und `vals.pop('vat')`-Kaskaden in `master_data.py` entfallen.

**Tests (Pflicht, gem. Testing Design Patterns):**
- Unit: `build_company` liefert nur valide Felder (Abgleich gegen Whitelist), PLZ passt zum City-Entry
- Unit: Pattern 2 (LLM `None`/`{}` → Fallback auf statische Namen, kein Crash)
- Integration: Pattern 4 Read-Back auf `res.partner` (street/zip/city gesetzt)

### 1.3 Arbeitspaket A2 — Recruiting-Prompt verschlanken 🟡

`fetch_recruiting_data`: Felder `candidate_emails` und `candidate_phones` aus dem Prompt entfernen. Stattdessen in `recruiting.py`:

```python
def _email_from_name(name: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '.', name.lower()).strip('.')
    return f"{slug}@example.com"

def _random_phone_de() -> str:
    return f"+49 {random.randint(150, 179)} {random.randint(1000000, 9999999)}"
```

Die Fallback-Auffüllung in `_create_applicants` (Z. 330-335) macht das für Fehlfälle bereits genau so — es wird zur Hauptlogik.

### 1.4 Arbeitspaket A3 — Cache-Konsistenz ⚪

CLAUDE.md-Konvention: "always check cache before LLM call". Heute gecacht: `name_suggestions`, `job_summaries`. Nicht gecacht: `recruiting_data`, `workcenter_data`, `project_stages`, `bom_components`.

- `workcenter_data`, `project_stages`, `bom_components`: cachen (Key: industry + language + Parameter-Hash + `_PROMPT_VERSION`) 🔒 *Seed-Cache-Namenskonvention beachten*
- `chatter_messages`: bewusst **nicht** cachen (Varianz erwünscht) — als Kommentar im Code dokumentieren
- `creative_atoms`: Namenslisten cachen, Assemblierung ist eh im Code

---

## 2. Bugs & Logikfehler

### B1 🔴 `gui.py:360` — Feature-Flags werden nie erkannt

```python
self.feature_flags = odoo_actions.get_enabled_features(self.client)
```

`get_enabled_features(client, installed_modules=None)` überspringt **alle** Probes, wenn `installed_modules` leer ist (`odoo_actions.py:58`: `installed = installed_modules or set()`). Die GUI übergibt den Parameter nicht → `feature_flags` ist immer `{}`.

**Folgen:**
- Leads-Spinner in `_sub_crm` (gui.py:549) erscheint nie, auch wenn `crm.use_lead` aktiv ist
- Arbeitszentren-Spinner und Qualitäts-Checkbox in `_sub_mrp` (gui.py:741, 751) erscheinen nie
- Inkonsistenz: `mrp.py:225` nutzt `ctx.feature_flags.get('mrp_routings', True)` mit Default `True` → Arbeitszentren werden trotzdem erstellt, aber mit `max(1, 0) = 1` statt der gewünschten Anzahl

**Fix:** `self.feature_flags = odoo_actions.get_enabled_features(self.client, mods)` (die Testsuite macht es in `test_suite.py:152` bereits richtig).
**Test:** Unit-Test mit Mock-Client: `get_enabled_features(client, {"crm", "mrp"})` ruft Probes auf; ohne Set keine Calls (Pattern 3).

### B2 🔴 `llm_service.py:104` — Timeout blockiert trotzdem

```python
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
    future = executor.submit(self._raw_call, prompt)
    try:
        text, ... = future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        ...
```

Der `with`-Block ruft beim Verlassen `shutdown(wait=True)` auf → nach einem `TimeoutError` **blockiert der Kontext-Exit, bis der hängende HTTP-Call doch fertig ist**. Der Timeout wartet also faktisch nicht ab, sondern nur die Fehlermeldung ist früher da.

**Fix:**

```python
executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
future = executor.submit(self._raw_call, prompt)
try:
    text, in_tok, out_tok = future.result(timeout=timeout)
except concurrent.futures.TimeoutError:
    msg = f"timed out after {timeout}s"
finally:
    executor.shutdown(wait=False, cancel_futures=True)  # Thread läuft ggf. aus, blockiert aber nicht
```

**Zusatz:** Timeouts sind aktuell nicht retry-fähig (`"timed out"` matcht keinen `_RETRYABLE_HINTS`-Eintrag). Entscheiden: entweder `"timed out"` in die Hints aufnehmen oder bewusst dokumentieren.
**Test:** Unit-Test mit gemocktem `_raw_call`, der `time.sleep` simuliert — `_call` muss innerhalb Toleranz zurückkehren.

### B3 🔴 `llm_service.py:367` + `llm_service.py:510` — ZeroDivisionError bei leerem LLM-Dict

```python
if isinstance(data, dict):
    sets = list(data.values())
    return {name: sets[i % len(sets)] for i, name in enumerate(project_names)}
```

LLM liefert `{}` → `isinstance` besteht → `len(sets) == 0` → `i % 0` → **ZeroDivisionError**. Betroffen: `fetch_all_project_stages` und `fetch_all_bom_components` (`fetch_workcenter_data` hat den Guard `len(data) >= 1` korrekt).

**Fix:** `if isinstance(data, dict) and data:` in beiden Funktionen.
**Test:** Pattern 2 — `mock._call_json.return_value = {}` → Rückgabe `{}`, kein Raise.

### B4 🟠 `accounting.py:148` — Banktransaktionen duplizieren sich bei Wiederholungsläufen

`create_bank_transactions_for_all_invoices` sucht **alle** gebuchten Rechnungen der Datenbank (`state = posted`, ohne Lauf-Eingrenzung). Zweiter Generator-Lauf → für sämtliche Alt-Rechnungen entstehen erneut Bank-Transaktionen. Zusätzlich wird bei existierendem Statement (`Z. 227-233`) `balance_start` hart auf `0.0` überschrieben, obwohl das Statement schon Zeilen hat → Salden inkonsistent.

**Fix:**
1. Nur Rechnungen dieses Laufs verwenden: `create_invoices_from_orders` und `create_vendor_bill` geben IDs bereits zurück → in `ctx` sammeln (`ctx.invoice_ids`, `ctx.bill_ids` — 🔒 Config-Schema-Erweiterung) und an die Funktion übergeben.
2. Bestehendes Statement: `balance_end_real` additiv fortschreiben (`bisheriges balance_end_real + Summe neuer Zeilen`), `balance_start` unangetastet lassen.

**Test:** Integration — zwei Aufrufe hintereinander; Assert: Anzahl Statement-Lines wächst nur um die neuen Rechnungen.

### B5 🟠 `hr.py` — Urlaub über Jahresgrenze scheitert an der Allocation

`create_leave_allocation` (Z. 47-48) begrenzt auf `{year}-01-01 … {year}-12-31`. `_random_future_monday` streut aber bis `timescale_days` (GUI erlaubt bis 730 Tage!) in die Zukunft → Anträge im Folgejahr liegen außerhalb der Allocation → `action_approve` schlägt fehl (heute: nur Print, Datenbestand unvollständig).

**Fix:** Allocation-Zeitraum aus dem tatsächlichen Streufenster ableiten:

```python
horizon_end = today + datetime.timedelta(days=timescale_days + 14)
# Variante A: eine Allocation pro betroffenem Jahr
# Variante B (einfacher): date_to = horizon_end, date_from = today - timedelta(days=timescale_days)
```

Variante B empfohlen (eine Allocation, deckt Fenster komplett ab).
**Test:** Integration mit `timescale_days=400` — Leave im Folgejahr wird erstellt **und** genehmigt (Read-Back `state == 'validate'`, Pattern 4).

### B6 🟠 `project.py:117-122` — `random.sample` zerstört Phasen-Reihenfolge

```python
selected = random.sample(stages, k=num_stages)
```

`random.sample` liefert eine zufällige *Reihenfolge* — die logische Workflow-Progression ("Kickoff → … → Abnahme"), die das LLM extra generiert, wird zerwürfelt und per `sequence=seq*10` falsch einsortiert (z. B. "Deployment" vor "Kickoff").

**Fix:** Teilmenge ziehen, Original-Reihenfolge bewahren:

```python
idx = sorted(random.sample(range(len(stages)), k=num_stages))
selected = [stages[i] for i in idx]
```

**Test:** Unit — `random.seed(42)`, Assert: Reihenfolge der Auswahl entspricht Reihenfolge der Quellliste.

### B7 ✅ Erledigt — `accounting.py` — Mindestens 10 Eingangsrechnungen, immer

War: `num_bills = max(10, num_invoices // 2)` erzwang immer ≥10 Vendor Bills. Core-Bug bereits am 2026-08-03/04 behoben (`max(1, num_invoices // 2)`); GUI-Konfigurierbarkeit (`ModuleSelections.account_bills: Optional[int] = None`, GUI-Feld "Anzahl Eingangsrechnungen" in `_sub_account`, min 0) im S4-Folgesprint ergänzt — `num_bills` wird jetzt vor dem `purchase_pool`-Gate berechnet, damit ein `0`-Override auch `_create_suppliers` überspringt. Getestet in `tests/unit/test_accounting_batch_unit.py` (B7-Tests) und `tests/integration/test_accounting.py` (Step 9, Pattern 4 Read-Back).

### B8 ✅ Erledigt — `sale.py` — Bestätigung hart auf 5 Aufträge begrenzt

War: `orders_to_confirm = ctx.order_ids[:max(1, min(5, len(ctx.order_ids)))]` bestätigte bei 200 Aufträgen genau 5. Core-Bug bereits am 2026-08-03/04 behoben (`_DEFAULT_CONFIRM_PCT = 65`-Konstante, skaliert mit Auftragsanzahl); GUI-Konfigurierbarkeit (`ModuleSelections.sale_confirm_pct: int = 65`, GUI-Slider "Bestätigt (%)" in `_sub_sale`, analog `validate_pct`) im S4-Folgesprint ergänzt — die Modul-Konstante ist entfernt, `sale.py` liest `ctx.module_selections.sale_confirm_pct`. Getestet in `tests/unit/test_sale_unit.py` und `tests/integration/test_sale.py` (Step 7, Pattern 4 Read-Back).

### B9 🟡 `crm.py:271-278` — Chatter-Teilnehmernamen nur von der ersten Opportunity

`participants` wird aus `opp_data[0]` gebaut und gilt für den **gesamten** Batch-Prompt → das LLM grüßt in allen Opportunities denselben Kunden/Verkäufer, obwohl `partner_name` pro Opp bekannt ist. Zusätzlich: `random.choice(opp_titles_bank)` (Z. 126) erzeugt Duplikat-Titel → `messages_by_title` (Dict!) liefert für gleichnamige Opps identische Konversationen.

**Fix:**
1. Titel ohne Zurücklegen vergeben (`random.sample`, bei Bedarf Suffix "– {Partnername}") → Titel eindeutig und Kundenspezifisch.
2. Prompt-Format auf Liste von Objekten umstellen: `[{"title": ..., "customer": ..., "salesperson": ...}, ...]`, Antwort keyed by Titel. Ein Call bleibt ein Call (Batch-Regel eingehalten).

**Test:** Unit — Pattern 8 (call_count == 1) bleibt; Assert: Titel im Request eindeutig.

### B10 ✅ Erledigt (dokumentiert, kein Code-Change) — `installed_modules` enthielt *ausgewählte*, nicht installierte Module

War: `installed_modules=selected_modules if mode_val == "both" else set()` — Namenskonflation zwischen "installiert" und "ausgewählt". Core-Bug bereits am 2026-08-03/04 behoben: `gui.py` befüllt `ctx.installed_modules` wieder aus `self.installed_modules` (dem echten Screen-2-Odoo-Probe); nicht ausgewählte Module bleiben bei ihrem `ModuleSelections`-Default (0/leeres dict), sodass das bestehende Truthiness-Gate im Orchestrator sie korrekt überspringt (verifiziert u. a. durch den bestehenden "B10 Pattern 4"-Test in `test_accounting_batch_unit.py`).

Architekten-Review (2026-08-04, S4-Folgesprint) hat den ursprünglich vorgeschlagenen Mechanismus (`RunContext.selected_modules: Set[str]` + explizites Orchestrator-Gate) bewusst **nicht** umgesetzt: kein Modul liest `selected_modules` heute, ein zusätzliches 🔒-Config-Schema-Feld ohne Konsument wäre totes Gewicht, und ein explizites Zweit-Gate (Option 2) würde eine zweite "was soll laufen"-Eingabe einführen, die mit den bestehenden Zählfeldern nicht zwangsläufig synchron bleibt — ein Aufrufer, der Zählwerte setzt aber `selected_modules` vergisst, würde still gar nichts ausführen. Bewusste Entscheidung gegen weiteren Umbau; siehe Sprint-S4-Notiz unten.

### B11 ✅ Erledigt — `odoo_client.py` — Letzter `call_method`-Fallback wirft Argumente weg 🔒

War: Fallback 3 postete `{}` (nur Context) unabhängig vom Inhalt von `args`/`kwargs`/`ids`. Behoben (2026-08-03/04): Guard `if ids or args or kwargs: raise` (odoo_client.py, in `call_method`) lässt Fallback 3 nur noch feuern, wenn wirklich nichts zu senden war. Getestet in `tests/unit/test_odoo_client_unit.py`.

### B12 🟡 `crm.py:116` — Verkäufer-Zuordnung hängt an Chatter-Option

```python
sales_users = _fetch_sales_users(client) if ctx.module_selections.crm_chatter else []
```

`user_id` (Verkäufer) auf Opportunities wird nur gesetzt, wenn Chatter aktiviert ist — sachfremde Kopplung.
**Fix:** `sales_users` immer laden (ein Call, billig); Chatter-Flag steuert nur die Nachrichtengenerierung.

### B13 🟡 `recruiting.py:253-258` — Skill-Level-Duplikate bei existierenden Skill-Typen

Existiert der Skill-Typ bereits, werden Skills **und Levels trotzdem neu angelegt** → bei jedem Lauf wachsen Duplikat-Levels ("Anfänger", "Anfänger", …).
**Fix:** Level-Erstellung nur im `else`-Zweig (neuer Typ); für existierende Typen `fetch_skill_levels_map` nutzen. Nebenbefund: `levels[:max(3, len(levels))]` ist ein No-Op — entfernen.
**Test:** Integration — zweimaliger Lauf, Assert: Level-Anzahl pro Typ konstant.

### B14 ✅ Erledigt — `sale.py` — Order↔Opportunity-Verknüpfung ignoriert Partner

War: `zip(ctx.order_ids, ctx.opportunity_ids)` verknüpfte positionsweise. Behoben (2026-08-03/04): `create_sale_data` gruppiert Opportunities nach `partner_id` und ordnet jeder Order nur eine Opportunity desselben Kunden zu (kein Match → keine Verknüpfung). Getestet in `test_sale_unit.py` und `tests/integration/test_sale.py` (Step 6, bewusst umgekehrte Opp-Reihenfolge zur Regressionsprüfung gegen positionsbasiertes `zip`).

### B15 ✅ Erledigt — `mrp.py` — `max(1, num_workcenters)` machte 0 unmöglich

War: `num_workcenters = max(1, int(...))` erzwang ≥1 auch bei deaktivierten Routings. Behoben (2026-08-03/04): `max(0, ...)`. Getestet in `tests/unit/test_mrp_batch_unit.py`.

### B16 ⚪ `crm.py:52` — toter Code / unklare Präzedenz

`(company_ids * 2)[:len(company_ids) * 2]` — der Slice ist ein No-Op. Und `crm.py:46` `return early or [stages[0]["id"]] if stages else []` funktioniert nur wegen Operator-Präzedenz korrekt — Klammern setzen: `return (early or [stages[0]["id"]]) if stages else []`.

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

### D5 🟡 Typisierte Modul-Configs statt roher Dicts 🔒

`ModuleSelections.mrp/hr_recruitment/hr_timeoff/crm_chatter/crm_activities` sind untypisierte Dicts mit Shape-Kommentaren. Fehlerklasse: Tippfehler im Key fällt erst zur Laufzeit (oder nie) auf.

**Fix (nach Architekten-Freigabe, da Config-Schema):** je ein Dataclass (`MrpConfig`, `TimeoffConfig`, …) mit Defaults; `ModuleSelections` referenziert `Optional[MrpConfig]`. GUI erzeugt die Objekte direkt. `enabled`-Bools entfallen (Objekt vorhanden = aktiv).

### D6 🟡 Namens-Hygiene: `gemini` → `llm`

Parameter heißt in allen Modulsignaturen `gemini`, Provider ist primär Groq; `RunContext.gemini_model_name` ist ungenutztes Erbe. Umbenennen (`llm`, `llm_model_name`), rein mechanisch.

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

### D8 ⚪ Kleinigkeiten

- `test_mrp_live.py` im Wurzelverzeichnis → nach `tests/integration/` verschieben oder löschen
- `odoo_client._post:46`: `response is not None` ist im `except requests.HTTPError` immer wahr — vereinfachen 🔒
- `gui.py` Screen 2 nutzt `self.llm._call(...)` (privat) für den Verbindungstest → öffentliche Methode `LLMService.ping()` einführen
- Provider-Erkennung `llm_key.startswith("gsk_")` (gui.py:383): explizites Dropdown "Groq / Gemini" ist robuster
- `orchestrator.run`: `fetch_name_suggestions` auch im reinen Stammdaten-Modus ausgeführt — nur laden, wenn Module aktiv sind oder Fallbacks es brauchen

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

### R4 ⚪ Weitere Kandidaten (Reihenfolge nach Nachfrage)

- **Helpdesk**: Tickets mit Chatter (Wiederverwendung der Chatter-Pipeline aus CRM)
- **Produktbilder**: Platzhalter-Generierung (farbige Initialen-Kacheln) oder Bild-API; Upload via `image_1920`
- **Szenario-Presets**: JSON-Presets ("Maschinenbau 50 MA", "IT-Agentur klein") die alle GUI-Regler vorbelegen — ein Klick statt 30
- **Headless-Modus**: `python3 generate.py --preset maschinenbau.json` für CI/Wiederholbarkeit (Orchestrator ist bereits GUI-frei, fehlt nur ein CLI-Einstieg)
- **Mehr-Firmen-Support**: aktuell implizit Company 1 (`odoo_actions.get_main_company_name`)

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

### R7 🟡 KI-generierter Demo-Plan als JSON-Eingabe (Spike vor Umsetzung)

**Hinzugefügt:** 2026-08-04, aus POC-Gespräch zur Machbarkeit.

Ziel: Demo-Vorbereitung läuft heute oft schon KI-gestützt ab (Ablaufplan/Storyline wird
von einer KI erstellt). Idee: dieselbe KI (Gem in Gemini) liefert zusätzlich zum Ablaufplan
ein JSON, das `DemoCriteria`+`ModuleSelections` gemäß `PLAN_JSON_SCHEMA.md` befüllt. Nutzer
fügt es in Screen 3 (Konfiguration) in ein Textfeld ein, Parser befüllt die **bestehenden**
Regler (kein Bypass), Fehler/unbekannte Keys → Warnhinweis, kein Abbruch, Nutzer korrigiert
direkt an den vorbefüllten Reglern nach. Nichts wird gespeichert (Einwegkonfiguration pro Demo).

Abgrenzung zu R4-Szenario-Presets: Presets sind kuratiert + dauerhaft im Repo; R7 ist
KI-generiert + ephemeral pro Demo-Termin. Nur der Lade-/Parsing-Mechanismus wird geteilt.

Nicht installierte Module: werden wie heute in Screen 3 ausgegraut/deaktiviert (kein
Auto-Install per API — `ir.module.module.button_immediate_install` wäre technisch möglich,
aber braucht `base.group_system`-Rechte auf dem API-Key und hat dauerhafte Nebenwirkungen
auf der Live-Instanz — bewusst nicht automatisiert). Stattdessen: Reload-Button auf Screen 3,
der nach manueller Installation durch den Nutzer (im Odoo-Backend) die Modul-/Feature-Flag-
Erkennung erneut ausführt und den Screen mit den bereits eingegebenen Werten (Snapshot vor
Rebuild wegen `_clear()`) neu aufbaut.

**Vor Umsetzung: Spike Pflicht**, siehe `PLAN_JSON_SCHEMA.md` Abschnitt 5. Offene Frage ist
nicht die GUI-Mechanik (unkritisch, nutzt bestehende Screen-3-Struktur), sondern ob der
Gem-Output zuverlässig genug ist. Exit-Kriterium: braucht ein typischer Gem-Plan mehr
Nachkorrekturen an den Reglern als er Klicks spart, gewinnt manuelle Eingabe — Feature
entfällt.

Aufwand GUI-Teil (falls Spike positiv): klein-mittel, kein 🔒-Konflikt (nur `gui.py`,
Config-Schema/Orchestrator-Reihenfolge unverändert). Nicht Teil von S3/S4, eigener Slot
nach Spike-Ergebnis einplanen.

**Erster Spike-Durchlauf (2026-08-04):** ein Gem-generierter Plan (IT-Systemhaus
Quote-to-Cash-Workbook) gegen Schema geprüft — JSON-Parsing sauber (0 unbekannte Keys,
korrekte Typen, `crm_chatter`-Falle vermieden), zwei Nachkorrekturen nötig (`hr_timesheet`
als Toggle statt Gesamtzahl missverstanden, `hr` fürs Workbook-Detail "Techniker zuweisen"
vergessen) — für den Exit-Test ein Pass. Dabei aber größerer Folgefund aufgedeckt, siehe R8.

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

---

## 5. Umsetzungsreihenfolge

Jedes Paket endet mit grüner `test_suite.py` gegen die Live-Instanz (CLAUDE.md-Pflicht). Empfohlene Sprints:

| Sprint | Inhalt | Begründung |
|---|---|---|
| **S1 — Bugfixes kritisch** | B1, B2, B3 (+ B16 als Beifang) | Kleine, isolierte Fixes; B1 schaltet verlorene Features frei |
| **S2 — Datenqualität** | B4, B5, B6, B9, B12, B13 | Sichtbare Qualität der Demo-Daten; keine Strukturänderungen |
| **S3 — LLM-Minimalismus** | A1 (`data_factory` + `static_data`), A2, A3 | Kern-Maxime; baut auf stabilem Fundament aus S1/S2 |
| **S4 — Architektur** ✅ | D1, D2, D3, B11, B14, B15 (2026-08-03/04); B7/B8 GUI-Config-Felder + B10-Architekten-Entscheidung (2026-08-04, Folgesprint) | Callback + Logging + Batching vor weiterem Feature-Ausbau — abgeschlossen |
| **S5 — API-Versions-Schicht (R5), Tier 1** ✅ | Versions-Erkennung (`get_server_version`), `fields_get`-Warnliste (`check_field_compatibility`) (2026-08-04) | Beide ohne 🔒-Berührung, unabhängig testbar; siehe R5-Statusblock für die Tier-2-Zurückstellungs-Begründung |
| S5 Tier 2 (zurückgestellt) | `api_versions/*.json`, Client-Adapter (🔒) | Erst mit einem echten, belegten Rename zwischen zwei Live-Versionen — siehe R5 |
| **S6 — PDF (R1/P1+P2)** ✅ | `pdf_factory`, `modules/documents`, GUI-Optionen, `RunContext.applicant_ids` (Voraussetzungs-Fix in `recruiting.py`) (2026-08-04) | Erster Roadmap-Ausbau, größter Demo-Effekt — siehe CLAUDE.md „Current Sprint" für Peer-Review-Ergebnis und den live gefundenen `ir.attachment`-Feldnamen-Bug |
| **S7 — Prozessketten-Kontinuität (R8)** ✅ | Universelles Service-Produkt-Tagging, billable-lines-first Zeiterfassung, Wizard-basierte Fakturierung, `orchestrator.py`-Reorder 🔒 (2026-08-05) | Umnummeriert von "S7 = Purchase+Inventory" — Prozessketten-Kontinuität ist Voraussetzung, nicht parallel; siehe R8-Statusblock oben für Details, Peer-Review-Verlauf (2× fremder Opus-Agent, Plan+Repo-Kontext) und den Hero→Universal-Kurswechsel |
| **S8 — Purchase + Inventory (R2, R3)** ✅ | `modules/purchase.py`, `modules/inventory.py` (neu), `odoo_actions.py`-Erweiterung, `orchestrator.py`-Anhang 🔒 (2026-08-28) | War ursprünglich S7; siehe R2/R3-Statusblöcke oben für Details, zwei Peer-Review-Durchläufe (Plan-Agent + fremder Cold-Review-Agent, gleiches Verfahren wie S5-S7) und live gefundene Bugs (`ctx.company_ids`-Namenskollision, `action_create_invoice`s fehlendes `invoice_date`) |
| **S9 — Webserver-Deployment (R9)** ✅ | `web/` (FastAPI, Guards, Session, Queue, SSE), `connect_service.py`/`run_config.py` (D4), `run_journal.py` (D7), `static/` (index/app.js/app.css), Docker-Compose, `gui.py` gelöscht (2026-08-28) | Ersetzt den Aufrufer, nicht die Pipeline — `orchestrator.py` bleibt unberührt (kein `mode`-Parameter, 🔒 nicht angefasst). Siehe R9-Statusblock oben für den gestrichenen Vorschau-Umfang, die korrigierte LLM-Invariante und die fünf live gefundenen Punkte |

**Pro Arbeitspaket verbindlich** (aus CLAUDE.md Testing Design Patterns):
- Empty-Pool-Guards (P1) für jede neue `random.choice/sample`-Stelle
- LLM-None-Guards (P2) für jeden neuen/geänderten LLM-Pfad
- Feature-Flag-Skip (P3) für jede neue GUI-Option
- Read-Back-Validierung (P4) in jedem neuen Integrationsschritt
- 🔒-Punkte (Pipeline-Reihenfolge, JSON2-Fallbacks, Config-Schema, Cache-Namen) vor Umsetzung explizit freigeben lassen
