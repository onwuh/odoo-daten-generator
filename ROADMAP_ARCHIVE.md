# Roadmap-Archiv — erledigte Punkte

Abgeschlossene Bugs/Design-Punkte/Roadmap-Items aus `ROADMAP.md`, hier archiviert um die Hauptdatei klein zu halten. Inhalt unverändert aus `ROADMAP.md` übernommen (Stand vor Archivierung: 2026-09-02). Sprint-für-Sprint-Narrativ steht separat in `odoo-daten-generator/SPRINT_LOG.md`.

---

## 1. Leitprinzip: LLM-Minimalismus

### 1.1 Ist-Analyse der LLM-Calls

_(Analyse-Grundlage für A1-A3, alle drei inzwischen umgesetzt — siehe unten.)_

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


### 1.2 Arbeitspaket A1 — `fetch_creative_data` ersetzen ✅ Erledigt

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


**Verifiziert 2026-09-02:** `llm_service.py:266 fetch_creative_atoms` (ersetzt `fetch_creative_data` vollständig), `data_factory.py` (`build_company`/`build_contacts`/`build_products`), `static_data.py`, genutzt von `modules/master_data.py` — behoben.

### 1.3 Arbeitspaket A2 — Recruiting-Prompt verschlanken ✅ Erledigt

`fetch_recruiting_data`: Felder `candidate_emails` und `candidate_phones` aus dem Prompt entfernen. Stattdessen in `recruiting.py`:

```python
def _email_from_name(name: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '.', name.lower()).strip('.')
    return f"{slug}@example.com"

def _random_phone_de() -> str:
    return f"+49 {random.randint(150, 179)} {random.randint(1000000, 9999999)}"
```

Die Fallback-Auffüllung in `_create_applicants` (Z. 330-335) macht das für Fehlfälle bereits genau so — es wird zur Hauptlogik.


**Verifiziert 2026-09-02:** `fetch_recruiting_data`-Prompt enthält keine `candidate_emails`/`candidate_phones`-Keys mehr; `modules/recruiting.py` erzeugt beides lokal (`text_utils.email_from_name`, `_random_phone_de`) — behoben.

### 1.4 Arbeitspaket A3 — Cache-Konsistenz ✅ Erledigt

CLAUDE.md-Konvention: "always check cache before LLM call". Heute gecacht: `name_suggestions`, `job_summaries`. Nicht gecacht: `recruiting_data`, `workcenter_data`, `project_stages`, `bom_components`.

- `workcenter_data`, `project_stages`, `bom_components`: cachen (Key: industry + language + Parameter-Hash + `_PROMPT_VERSION`) 🔒 *Seed-Cache-Namenskonvention beachten*
- `chatter_messages`: bewusst **nicht** cachen (Varianz erwünscht) — als Kommentar im Code dokumentieren
- `creative_atoms`: Namenslisten cachen, Assemblierung ist eh im Code

---


**Verifiziert 2026-09-02:** `workcenter_data`/`project_stages`/`bom_components`/`creative_atoms` laufen alle über `_cached_llm_call`; `fetch_crm_chatter_messages` ist explizit unkgecacht mit Begründungskommentar — behoben.

## 2. Bugs & Logikfehler

### B1 ✅ Erledigt — `gui.py:360` — Feature-Flags werden nie erkannt

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

**Verifiziert 2026-09-02:** `connect_service.py:182` übergibt `mods` an `get_enabled_features` — behoben.

### B2 ✅ Erledigt — `llm_service.py:104` — Timeout blockiert trotzdem

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

**Verifiziert 2026-09-02:** `llm_service.py:136-151` — kein `with`-Block mehr, `executor.shutdown(wait=False, cancel_futures=True)` explizit — behoben.

### B3 ✅ Erledigt — `llm_service.py:367` + `llm_service.py:510` — ZeroDivisionError bei leerem LLM-Dict

```python
if isinstance(data, dict):
    sets = list(data.values())
    return {name: sets[i % len(sets)] for i, name in enumerate(project_names)}
```

LLM liefert `{}` → `isinstance` besteht → `len(sets) == 0` → `i % 0` → **ZeroDivisionError**. Betroffen: `fetch_all_project_stages` und `fetch_all_bom_components` (`fetch_workcenter_data` hat den Guard `len(data) >= 1` korrekt).

**Fix:** `if isinstance(data, dict) and data:` in beiden Funktionen.
**Test:** Pattern 2 — `mock._call_json.return_value = {}` → Rückgabe `{}`, kein Raise.

**Verifiziert 2026-09-02:** `llm_service.py:437,446,602,611` — `if isinstance(data, dict) and data:` plus zweiter `if not data: return {}`-Guard vor der Indizierung in beiden Funktionen — behoben.

### B4 ✅ Erledigt — `accounting.py:148` — Banktransaktionen duplizieren sich bei Wiederholungsläufen

`create_bank_transactions_for_all_invoices` sucht **alle** gebuchten Rechnungen der Datenbank (`state = posted`, ohne Lauf-Eingrenzung). Zweiter Generator-Lauf → für sämtliche Alt-Rechnungen entstehen erneut Bank-Transaktionen. Zusätzlich wird bei existierendem Statement (`Z. 227-233`) `balance_start` hart auf `0.0` überschrieben, obwohl das Statement schon Zeilen hat → Salden inkonsistent.

**Fix:**
1. Nur Rechnungen dieses Laufs verwenden: `create_invoices_from_orders` und `create_vendor_bill` geben IDs bereits zurück → in `ctx` sammeln (`ctx.invoice_ids`, `ctx.bill_ids` — 🔒 Config-Schema-Erweiterung) und an die Funktion übergeben.
2. Bestehendes Statement: `balance_end_real` additiv fortschreiben (`bisheriges balance_end_real + Summe neuer Zeilen`), `balance_start` unangetastet lassen.

**Test:** Integration — zwei Aufrufe hintereinander; Assert: Anzahl Statement-Lines wächst nur um die neuen Rechnungen.

**Verifiziert 2026-09-02:** `modules/accounting.py:187-295` — `create_bank_transactions_for_all_invoices` nimmt jetzt explizite `invoice_ids`/`bill_ids` (lauf-eingegrenzt statt aller gebuchten DB-Rechnungen), `balance_start` bleibt unangetastet, `balance_end_real` wird additiv fortgeschrieben — behoben.

### B5 ✅ Erledigt — `hr.py` — Urlaub über Jahresgrenze scheitert an der Allocation

`create_leave_allocation` (Z. 47-48) begrenzt auf `{year}-01-01 … {year}-12-31`. `_random_future_monday` streut aber bis `timescale_days` (GUI erlaubt bis 730 Tage!) in die Zukunft → Anträge im Folgejahr liegen außerhalb der Allocation → `action_approve` schlägt fehl (heute: nur Print, Datenbestand unvollständig).

**Fix:** Allocation-Zeitraum aus dem tatsächlichen Streufenster ableiten:

```python
horizon_end = today + datetime.timedelta(days=timescale_days + 14)
# Variante A: eine Allocation pro betroffenem Jahr
# Variante B (einfacher): date_to = horizon_end, date_from = today - timedelta(days=timescale_days)
```

Variante B empfohlen (eine Allocation, deckt Fenster komplett ab).
**Test:** Integration mit `timescale_days=400` — Leave im Folgejahr wird erstellt **und** genehmigt (Read-Back `state == 'validate'`, Pattern 4).

**Verifiziert 2026-09-02:** `modules/hr.py:238-247` — Allocation-Fenster (`alloc_date_from`/`alloc_date_to`) wird aus `timescale_days` abgeleitet statt aus einem festen Kalenderjahr (Variante B aus dem ursprünglichen Fix-Vorschlag) — behoben.

### B6 ✅ Erledigt — `project.py:117-122` — `random.sample` zerstört Phasen-Reihenfolge

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

**Verifiziert 2026-09-02:** `modules/project.py:51` — `sorted(random.sample(range(len(stages)), k=num_stages))` bewahrt die Original-Reihenfolge — behoben.

### B7 ✅ Erledigt — `accounting.py` — Mindestens 10 Eingangsrechnungen, immer

War: `num_bills = max(10, num_invoices // 2)` erzwang immer ≥10 Vendor Bills. Core-Bug bereits am 2026-08-03/04 behoben (`max(1, num_invoices // 2)`); GUI-Konfigurierbarkeit (`ModuleSelections.account_bills: Optional[int] = None`, GUI-Feld "Anzahl Eingangsrechnungen" in `_sub_account`, min 0) im S4-Folgesprint ergänzt — `num_bills` wird jetzt vor dem `purchase_pool`-Gate berechnet, damit ein `0`-Override auch `_create_suppliers` überspringt. Getestet in `tests/unit/test_accounting_batch_unit.py` (B7-Tests) und `tests/integration/test_accounting.py` (Step 9, Pattern 4 Read-Back).

### B8 ✅ Erledigt — `sale.py` — Bestätigung hart auf 5 Aufträge begrenzt

War: `orders_to_confirm = ctx.order_ids[:max(1, min(5, len(ctx.order_ids)))]` bestätigte bei 200 Aufträgen genau 5. Core-Bug bereits am 2026-08-03/04 behoben (`_DEFAULT_CONFIRM_PCT = 65`-Konstante, skaliert mit Auftragsanzahl); GUI-Konfigurierbarkeit (`ModuleSelections.sale_confirm_pct: int = 65`, GUI-Slider "Bestätigt (%)" in `_sub_sale`, analog `validate_pct`) im S4-Folgesprint ergänzt — die Modul-Konstante ist entfernt, `sale.py` liest `ctx.module_selections.sale_confirm_pct`. Getestet in `tests/unit/test_sale_unit.py` und `tests/integration/test_sale.py` (Step 7, Pattern 4 Read-Back).

### B9 ✅ Erledigt — `crm.py:271-278` — Chatter-Teilnehmernamen nur von der ersten Opportunity

`participants` wird aus `opp_data[0]` gebaut und gilt für den **gesamten** Batch-Prompt → das LLM grüßt in allen Opportunities denselben Kunden/Verkäufer, obwohl `partner_name` pro Opp bekannt ist. Zusätzlich: `random.choice(opp_titles_bank)` (Z. 126) erzeugt Duplikat-Titel → `messages_by_title` (Dict!) liefert für gleichnamige Opps identische Konversationen.

**Fix:**
1. Titel ohne Zurücklegen vergeben (`random.sample`, bei Bedarf Suffix "– {Partnername}") → Titel eindeutig und Kundenspezifisch.
2. Prompt-Format auf Liste von Objekten umstellen: `[{"title": ..., "customer": ..., "salesperson": ...}, ...]`, Antwort keyed by Titel. Ein Call bleibt ein Call (Batch-Regel eingehalten).

**Test:** Unit — Pattern 8 (call_count == 1) bleibt; Assert: Titel im Request eindeutig.

**Verifiziert 2026-09-02:** `modules/crm.py:317-325` — ein Customer/Salesperson-Paar pro Opportunity statt eines für den ganzen Batch, plus `_unique_titles` gegen Titel-Duplikate — behoben.

### B10 ✅ Erledigt (dokumentiert, kein Code-Change) — `installed_modules` enthielt *ausgewählte*, nicht installierte Module

War: `installed_modules=selected_modules if mode_val == "both" else set()` — Namenskonflation zwischen "installiert" und "ausgewählt". Core-Bug bereits am 2026-08-03/04 behoben: `gui.py` befüllt `ctx.installed_modules` wieder aus `self.installed_modules` (dem echten Screen-2-Odoo-Probe); nicht ausgewählte Module bleiben bei ihrem `ModuleSelections`-Default (0/leeres dict), sodass das bestehende Truthiness-Gate im Orchestrator sie korrekt überspringt (verifiziert u. a. durch den bestehenden "B10 Pattern 4"-Test in `test_accounting_batch_unit.py`).

Architekten-Review (2026-08-04, S4-Folgesprint) hat den ursprünglich vorgeschlagenen Mechanismus (`RunContext.selected_modules: Set[str]` + explizites Orchestrator-Gate) bewusst **nicht** umgesetzt: kein Modul liest `selected_modules` heute, ein zusätzliches 🔒-Config-Schema-Feld ohne Konsument wäre totes Gewicht, und ein explizites Zweit-Gate (Option 2) würde eine zweite "was soll laufen"-Eingabe einführen, die mit den bestehenden Zählfeldern nicht zwangsläufig synchron bleibt — ein Aufrufer, der Zählwerte setzt aber `selected_modules` vergisst, würde still gar nichts ausführen. Bewusste Entscheidung gegen weiteren Umbau; siehe Sprint-S4-Notiz unten.

### B11 ✅ Erledigt — `odoo_client.py` — Letzter `call_method`-Fallback wirft Argumente weg 🔒

War: Fallback 3 postete `{}` (nur Context) unabhängig vom Inhalt von `args`/`kwargs`/`ids`. Behoben (2026-08-03/04): Guard `if ids or args or kwargs: raise` (odoo_client.py, in `call_method`) lässt Fallback 3 nur noch feuern, wenn wirklich nichts zu senden war. Getestet in `tests/unit/test_odoo_client_unit.py`.

### B12 ✅ Erledigt — `crm.py:116` — Verkäufer-Zuordnung hängt an Chatter-Option

```python
sales_users = _fetch_sales_users(client) if ctx.module_selections.crm_chatter else []
```

`user_id` (Verkäufer) auf Opportunities wird nur gesetzt, wenn Chatter aktiviert ist — sachfremde Kopplung.
**Fix:** `sales_users` immer laden (ein Call, billig); Chatter-Flag steuert nur die Nachrichtengenerierung.

**Verifiziert 2026-09-02:** `modules/crm.py:140` — `sales_users` wird unconditional geladen, nicht mehr an `crm_chatter` gekoppelt — behoben.

### B13 ✅ Erledigt — `recruiting.py:253-258` — Skill-Level-Duplikate bei existierenden Skill-Typen

Existiert der Skill-Typ bereits, werden Skills **und Levels trotzdem neu angelegt** → bei jedem Lauf wachsen Duplikat-Levels ("Anfänger", "Anfänger", …).
**Fix:** Level-Erstellung nur im `else`-Zweig (neuer Typ); für existierende Typen `fetch_skill_levels_map` nutzen. Nebenbefund: `levels[:max(3, len(levels))]` ist ein No-Op — entfernen.
**Test:** Integration — zweimaliger Lauf, Assert: Level-Anzahl pro Typ konstant.

**Verifiziert 2026-09-02:** `modules/recruiting.py:312-327` — Skill-Levels werden nur noch `for entry in new_entries` erzeugt, der No-Op-Slice ist entfernt — behoben.

### B14 ✅ Erledigt — `sale.py` — Order↔Opportunity-Verknüpfung ignoriert Partner

War: `zip(ctx.order_ids, ctx.opportunity_ids)` verknüpfte positionsweise. Behoben (2026-08-03/04): `create_sale_data` gruppiert Opportunities nach `partner_id` und ordnet jeder Order nur eine Opportunity desselben Kunden zu (kein Match → keine Verknüpfung). Getestet in `test_sale_unit.py` und `tests/integration/test_sale.py` (Step 6, bewusst umgekehrte Opp-Reihenfolge zur Regressionsprüfung gegen positionsbasiertes `zip`).

### B15 ✅ Erledigt — `mrp.py` — `max(1, num_workcenters)` machte 0 unmöglich

War: `num_workcenters = max(1, int(...))` erzwang ≥1 auch bei deaktivierten Routings. Behoben (2026-08-03/04): `max(0, ...)`. Getestet in `tests/unit/test_mrp_batch_unit.py`.

### B16 ✅ Erledigt — `crm.py:52` — toter Code / unklare Präzedenz

`(company_ids * 2)[:len(company_ids) * 2]` — der Slice ist ein No-Op. Und `crm.py:46` `return early or [stages[0]["id"]] if stages else []` funktioniert nur wegen Operator-Präzedenz korrekt — Klammern setzen: `return (early or [stages[0]["id"]]) if stages else []`.

**Verifiziert 2026-09-02:** `modules/crm.py:49` — Klammern gesetzt (`(early or [stages[0]["id"]]) if stages else []`); `modules/crm.py:52-59` — toter Slice durch echte `_build_partner_pool`-Funktion (`random.choices` für den Rest) ersetzt — behoben.

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

### D19 ✅ Erledigt (2026-09-05) — ID-Namensraum-Kollision zwischen §3 und den Sprint-Spikes

**Erfasst und geschlossen am 2026-09-05.** §3 vergibt `D1`–`D10` für dauerhafte Architektur-Punkte. Der S16-Architektur-Spike vergab für seine sprint-internen Entscheidungen ebenfalls `D1`–`D15`. Vier Kennungen bedeuteten dadurch je zwei verschiedene Dinge:

| ID | §3 | S16-Spike |
|---|---|---|
| `D5` | Typisierte Modul-Configs | LLM-Atom-Abruf pro Firma |
| `D6` | `gemini` → `llm` | Fortschrittsanzeige-Umbau |
| `D8` | Kleinigkeiten | Bestehende-Firma-Wiederverwendung |
| `D10` | Rate-Limiting | `RunContext`-Lebenszyklus |

**Der Schaden ist bereits eingetreten, nicht hypothetisch:** Commit `f052fe3` heißt "S16-NEU WP2b: res_company_ids field, ctx-aware company helpers, **D8-Ergänzung**". CLAUDE.md verlangt Item-IDs im Commit — hier ist nicht auflösbar, welches D8 gemeint ist (tatsächlich das des Spikes).

**Umgesetzt — bewusst nicht als Massen-Umbenennung.** Der naheliegende Fix (alle ~150 `D<n>`-Vorkommen im S16-Text auf `S16-D<n>` umschreiben) wurde verworfen, nachdem die Prüfung zeigte, dass er den Text beschädigt hätte: drei Stellen im S16-Block meinen tatsächlich **§3s** D10 (Rate-Limiting), nicht S16s D10 (`RunContext`-Lebenszyklus) — ein blindes Ersetzen hätte sie stillschweigend verfälscht. Stattdessen, bei gleichem Nutzen und fünf statt 150 Änderungen:

1. **Lesart-Kopf** über dem archivierten S16-Abschnitt (`ROADMAP_ARCHIVE.md` §5): die `D<n>` dort sind sprint-lokal und meinen `S16-D1`…`S16-D15`, nicht die gleichnamigen §3-Punkte.
2. **Die drei echten Grenzgänger** inline als `§3-D10` qualifiziert.
3. **Vorwärts wirkt die Regel, nicht die Umbenennung:** `CLAUDE.md`s Planning-document rule 2 macht das Präfix `S<N>-D<n>` ab S17 verbindlich. S16 bleibt der letzte Abschnitt ohne — dokumentiert, statt nachträglich umgeschrieben.


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

**Nachtrag (2026-09-02, R19/S12):** dieser Hinweis war beim R19-Cold-Review nicht mehr
auffindbar (`ROADMAP.md` referenzierte ihn nur als "siehe Implementierungshinweis oben" —
stale geworden, als R10 hierher archiviert wurde und die Referenz ihr Ziel verlor). R19s
eigener Cold-Review erweiterte/verifizierte ihn gegen den tatsächlichen Code auf sieben
Punkte (exakte Zeilennummern, plus die `test_run_config_unit.py`-Testabdeckung als Punkt 7)
und schrieb ihn neu als eigenständigen, dauerhaften Abschnitt "Referenz —
Registrierungskette für ein neues orchestriertes Modul" in `ROADMAP.md` §3 — R12/R18
verweisen jetzt dorthin, nicht mehr hierher.

### D9 ✅ Erledigt (2026-09-02, als Teil von Sprint S11 Phase B) — Feedback-Logs: Lauf-Log lokal, Referenz im Issue

Der Feedback-Button (`web/feedback.py`) übertrug bisher nur eine Kurzzusammenfassung ins
GitHub-Issue — bewusst daten-minimiert (Kommentar im Code: "niemals Ziel-URL/Datenbank/
Fehlertext"). Nutzerwunsch: das volle Lauf-Log für Diagnose mitschicken, was direkt mit
dieser Daten-Minimierung kollidiert — ein Log kann die Ziel-Odoo-URL und rohen Odoo-Fehlertext
(potenziell Kundendaten eines Interessenten) enthalten, und Issues landen in einem
**öffentlichen** GitHub-Repo.

**Gelöste Spannung (Nutzerentscheidung, ersetzte einen ursprünglich geplanten
Redaktions-Ansatz):** kein Log-Inhalt geht je ins öffentliche Issue, auch nicht redigiert —
das Log bleibt lokal auf der eigenen Maschine (Self-Hosting ist bereits die
Vertrauensgrenze), das Issue trägt nur die ohnehin schon vorhandene `run_id`-Referenz.

**Umsetzung:** zweiter `logging.FileHandler` über das bestehende
`logging_setup.run_log_capture` (`web/jobs.py._execute`, verschachtelt neben dem
SSE-Handler, `contextlib.nullcontext()` bei nicht anlegbarer Datei). Pfad/Retention über
`run_journal.run_log_path`/`prune_journals` — dasselbe Verzeichnis wie die Run-Journale,
`ODOO_GENERATOR_RUNS_DIR`-Override greift automatisch mit. **Cold-Review-Fund:**
`prune_journals` räumt jetzt `*.json` UND `*.log` in einem Durchgang ab — ein Lauf-Log ist
*stärker* Interessenten-identifizierend als das Journal selbst (`odoo_client._post` loggt
die volle Ziel-URL bei **jedem** Request, nicht nur einmal); ein separater, leicht
vergessener zweiter Retention-Pfad wäre schlimmer gewesen als gar keiner. Schreiben ist
best-effort, gleiche Regel wie `RunJournal._persist`: ein Lauf darf nie daran scheitern,
dass sein eigenes Log nicht angelegt werden konnte.

Getestet: `test_web_api_unit.py` (echter Lauf schreibt sein Log lokal, unbeschreibbares
Verzeichnis → `None`, kein Crash), `test_run_journal_unit.py` (`*.log` im selben
Retention-Durchlauf wie `*.json`), `test_web_feedback_unit.py` (exakte, minimale
Schlüsselmenge `{run_id, status, modules, api_error_count}` im Issue-Kontext).

### R11 ✅ Erledigt (2026-09-02, als Teil von Sprint S12/WP4) — Lost Opportunities (CRM)

`modules/crm.py` erzeugte bis dahin nur aktive/gewonnene Opportunities — kein
Trichter-Realismus, jede Demo-Pipeline sah zu 100% erfolgreich aus. Neu: ein konfigurierbarer
Anteil wird als verloren markiert (`crm.lost.reason` gesucht, nie erzeugt — Referenzdaten-
Konvention wie `modules/mrp.py`s `quality.alert.team`-Suche).

**Sequenzierungs-Falle (vor Umsetzung gefunden, nicht live):** die naheliegende Umsetzung
(lost-Anteil am Ende von `create_crm_data` markieren) wäre falsch gewesen — `sale.py`
verknüpft Aufträge über `search_read('crm.lead', …)`, was `active=False`-Leads unsichtbar
macht, und schreibt danach die Won-Stage auf verknüpfte Leads. Da CRM vor Sales läuft, hätte
ein in `crm.py` bereits verlorener Lead entweder fälschlich doch verknüpft oder von `sale.py`
ignoriert werden können — im schlimmsten Fall verloren *und* Won-staged gleichzeitig. Fix:
eigener, späterer Aufruf **nach** `sale.py`, operiert nur auf den von `sale.py` **nicht**
verknüpften Opportunities.

**Umsetzung:** neues additives `RunContext.linked_opportunity_ids` — `sale.py`s
Verknüpfungsschleife hängt bei jedem erfolgreichen Link den `opp_id` zusätzlich an (kein
extra `search_read` gegen die rate-limitierte Live-Instanz nötig). `modules/crm.py`s neues
`mark_lost_opportunities` berechnet die unverknüpften Leads rein lokal, gruppiert den zu
markierenden Anteil nach zufällig zugewiesenem `lost_reason_id` und schreibt pro Gruppe
gebündelt (`active=False, probability=0, lost_reason_id=X`) — `won_status` ist ein
Compute-Feld und zieht korrekt auf `'lost'` nach (live verifiziert, S12/WP3: kein
`action_set_lost`-Call nötig). Neuer `orchestrator.py`-`module_order`-Eintrag `crm_lost`
direkt nach `sale`, vor `hr` — ein echter Locked-List-Eingriff (Position zwischen zwei
bestehenden Schritten, nicht am Ende), dafür holte diese Session explizite
Architekten-Freigabe ein, statt die vom Cold-Review vorgeschlagene Alternative (Aufruf aus
`sale.create_sale_data` heraus) zu nehmen — die hätte zwar keine 🔒-Freigabe gebraucht, aber
Fortschrittszeile/Fehler-Erfassung/eigenes Gate verloren, architektonisch schlechter trotz
kleinerem sichtbaren Antrag. `crm_lost` bleibt bewusst log-only ohne eigene GUI-
Fortschrittszeile (kein `WANTED_MODULES`/`MODULE_RUN_ORDER`-Eintrag).

**Cold-Review (fremder Opus-Agent, Plan-Text + Live-Repo, keine Konversationshistorie) vor
Umsetzung — 3 Blocker gefunden:** ein falsches Vorbild-Zitat für die Search-only-Konvention
(`mrp.py`s tatsächliche Fundstelle war eine andere Funktion als ursprünglich zitiert); die
🔒-Einstufung der `orchestrator.py`-Einfügung als "Freigabe ist Formsache" war falsch (siehe
oben); und `config.py`s neues Feld allein hätte nicht gereicht — ohne einen Eintrag in
`run_config.build_selections` wäre `crm_lost` nie befüllt worden (B1-Fehlerklasse).

Getestet: 7 neue Unit-Fälle (u. a. ein direkter Beweis, dass nur unverlinkte Opportunities je
geschrieben werden, selbst bei `pct=100`), 2 neue Live-Integrationsschritte — echter Beweis
gegen `demo-test5`: eine simuliert verlinkte Opportunity blieb aktiv, eine unverlinkte wurde
mit `won_status='lost'` markiert.

### R19 ✅ Erledigt (2026-09-02, als Teil von Sprint S12/WP2) — Expenses

Neues `modules/expenses.py`: `hr.expense`-Datensätze pro Mitarbeiter, Kategorien
(`product.product` mit `can_be_expensed=True`) gesucht statt erzeugt, kein LLM-Call
(Beschreibung per Template — LLM-Minimalismus: nichts Kreatives, das einen Roundtrip
rechtfertigt). Ein konfigurierbarer Anteil wird über zwei gebündelte `write()`-Aufrufe
(`submitted`, dann `approved` — nicht 2×N Einzel-Calls) genehmigt.

**Live-Fund (S12/WP3, vor Implementierung):** `product.product` muss auf jedem Datensatz
gesetzt sein — ohne `product_id` lehnte Odoo sowohl den reinen `write(approval_state=…)` als
auch `action_submit`/`action_approve` mit "Select a product to proceed" ab, obwohl das Feld
laut `fields_get` `required: false` ist. Mit `product_id` gesetzt reicht ein reiner
`write('hr.expense', ids, {'approval_state': 'submitted'})` (und danach `'approved'`) —
kein Action-Methoden-Aufruf nötig.

**Cold-Review (fremder Opus-Agent, Plan-Text + Live-Repo, keine Konversationshistorie) vor
Umsetzung — 2 Blocker gefunden, beide gravierender als übliche Planungsungenauigkeiten:**
(1) der Modul-Key musste **`hr_expense`** sein, nicht das ursprünglich geplante `expenses` —
`test_run_config_unit.py`s eigene Invariante (`orchestrator.py`s Literal-Text nach dem
`WANTED_MODULES`-Key durchsucht) hätte bei einem Mismatch fehlgeschlagen, exakt wie
`static/app.js`s Karten-Gate auf denselben Odoo-Technik-Namen angewiesen ist
(`hr_recruitment`/`hr_timesheet`-Präzedenzfall). (2) die ursprüngliche Planung nannte nur
`config.py` + `orchestrator.py` — ohne die vollständige Registrierungskette
(`run_config.py`s `WANTED_MODULES`/`MODULE_LABELS`/`MODULE_RUN_ORDER`/`build_selections`/
`estimate_record_counts`, `static/app.js`s `MODULE_DEFS`+`ICONS`,
`test_run_config_unit.py`s Testdaten) hätte das Feature **nie** funktioniert — exakt die
B1-Fehlerklasse, nicht bloß eine Ungenauigkeit. Diese Kette ist jetzt als eigenständige
Referenz in `ROADMAP.md` §3 dokumentiert (siehe Nachtrag oben), statt bei jedem neuen Modul
neu entdeckt zu werden.

Getestet: 7 neue Unit-Fälle (Pattern 1/3/5/7, ein Regressionstest, dass `product_id` auf
jedem erzeugten Datensatz gesetzt ist, ein Beweis, dass die Genehmigung als 2 gebündelte
Batch-Writes läuft statt 2×N Einzel-Calls), 3 neue Live-Integrationsschritte — erster Lauf
durchgehend grün, kein einziger Fehlschlag.

### R13 ✅ Erledigt (2026-09-02/03, als Teil von Sprint S13/WP2-4) — Seriennummern-/Chargenverfolgung

`data_factory.assign_tracking` setzt `tracking='lot'`/`'serial'`/`'none'` auf einen
konfigurierbaren Anteil frisch erzeugter Storables — strikt gegated auf `RunContext.
new_product_ids` (neues additives Feld: nur was `master_data._create_products` in diesem
Lauf selbst anlegt, nie `use_existing`-Bestandsprodukte oder MRP-Komponenten/-Fertigprodukte,
die trotz echtem Odoo-Tracking-Wert unberührt bleiben müssen). `inventory.py`: `tracking='lot'`
bekommt 1 `stock.lot` + Quant mit `lot_id`; `tracking='serial'` bekommt N Einzel-Quants
(Menge je 1, eigener Lot) statt eines Bulk-Quants — Peer-Review-Korrektur aus der ursprünglichen
Planung, live bestätigt korrekt. Lauf-weiter Deckel `_MAX_SERIAL_RECORDS_PER_RUN=500` (nicht
GUI-exponiert, analog `assign_barcodes`s `max_attempts`), degradiert bei Erschöpfung auf die
Serial-Mindestrepräsentation statt auf 0 oder einen ungültigen Bulk-Quant. Ein blockierter
`stock.lot`-Zugriff degradiert (WP5-Fix) betroffene Produkte auf normale Bestände statt das
ganze Modul mitzureißen. **Bewusst offen geblieben, kein stiller Gap:** MRP-Fertigmeldung
(`lot_producing_id`) — `mrp.py` hat weiterhin keinen Abschluss-Call für Fertigungsaufträge,
bleibt eigenes künftiges Item; `purchase.py`-Wareneingang bleibt ohne Lot-Zuordnung.

Details, Live-Befunde und Testabdeckung: siehe "S13 — WP-Sequenz" in §5 dieses Dokuments,
[PR #30](https://github.com/pahuodoo/odoo-daten-generator/pull/30).

### R15 ✅ Erledigt (2026-09-02/03, als Teil von Sprint S13/WP2-4) — Lagerplätze (Sub-Locations)

`inventory.py`: konfigurierbare Anzahl `stock.location`-Kindknoten unter der
Warehouse-Stock-Location (`usage='internal'`, EAN-13-Barcode über `data_factory.
assign_barcodes` — R16-Anknüpfung), bestehende Quant-Erzeugung (S8) verteilt sich per
Round-Robin über einen Location-Pool (Warehouse-Root + optionales zweites Warehouse + alle
Sub-Locations) statt immer auf die Warehouse-Root. `run_journal.ARCHIVE_FALLBACK_MODELS`
(neuer Mechanismus): `stock.location`s Unlink schlägt fehl, solange sie einen Quant
enthält — Archive-Fallback (`active=False`) hilft nur, wenn eine Sub-Location diesen Lauf
leer geblieben ist (live bestätigte Einschränkung, keine falsche Symmetrie-Behauptung
gegenüber `stock.warehouse`, das auch mit Bestand archivierbar ist). `stock.putaway.rule`
(Stretch aus der ursprünglichen Planung) nicht umgesetzt — kein Demo-Mehrwert ggü. dem
Round-Robin-Ansatz für den ersten Wurf, kein stiller Gap, einfach nicht gebraucht.

Details: siehe "S13 — WP-Sequenz" in §5 dieses Dokuments, [PR #30](https://github.com/pahuodoo/odoo-daten-generator/pull/30).

### R16 ✅ Erledigt (Produkt-Ebene 2026-09-02 S12/WP1, Location-Ebene 2026-09-02/03 S13/WP2) — Barcode

Produkt-Ebene (S12): `data_factory.py` erzeugt valide EAN-13 (12 Zufallsziffern +
Standard-Prüfziffer), `master_data.py` schreibt sie bei Produkterzeugung, Kollisionsvermeidung
gegen bereits vorhandene **und** in diesem Lauf bereits vergebene Barcodes (ein `search_read`
bei Laufbeginn, kein N+1), Cap 20 Versuche. Location-Ebene (S13): dieselbe
`assign_barcodes`-Hilfsfunktion unverändert wiederverwendet für R15s Sub-Locations —
`stock.location.barcode` und `product.product.barcode` live bestätigt getrennte
Namensräume, kein Cross-Model-Dedup nötig. `odoo_actions.FIELD_COMPAT_WHITELIST` um
`product.product.barcode` und `stock.location` ergänzt.

Details: siehe "S12 — WP-Sequenz"/"S13 — WP-Sequenz" in §5 dieses Dokuments,
[PR #29](https://github.com/pahuodoo/odoo-daten-generator/pull/29)/[PR #30](https://github.com/pahuodoo/odoo-daten-generator/pull/30).


---

## 5. Sprint-WP-Sequenzen (abgeschlossen)

Aus `ROADMAP.md` hierher verschoben (2026-09-05) — dort gehören laut Dokumentkopf nur
offene/geplante Punkte hin, diese Sprints sind gemerged. Unverändert übernommen.

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

---

### R17 ✅ Erledigt (2026-09-05, als Sprint S16) — Multicompany, N Firmen

N zusätzliche Firmen pro Lauf, je mit eigener Branche und (bei Neuanlage) eigenem Land,
wahlweise eine bestehende `res.company` wiederverwendend. Die komplette Pipeline läuft
**pro Firma erneut** — genau der Umfang, den der ursprüngliche Minimal-Scope
ausgeschlossen hatte.

Umgesetzt (jeweils live gegen `demo-test5.odoo.com` verifiziert):

- **Firma-Scoping ohne Modul-Code-Touch** (S16-D14): `OdooJson2Client` trägt einen
  Default-Kontext (`odoo_client.py:275`, `_merge_context` `:277`), der bei jedem
  Aufruf `{'allowed_company_ids': [id], 'company_id': id}` mitschickt. Transaktions-
  Records landen dadurch bei der richtigen Firma, ohne dass die sieben schreibenden
  Module angefasst werden mussten. **Ausnahme, live widerlegt:** `res.partner` und
  `product.product` erben `company_id` *nicht* aus dem Kontext — nur Modelle mit
  `default=lambda self: self.env.company` reagieren darauf; `master_data.py` brauchte
  deshalb doch einen kleinen Touch.
- **`RunContext.res_company_ids`** (`config.py:185`) — die echte `res.company`-Id, auf
  die eine `ctx` gescoped ist. Bewusst getrennt von `company_ids`, das trotz seines
  Namens `res.partner`-Ids hält (siehe `ROADMAP.md`s D16, offen).
- **`ctx`-bewusste Firma-Helfer**: `odoo_actions.get_main_company_id(client, company_id)`
  (`:368`) nimmt die Ziel-Firma jetzt als Parameter statt sie global zu erraten.
- **`run_config.build_context_list`** (`:540`) — eine `ctx` pro Firma statt einer
  globalen; `build_context` bleibt für den Einzelfirmen-Pfad.
- **Teilausfall als eigener Zustand**: `STATUS_PARTIAL` (`web/jobs.py:41`) — schlägt eine
  Firma fehl, laufen die übrigen weiter und der Lauf endet sichtbar teil-erfolgreich
  statt binär grün/rot. Berührte sieben bestehende Zwei-Zustand-Prüfungen.
- **Frontend**: Firmenauswahl-Bildschirm nach "Verbindung", Konfigurations-Tabs pro
  Firma, `/api/preflight` pro Firma.

**Verlauf:** neun Cold-Review-Runden über zwei Plan-Generationen, 15 Planungs-Commits vor
der ersten Implementierungszeile — siehe `SPRINT_LOG.md`s S16-Eintrag. Der Umfang dieses
Aufwands ist der Auslöser für den `sprint-review`-Skill.

Entscheidungen S16-D1–S16-D15, beide WP-Sequenzen und der nie umgesetzte Minimal-Scope:
§5 dieses Dokuments. [PR #35](https://github.com/pahuodoo/odoo-daten-generator/pull/35).

<details>
<summary>Ursprünglicher R17-Abschnitt aus <code>ROADMAP.md</code> (Minimal-Scope, nie umgesetzt)</summary>

### R17 ⚠️ SCOPE ÜBERHOLT (2026-09-04) — Multicompany

**Der komplette Minimal-Scope unten (dreifach cold-reviewed, freigegeben) ist
durch neue Nutzeranforderungen überholt — nicht implementieren wie unten
beschrieben.** Nach Abschluss von Cold-Review Runde 3 stellte der Nutzer klar,
dass der eigentlich gewollte Umfang deutlich größer ist: **N** zusätzliche
Firmen (nicht eine), pro Firma **frei wählbare Branche** und (bei neu
angelegten Firmen) **frei wählbares Land**, **Wiederverwendung einer
bereits existierenden `res.company`** als Alternative zum Neuanlegen
(inkl. optionaler Wiederverwendung von deren eigenen Partnern/Produkten),
ein neuer Auswahl-/Konfigurationsbildschirm **pro Firma** nach "Verbindung",
und — der größte Einzelfund — **die komplette bestehende Pipeline (CRM/Sale/
HR/MRP/Accounting/etc., heute die "Konfiguration"-Ansicht) läuft pro Firma
erneut**, nicht nur einmal mit über Firmen verteilten Zusatz-Records. Das ist
genau der Umfang, den der ursprüngliche Minimal-Scope unten bewusst
ausgeschlossen hatte ("Nicht den kompletten 8-Module-Durchlauf pro Firma
wiederholen"). Vollständige Anforderungserfassung siehe **"S16-NEU —
Anforderungen (2026-09-04, ersetzt Minimal-Scope)"** direkt nach der
"S16 — WP-Sequenz"-Sektion unten. Architektur-Spike für den neuen Umfang
noch nicht begonnen — nächster Schritt einer künftigen Sitzung.

**Was aus dem alten Spike weiterhin gültig bleibt (Instanz-Fakten, unabhängig
vom Umfang):** die Umhäng-Verweigerung referenzierter Records (Punkt 2
unten), die fehlende Record-Rule-Maskierung für den API-Key-User (Punkt 3),
`get_main_company_id`s vier Call-Sites (Punkt 4), kein automatisches
Warehouse pro Firma, `res.company.active` als Archiv-Fallback, und —
**neu, 2026-09-04, direkt aus der Nutzer-Rückfrage zu Land/Kontenplan
entstanden:** `country_id` **im `create()`-Aufruf selbst** gesetzt (nicht
nachträglich per `write()`) lässt Odoo automatisch einen vollständigen
Kontenplan laden — live bestätigt, 1312 `account.account`/8 `account.journal`/
12 `account.tax`/6 `account.fiscal.position` sofort nach `create()` lesbar,
`chart_template` automatisch auf `de_skr03` gesetzt. Kein manueller
`account.chart.template.try_loading`-Aufruf nötig — der Mechanismus aus
Punkt 1 unten ist damit überholt (einfacher, aber die Uncleanable-Residue-
Eigenschaft bleibt: diese Records laufen weiterhin nie durch
`JournalingClient`). `country_id` **nach** `create()` per `write()` gesetzt
lädt dagegen **nichts** (live bestätigt, 0 Accounts). Details in
`ODOO_GOTCHAS.md`.

---

**Live bestätigt (Architektur-Spike, 2026-09-04, `demo-test5.odoo.com`,
ursprünglicher Minimal-Scope, siehe Überholt-Hinweis oben):**
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

</details>

---

### S16 — Multicompany (R17), abgeschlossen 2026-09-05

Aus `ROADMAP.md` hierher verschoben nach dem Merge von
[PR #35](https://github.com/pahuodoo/odoo-daten-generator/pull/35).

> **Zur Lesart der `D<n>`-Nummern unten:** sie sind **sprint-lokal** und meinen
> `S16-D1` … `S16-D15`, **nicht** die gleichnamigen Punkte aus `ROADMAP.md` §3.
> Die beiden Namensräume kollidieren bei D5, D6, D8 und D10 (siehe `ROADMAP.md`s
> D19). Wo im folgenden Text ausnahmsweise ein §3-Punkt gemeint ist, steht das
> ausdrücklich als `§3-D<n>` dabei. Ab S17 gilt die Präfix-Pflicht aus `CLAUDE.md`,
> dieser Abschnitt ist der letzte ohne sie.

Der erste Block ist der **nie umgesetzte** Minimal-Scope: er wurde vor jeder
Implementierungszeile durch neue Nutzeranforderungen ersetzt (S16-NEU darunter).
Aufbewahrt, weil seine live geprüften Instanz-Fakten in die neuen Entscheidungen
eingeflossen sind — nicht, weil daraus Code entstanden wäre.

### S16 — WP-Sequenz (Multicompany: R17) ⚠️ SCOPE ÜBERHOLT, siehe R17-Abschnitt

**Diese komplette WP-Sequenz beschreibt den ursprünglichen Minimal-Scope
(eine zusätzliche Firma, kein voller Pipeline-Durchlauf) — dreifach
cold-reviewed und freigegeben, aber inzwischen durch neue Anforderungen
überholt (siehe R17-Abschnitt oben und "S16-NEU — Anforderungen" direkt nach
dieser Sektion). Bleibt unten stehen als Referenz für die weiterhin gültigen
Low-Level-Fakten und das dreifache Cold-Review-Verfahren selbst — nicht als
Umsetzungsvorlage für den neuen, größeren Scope.**

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
(Detail siehe R17-Abschnitt Punkt 3, und `ODOO_GOTCHAS.md`). Das kehrt den
ursprünglichen Grund für "`company_ids`-Erweiterung nicht empfohlen" um: nicht
weil eine Erweiterung ein Leck öffnen würde, sondern weil das Leck
(`connect_service.fetch_existing_data` ungefiltert nach `company_id`) bereits
**heute** existiert, sobald dieser Sprint das erste Firma-2-Produkt anlegt —
das macht den Filter in `fetch_existing_data` zu einem echten
WP2-Pflichtschritt, kein optionales Zukunfts-Item mehr.

**Cold-Review Runde 3 gelaufen** (dritter unabhängiger Opus-Agent, gleiches
Verfahren, explizit auf Nutzer-Wunsch — Runde 2 hatte selbst einen dritten
Durchgang verlangt, "meine Einschätzung war rein mechanisch" ersetzt keine
unabhängige Bestätigung). **Der Vorbehalt war berechtigt: Runde 3 fand
3 weitere Blocker + 8 Should-Fixes**, das dritte Mal in Folge, dass eine
Fix-Runde neue Defekte einführt oder auf einer ungeprüften Behauptung
beruht — u. a. eine dritte, von Runde 1+2 übersehene Testdatei, die mit den
bis dahin vorgeschlagenen Fixes tatsächlich rot geworden wäre (live
durchgerechnet, nicht nur behauptet); ein Selbstwiderspruch in WP4 (verlangte
gleichzeitig "nur Unit-Tests" und eine Live-Test-Pattern, die nur mit einem
echten Live-Test erfüllbar ist); und eine im Plan seit WP1 offene Live-Frage
(legt Odoo automatisch ein Warehouse pro Firma an?), die trotz einer bereits
live stehenden Firma+Warehouse aus dem Spike nie tatsächlich beantwortet
wurde — jetzt live geklärt (**nein**, kein automatisches Warehouse), per
Zusatz-Spike im Rahmen dieser dritten Runde. Alle 3 Blocker + die
substantiellen Should-Fixes sind unten eingearbeitet; die von Runde 3 explizit
gegengeprüften Runde-2-Fixes (B3s Index-Formel, B2s Namens-Parameter-Korrektur,
B4s Testpräzedens-Kern, die `test_run_config_unit.py`-Scoping) hielten alle
stand — die Defekte lagen in neu geschriebenem Prosa-Text, nicht in bereits
korrigierter Logik. Runde 3 fand **keine** neue offene Live-Frage mehr (die
eine, B3, wurde in derselben Runde aufgelöst) und schloss mit einer direkten
Empfehlung, dass der Plan nach Einarbeitung bereit zur Implementierung ist —
drei Runden ohne verbleibende offene Live-Fragen, jede Runde fand strikt
weniger Blocker als die vorherige (6 → 4 → 3, aber vor allem: 0 verbleibende
Live-Fragen). Plan gilt als freigegeben zur Implementierung.

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
| **WP2** | Infrastruktur — **vollständige Registrierungskette** (Cold-Review-Blocker B1/B2 Runde 1, analog §3s "Referenz — Registrierungskette", hier mit pseudo-Modul-Abweichung): `config.py` (`RunContext.res_company_ids` + `ModuleSelections.multicompany`, siehe oben) 🔒; `run_config.py`s `PSEUDO_MODULES` (`"multicompany"` ergänzen, **nicht** `WANTED_MODULES` — keine echte Odoo-App, kein `installed_modules`-Gating nötig, Runde 2 bestätigt: `active_progress_keys` hat einen eigenen pseudo-Zweig, gated nur auf Auswahl) + `MODULE_LABELS` + `MODULE_RUN_ORDER` (nach `"documents"`, **exakt** das Literal `("multicompany"` ohne Leerzeichen nach der Klammer — `test_run_config_unit.py:99`s Invariante sucht `src.index(f'("{key}"')` im rohen `orchestrator.py`-Text, Should-Fix S4 Runde 2) + `build_selections` + `estimate_record_counts` (**eigene, von "Kontakte"/"Produkte" verschiedene Labels** wie `"Kontakte (2. Firma)"`/`"Produkte (2. Firma)"` — `estimate_record_counts` ist ein einfaches Dict, ein kollidierender Key überschreibt master_datas Zahl kommentarlos, Should-Fix S1 Runde 2; dazu eine eigene `"Firmen (2. Firma)"`-artige Zeile, die im Erstentwurf fehlte, Should-Fix S8 Runde 3) + `test_run_config_unit.py`s **zwei** betroffene Stellen: Zeile mit `selected == _ALL_INSTALLED \| {"documents", "analytic"}` wird `\| {"documents", "analytic", "multicompany"}` (nur `_FULL`s Auswahl-Dict bekommt den Block, **`_ALL_INSTALLED` selbst NICHT** — das würde eine fiktive echte Odoo-App vortäuschen, Should-Fix S3 Runde 2), UND separat `test_run_config_unit.py:240`s `keys == ["stammdaten", "crm", "sale", "documents"]`-Assertion (S16 landet dort ebenfalls, weil `MODULE_RUN_ORDER`-Mitgliedschaft + Auswahl reicht — anders als `analytic`, das bewusst nie in `MODULE_RUN_ORDER` steht, Should-Fix S3 Runde 2); `static/app.js:257`s hardcodierte `isPseudo`-Prüfung um `"multicompany"` erweitern (zweite hardcodierte Pseudo-Modul-Stelle, sonst bleibt die Karte dauerhaft deaktiviert — B2 Runde 1) **plus** einen `ICONS`-Eintrag ergänzen (`iconSvg` fällt bei fehlendem Key still auf ein leeres `<svg>` zurück, kein Fehler — Should-Fix S7 Runde 2; Runde 2 bestätigt: sonst nichts in `app.js` hardcodet ein Modul-Set, `renderModuleGrid`/`activeModuleKeys`/`buildPayload` iterieren durchgängig `MODULE_DEFS`). **Präzisierung (Should-Fix S3 Runde 3):** `iconSvg` liest über `def.icon`, **nicht** `def.key` (`static/app.js:268`) — vier von 13 bestehenden Einträgen weichen ab (`hr_timesheet`→`timesheet`, `hr_recruitment`→`recruit`, `hr_expense`→`expense`, `documents`→`docs`). Der `ICONS`-Eintrag muss unter dem im `MODULE_DEFS`-Eintrag gewählten `icon`-Wert stehen, nicht zwingend unter `"multicompany"` selbst — sonst bleibt exakt die leere-`<svg>`-Falle offen, die dieser Fix schließen soll (dieselbe Ungenauigkeit steckt auch in §3s kanonischer Referenz, Schritt 6 — dort ebenfalls präzisieren); `odoo_actions.MODEL_ACCESS_PROBES["multicompany"] = ["res.company"]` + `probe_model_access`s hardcodiertes Pseudo-Modul-Tupel (`odoo_actions.py:317`) um `"multicompany"` erweitern — **und den Probe-Wert tatsächlich in `modules/multicompany.py` konsumieren** (`if not ctx.model_access.get('res.company', True): ctx.skipped_modules.add("multicompany"); return`, analog `modules/documents.py:285-288` — ohne diesen expliziten Check meldet die Progress-Zeile "Fertig" für ein Modul, das nichts getan hat, Should-Fix S2 Runde 2; `res.company`-Anlegen ist ein echtes, oft eingeschränktes Recht, verwandelt einen harten Fehlschlag in einen sauberen Pattern-3-Skip). **Dritte betroffene Teststelle (Blocker B1 Runde 3, von Runden 1+2 übersehen):** `tests/unit/test_odoo_actions_unit.py:344`s `expected_always_on = set(MODEL_ACCESS_PROBES["stammdaten"]) | set(MODEL_ACCESS_PROBES["documents"])` **muss** `| set(MODEL_ACCESS_PROBES["multicompany"])` ergänzen — sonst schlägt der Test rot fehl, sobald `multicompany` zum hardcodierten Pseudo-Tupel hinzukommt (live durchgerechnet: `result.keys()` enthält dann `res.company`, die Assertion nicht). Gleicher Patch-Durchgang: `probe_model_access`s Docstring (`odoo_actions.py:305-312`) nennt bisher nur `"stammdaten"`/`"documents"`, ebenfalls auf drei erweitern; `odoo_actions.create_second_company` (Firma anlegen — nur `name` aus `ctx.name_banks.get('company_names')` per deterministischem Index gewählt, **einheitlich** `idx = ctx.criteria.num_companies + len(ctx.res_company_ids)` (Blocker B3 Runde 2 — Erstentwurf nannte in WP2 und WP3 zwei widersprüchliche Indexformeln, die zweite kollidierte garantiert mit master_datas erster Partnerfirma; Modulo + `_unique_name`-artiges Suffix-Fallback für den Fall `idx >= len(pool)` — **Bedingung auf `idx` selbst, nicht auf `num_companies`** (Should-Fix S4 Runde 3: bei bereits vorhandenen `res_company_ids` weichen beide auseinander, `num_companies >= len(pool)` als Trigger würde bei z. B. `num_companies == len(pool)-1` mit einer bereits vorhandenen Firma keinen Suffix anhängen und exakt auf `pool[0]` zurückfallen — die Kollision, die dieser Fix verhindern soll; bei aktuellem Ein-Firma-Scope noch nicht erreichbar, aber die Formel muss von Anfang an richtig stehen, nicht erst bei einem hypothetischen N-Firmen-Ausbau), insbesondere gegen den nur 5 Einträge großen `fallback_data.FALLBACK_COMPANIES`-Pool bei UI-Maximum 20 Firmen), plus `currency_id` von Firma 1 übernommen; **kein** `country_id`/Kontenplan, siehe R17 Punkt 1; **kein Barcode** auf den WP3-Produkten — `master_data.py:59-62`s Barcode-Dedup liest ungefiltert, Runde 2s B1-Nachtest zeigt zwar keine Maskierung mehr, ein Barcode wäre also technisch sichtbar, aber ungeprüft gegen Eindeutigkeits-Constraints über Firmen hinweg — einfach vermeiden); **`connect_service.fetch_existing_data(client)`s zwei Domains (Firmen-/Produkt-Query, Funktion endet `:126`) um einen `company_id`-Filter ergänzen** (`['|', ['company_id','=',False], ['company_id','=', <Firma-1-Id>]]`, Firma-1-Id per `get_main_company_id(client)`-Aufruf **innerhalb** der Funktion, kein neuer Parameter) — **echter WP2-Pflichtschritt, kein optionales Future-Item** (Runde 2, Konsequenz aus R17 Punkt 3: da keine Record-Rule-Maskierung existiert, zieht jeder spätere `use_existing`-Lauf sonst Firma-2-Produkte/-Partner ungefiltert in den geteilten `ctx.product_ids`/`ctx.company_ids`-Pool, sobald dieser Sprint das erste Firma-2-Produkt anlegt. **Null-Fallback (Should-Fix S2 Runde 3):** `get_main_company_id` gibt bei Fehlschlag `None` zurück (`odoo_actions.py:341`) — `['company_id','=',None]` kollabiert die Domain auf `company_id=False` und schrumpft `existing_products` für **jeden** Nutzer bei jedem Connect, nicht nur bei `use_existing`. Bei `None` den Filter ganz auslassen (ungefiltert wie bisher), nicht mit `None` filtern. **Wirkt auf ALLE `use_existing`-Läufe,
nicht nur auf Läufe mit aktivem Multicompany-Flag** — der Filter selbst ist unbedingt, nur
das Risiko, das er behebt, ist an Firma-2-Produkte gekoppelt. Für WP4s Diff-Review explizit als
Verhaltensänderung an einem bestehenden, unabhängigen Feature benennen, nicht stillschweigend
mitschicken); `run_journal.py`s `ARCHIVE_FALLBACK_MODELS` um `"res.company"` ergänzen (D7-Cleanup-Netz — `res.company` lässt sich oft nicht `unlink`en sobald sie referenziert wird; Archivieren als Fallback **jetzt live bestätigt**: `write(active=False)` auf eine Firma, die bereits ein Warehouse hält, funktioniert, 2026-09-04 nachgeprüft, war zuvor unverifiziert); neues `modules/multicompany.py`-Grundgerüst + `orchestrator.py`-Registrierung als letzter Pipeline-Schritt 🔒 (Platzierungs-Begründung oben); `static/app.js`-UI-Karte | ja | WP1 |
| **WP3** | Befüllung: `modules/multicompany.py` erzeugt `partner_count` Partner + `product_count` Produkte (`company_id=ctx.res_company_ids[0]`, batched, lokale Variablen — siehe Pool-Isolation oben) + ein Warehouse für die neue Firma. `odoo_actions.create_second_warehouse` (R14, `odoo_actions.py:113`) nimmt `company_id` bereits als Parameter — `inventory.py:55` übergibt heute nur zufällig `get_main_company_id(client)`, kein Refactor an R14-Code nötig, nur `create_second_warehouse(client, ctx.res_company_ids[0])` aufrufen. **Kein Namens-Parameter (Blocker B2 Runde 2 — Erstentwurf verlangte "einen passenden Namen übergeben" bei gleichzeitiger "kein Refactor nötig"-Aussage, ein Widerspruch: die Funktion hat keinen Namens-Parameter, `"Lager 2 (<suffix>)"`/Code `WH2<suffix>` sind intern gebaut).** Default-Name bleibt — Name/Code sind pro Firma eindeutigkeitsgeprüft, `"Lager 2 (...)"` unter Firma 2 ist bereits gültig, kein Aufwand für kosmetische Umbenennung gerechtfertigt. **Live geklärt (Blocker B3 Runde 3 — die Frage stand im Erstentwurf, wurde aber trotz bereits stehender Firma+Warehouse aus dem WP1-Spike nie beantwortet):** legt Odoo beim Anlegen einer `res.company` automatisch ein Standard-Warehouse an? **Nein** — live nachgeprüft 2026-09-04, frische Firma ohne eigenes Zutun angelegt, `search_read('stock.warehouse', [['company_id','=', <neue id>]])` direkt danach liefert `[]`. WP3s Warehouse-Schritt ist damit notwendig, nicht redundant, und es entsteht **kein** zusätzlicher un-journallierter Rest über den bereits akzeptierten `res.partner` (S6) hinaus. `create_second_warehouse` selbst ist über `tests/integration/test_inventory.py`s "Step 4 — S13/R14" (`modules/inventory.py:68`) bereits gegen Firma 1 live getestet — die stärkere, tatsächlich zutreffende Begründung, warum WP3 dafür **keinen eigenen neuen** Live-Test braucht (Zitat korrigiert, Should-Fix S5/S7 Runde 3 — der Erstentwurf verwies fälschlich auf einen `grep`-Treffer in `tests/integration/`, es gibt dort keinen direkten Treffer, nur den indirekten Pfad über `inventory.create_inventory_data`): WP1s Spike hat bereits **live eine frische Firma mit Warehouse** angelegt (derselbe Spike, der gerade die Auto-Warehouse-Frage beantwortet hat) — das ist der tragfähige Beleg, nicht der `grep`. **Produktnamen-Quelle korrigiert (Should-Fix S1 Runde 3):** `fallback_data.FALLBACK_PRODUCTS` ist **kein** flacher Fallback-Pool, sondern ein nach Branche geschlüsseltes Dict (`{'IT': [...], 'Fertigung': [...], 'Handel': [...]}`, `fallback_data.py:9-13`) — `ctx.name_banks.get('product_names') or fallback_data.FALLBACK_PRODUCTS['IT']` würde bei fehlenden LLM-Namen einen `KeyError` werfen. Richtige Form (analog `orchestrator.py:160`/`master_data.py:42`): `FALLBACK_PRODUCTS.get(ctx.industry, FALLBACK_PRODUCTS['IT'])` — der `.get`-Default ist nicht kosmetisch, `ctx.industry` defaulted auf `"IT-Dienstleistung"` (`run_config.py:105`), was selbst **kein** Schlüssel im Dict ist. `'stock' in ctx.installed_modules`-Gate für den Warehouse-Schritt ergänzen (fehlte im Erstentwurf) | nein | WP2 |
| **WP4** | Peer-Review vor Merge (S5-S15-Verfahren, Diff statt Plan-Text), grüner Live-`test_suite.py`. **Testumfang eindeutig festgelegt (Blocker B2 Runde 3 — Erstentwurf widersprach sich selbst: "nur Unit-Test-Abdeckung" bei gleichzeitiger Anweisung, in `tests/integration/test_suite.py` zu registrieren, während die "Pro Arbeitspaket verbindlich"-Liste unten Pattern 4 verlangt, das laut CLAUDE.md ausdrücklich die Integrationstest-Form ist — Partner/Produkte/Warehouse unter Firma 2 existieren aber nur, wenn ein Integrationstest tatsächlich eine Firma live anlegt, was der erste Satz verbietet):** `modules/multicompany.py` bekommt **ausschließlich** Unit-Test-Abdeckung (gemockter Client), **kein** Integrationstestmodul, **keine** Registrierung in `tests/integration/test_suite.py`. Pattern 4 für diesen Sprint gilt als erfüllt durch WP1s einmalige manuelle Live-Verifikation (frische Firma + Warehouse live angelegt und zurückgelesen, siehe R17 Punkt 3 und die B3-Auflösung in WP3 oben) plus gemockte Read-Back-Tests in der Unit-Suite — nicht durch einen sich wiederholenden Live-Test. **Begründung (Blocker B4 Runde 2, Zitat korrigiert Runde 3 — siehe WP3-Notiz oben: `create_second_warehouse` hat sehr wohl einen indirekten Live-Testpfad über `inventory.create_inventory_data`, kein direkter `grep`-Treffer):** der echte Grund, hier anders zu entscheiden als S13/R14 (das seinen Warehouse-Rückstand ausdrücklich akzeptiert): eine im Firmenumschalter sichtbare, dauerhaft archivierte `res.company` ist deutlich schwereres Live-Restmaterial als ein zusätzliches Warehouse. Nur `tests/unit/unit_suite.py` (Import + `_MODULES`) bekommt einen neuen Eintrag, `tests/integration/test_suite.py` **nicht** | — | WP2-WP3 Code steht |

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
- **(Namens-Kollision im LLM-Pool, Should-Fix S6 Runde 3)** `master_data.py`s
  `_unique_name`-Dedup läuft über ein funktionslokales `used`-Set
  (`:145-155`), das `multicompany.py` nicht sieht — enthält der von der LLM
  gelieferte `company_names`-Pool selbst Duplikate (Prompt dedupt nicht),
  kann die neue Firma denselben Namen wie eine bereits erzeugte
  Partnerfirma bekommen. Rein kosmetisch (keine Eindeutigkeits-Constraint
  auf `res.partner.name` oder `res.company.name`), durch die Prompt-Vorgabe
  "mind. 25 Firmennamen" in der Praxis selten. Akzeptiert, kein Code-Fix.

**Pro Arbeitspaket verbindlich:** dieselben Testing Design Patterns wie jedes
bisherige Sprintpaket (siehe CLAUDE.md) — Pattern 1 (leerer
Partner-/Produkt-Namenspool), Pattern 2 (LLM liefert `None`/leer für
`company_names`/`product_names` → Fallback), Pattern 3
(`multicompany.enabled=False` → keine API-Calls), Pattern 4 (Read-back auf
Partnern/Produkten/Warehouse nach Erzeugung — **erfüllt durch WP1s manuelle
Live-Verifikation + gemockte Read-Back-Assertions in der Unit-Suite, siehe
WP4-Testumfang oben; kein wiederholter Live-Integrationstest, und nichts
davon auf einem Kontenplan, der nicht mehr im Scope ist**), Pattern 5
(fehlende Prerequisites — leere `ctx.name_banks['company_names']` → Skip mit
Fallback-Namen statt Crash; **und** `create_second_company`-Fehlschlag →
`ctx.res_company_ids` bleibt leer, WP3s Partner-/Produkt-/Warehouse-Schritte
müssen das vor einem `ctx.res_company_ids[0]`-Zugriff prüfen statt einen
`IndexError` zu riskieren, Should-Fix S8 Runde 3), Pattern 8
(Partner-/Produkt-Erzeugung batched, nicht in einer Schleife pro Record).

### S16-NEU — Anforderungen (2026-09-04, ersetzt Minimal-Scope)

Erfasst aus einer Rückfrage-Runde mit dem Nutzer nach Abschluss von
Cold-Review Runde 3 (siehe R17-Abschnitt oben für den Überholt-Hinweis).
**Kein Architektur-Spike für diesen Umfang begonnen** — dieser Abschnitt ist
reine Anforderungserfassung, Startpunkt für die nächste Sitzung, kein Plan.

**Kernentscheidungen (vom Nutzer bestätigt):**

1. **N zusätzliche Firmen, nicht eine.** Der "höchstens ein Eintrag"-Minimal-
   Scope entfällt. `RunContext.res_company_ids: List[int]` (bereits als
   flache Liste entworfen, genau für diesen Fall — Design hält) muss jetzt
   wirklich mehrere Einträge tragen können.
2. **Pro Firma: neu anlegen ODER bestehende `res.company` wiederverwenden.**
   Für "wiederverwenden" existiert **keine** Infrastruktur — `connect_service`s
   `existing_companies` meint `res.partner` mit `is_company=True`
   (Kunden-Kontakte), nicht echte `res.company`-Records. Braucht einen neuen,
   eigenständigen Fetch (`res.company`-Liste) — sauber benannt, nicht in die
   bestehende `company_ids`-Verwechslungsfalle laufen.
3. **Pro Firma, bei "wiederverwenden": opt-in, ob auch deren bereits
   existierende Partner/Produkte wiederverwendet werden** (statt neue für sie
   zu erzeugen). Braucht einen `company_id`-gescopten Fetch — `connect_service.
   fetch_existing_data` ist heute ungefiltert und liefert Firma-1-Daten;
   dieselbe Funktion bekommt (aus dem alten Minimal-Scope) bereits einen
   Firma-1-Filter zum Schutz gegen Firma-2-Leck — beide Anforderungen an
   derselben Stelle zusammen durchdenken, nicht zwei getrennte Patches.
4. **Pro Firma: frei wählbare Branche**, bei neu angelegten Firmen zusätzlich
   **frei wählbares Land** (Scope vorerst DE/AT/CH, "können wir später
   erweitern" — passt zu `data_factory.py`s bereits vorhandenem
   `target_countries`-Parameter, siehe dessen eigenen Kommentar "so a future
   multi-country feature can pass an explicit GUI-selected country list").
   **Branche ist heute ein Singular-Feld** (`DemoCriteria.industry: str`,
   `RunContext.industry: str`, `config.py:8,101`) — pro Firma eine eigene
   Branche bedeutet pro Firma ein eigener `fetch_creative_atoms`/
   `fetch_name_suggestions`-Aufruf (heute: je einmal, ganz am Anfang von
   `orchestrator.run()`, `creative_atoms` ist eine lokale Variable, erreicht
   `ctx` nie). N Firmen mit N Branchen heißt N Aufruf-Paare — wo diese
   passieren (vorab gebündelt vs. lazy pro Firma) ist eine offene
   Architekturfrage für den nächsten Spike.
5. **Land bei neu angelegten Firmen löst automatisch einen Kontenplan aus —
   bestätigt, Mechanismus ist einfacher als angenommen.** `country_id`
   **im `create()`-Aufruf selbst** gesetzt lässt Odoo automatisch einen
   vollständigen Kontenplan laden (live bestätigt, siehe R17-Abschnitt oben
   und `ODOO_GOTCHAS.md`) — kein manueller `try_loading`-Aufruf nötig, der
   alte Minimal-Scope-Mechanismus (Punkt 1 im ursprünglichen R17-Text) ist
   überholt. Die Uncleanable-Residue-Eigenschaft (D7 kann diese Records nie
   erfassen) bleibt unverändert bestehen — jetzt als bewusst akzeptierte
   Konsequenz zu dokumentieren, nicht als Grund, das Feature zu meiden.
6. **Neuer Bildschirm nach "Verbindung":** Firmenauswahl (wie viele, neu vs.
   bestehend), dann **pro Firma ein eigener Konfigurationsbildschirm**
   (Branche, Land, Wiederverwendungs-Toggle) — **inklusive Firma 1**
   (Nutzer-Entscheidung: "unify", kein Sonderfall für die Primärfirma mehr).
7. **Größter Einzelfund: die komplette bestehende Pipeline läuft pro Firma
   erneut.** Heutige "Konfiguration"-Ansicht (CRM %, Sale %, HR, MRP,
   Accounting, etc.) wird nicht länger ein einziger geteilter Schritt,
   sondern **pro Firma wiederholt** — explizit bestätigt ("Full pipeline
   repeats per company"), nicht der ursprünglich vom Minimal-Scope bewusst
   ausgeschlossene Umfang ("Nicht den kompletten 8-Module-Durchlauf pro
   Firma wiederholen — bei ~1 req/s Live-Rate-Limit... Laufzeit- und
   Fehlerbudget-Vielfaches"). Diese Sorge ist mit N Firmen jetzt real und
   muss im nächsten Architektur-Spike explizit adressiert werden (Laufzeit-
   Hochrechnung gegen D10s Rate-Limit, UI-Fortschrittsanzeige über N
   Firmen-Durchläufe hinweg, Fehlerbehandlung wenn Firma 3 von 5 fehlschlägt).

**Was unverändert gültig bleibt** (Instanz-Fakten, nicht vom neuen Umfang
berührt — siehe R17-Abschnitt "Was aus dem alten Spike weiterhin gültig
bleibt" für die vollständige Liste): Umhäng-Verweigerung referenzierter
Records, keine Record-Rule-Maskierung für den API-Key-User,
`get_main_company_id`s vier Call-Sites, kein automatisches Warehouse pro
Firma, `res.company.active` als Archiv-Fallback, der neue
Land-bei-create-lädt-Kontenplan-Mechanismus.

**Nächster Schritt (künftige Sitzung):** eigener Architektur-Spike für den
neuen Umfang, mit den offenen Fragen aus Punkt 4 (wo laufen N
LLM-Atom-Aufrufe) und Punkt 7 (Laufzeit-/Fehlerbudget bei N vollen
Pipeline-Durchläufen) als zentrale Klärungspunkte — danach mindestens eine
Cold-Review-Runde (Runde 2/3 dieser Sitzung zeigten: die verifizierte Logik
hielt jedes Mal, die Fehler saßen im nachträglich angehängten Prosa-Text —
für den neuen Plan also kürzere, entscheidungsfokussierte WP-Zellen
bevorzugen statt lange Should-Fix-Ketten anzuhäufen).

### S16-NEU — Architektur-Spike (begonnen 2026-09-04)

**D1 — Ausführung: sequentieller Loop, EIN `run_id`/Client/Journal für die
ganze Mehrfirmen-Generierung, keine N parallelen Läufe.** `web/jobs.py`s
`_execute()` baut genau einen `JournalingClient` pro `run_id` (`:314`) — §3-D10s
Drossel-Zustand (`_last_request_at`) lebt auf dieser Client-Instanz. N Firmen
als N separate `run_id`s/Clients würden je unabhängig drosseln; die reale
Gesamtrate gegen dieselbe Odoo-Instanz wäre N×, §3-D10 wäre wirkungslos genau in
dem Moment, in dem es am meisten zählt. Der Docstring-Hinweis "~5 gleichzeitige
Läufe" meint verschiedene Nutzer/Instanzen, nicht N Firmen einer Anfrage.
**Entscheidung:** ein `run_id`, ein `client`, ein `llm`, ein `RunJournal` für
den gesamten Mehrfirmen-Lauf; `orchestrator.run()` läuft in einer Schleife
innerhalb `_execute()`, einmal pro Firma, sequentiell — Drossel bleibt korrekt
getaktet, LLM-Cache profitiert firmenübergreifend, Journal sammelt alle
Firmen unter einer löschbaren Einheit (ein "Lauf löschen" räumt alles —
für `res.company` selbst braucht das noch einen kleinen Fix, siehe D13).

**D2 — Namensraum: neues Konzept braucht einen von "company" getrennten
Namen. Korrigiert (Cold-Review): nur ZWEI bestehende Begriffe, nicht drei —
`res_company_ids` existiert noch gar nicht.** `DemoCriteria.num_companies`
(Anzahl `res.partner`-Kundenkontakte PRO Firmenlauf, `config.py:9` — Zeile
korrigiert, `:8` ist `industry`), `RunContext.company_ids` (dieselbe
Bedeutung, historisch falsch benannt, `config.py:110`). `RunContext.
res_company_ids` war ein **Vorschlag** aus dem alten, nie umgesetzten
R17-Minimal-Scope-Plan (repo-weiter Grep: kommt nur in `ROADMAP.md` vor,
nirgends in `config.py` oder sonstigem Code) — der Erstentwurf dieses
Spikes beschrieb es fälschlich als bereits existierendes Feld ("aktuell
≤1"). Muss als neues 🔒-Feld angelegt werden, ist keine Anpassung eines
bestehenden. Ein drittes, jetzt plurales "N Ziel-Firmen"-Konzept braucht
trotzdem einen eigenen, klar unterscheidbaren Namen (z. B.
`target_companies`/`company_profiles`) — siehe D11 unten für die konkrete
Form.

**D3 — `get_main_company_id()` & Geschwister werden `ctx`-bewusst — mit
einem echten Split, nicht pauschal. Korrigiert (Cold-Review):** der
Erstentwurf behauptete 3 "bisher ungenutzte" Helper — **alle drei werden
bereits aufgerufen** (`get_main_company_name` → `connect_service.py:164`,
`get_main_company_info` → `modules/documents.py:97`,
`get_main_company_language` → `connect_service.py:171`), macht die
Gesamtzahl von 7 Call-Sites zwar richtig, aber aus falschem Grund. Wichtiger
Fund dabei: **`connect_service.py:164`/`:171` laufen beim Verbinden, bevor
irgendeine `RunContext` existiert** (`build_context` läuft erst später, in
`jobs.submit()`) — diese zwei können grundsätzlich **nicht** `ctx`-bewusst
werden, egal welche Signatur gewählt wird. **Entscheidung (überarbeitet,
Zählung Runde 2 korrigiert — "4 Helper"/"5 (nicht 7)" war selbst wieder
verrutschte Prosa):** **2 Helper werden `ctx`-bewusst, über 5 Call-Sites**
(`get_main_company_id` — 4 Call-Sites `expenses.py`/`mrp.py`/
`inventory.py`/`purchase.py`; `get_main_company_info` — 1 Call-Site
`documents.py:97`), **2 Helper bleiben unverändert** (`get_main_company_name`/
`get_main_company_language`, Verbindungs-Zeitpunkt, kein `ctx` verfügbar).
Die 2 ctx-bewussten Helper nehmen `company_id` explizit entgegen (aus
`ctx.res_company_ids[0]` der aktuellen Schleifen-Iteration, siehe D10/D11 —
Befüllungs-Reihenfolge dort jetzt explizit geklärt). `get_main_company_name`/
`get_main_company_language` bleiben unverändert (Verbindungs-Zeitpunkt ist
inhärent Firma-1-only, das ist korrekt so, kein Firma-N-Bezug möglich).
🔒-Signaturänderung über `odoo_actions.py` (2 Funktionen) + 5 Call-Sites
insgesamt (4× `get_main_company_id`, 1× `get_main_company_info`).
Architekten-Freigabe nötig.

**D4 — Live bestätigt (2026-09-04): Transaktions-Records unter einer
Nicht-Primär-Firma funktionieren mit firmenneutralen Partnern/Produkten.**
`sale.order` mit `company_id=<frische Firma>`, `partner_id`/`product_id`
beide firmenneutral (`company_id=False`, heutiger Default — `master_data.py`
setzt `company_id` nie explizit) — `create()` **und** `action_confirm()`
beide erfolgreich, live getestet. Zusätzliche Firmen brauchen also **nicht
zwingend** eigene Partner/Produkte — geteilte, firmenneutrale Stammdaten
funktionieren firmenübergreifend ohne Umhäng-Konflikt (Punkt 2 im alten
R17-Befund betraf nur bereits **einer anderen** Firma zugeordnete Records,
nicht firmenneutrale). Vereinfacht die Architektur: eigene Kataloge pro
Firma sind eine Realismus-Entscheidung, keine technische Pflicht.

**D5 — LLM-Atom-Abruf: pro Firma, lazy innerhalb der Schleife, nicht
gebündelt vorab.** Passt zur sequentiellen Ausführung, liefert schneller
sichtbaren Fortschritt für Firma 1 (kein langes stilles Warten auf N
Branchen-Abrufe, bevor überhaupt ein Modul startet), bleibt trotzdem ein
gebatchter Aufruf pro Firma (LLM-Minimalismus intakt — N Aufrufe für N
Branchen, nicht pro Record). **Cache-Key-Präzisierung (Cold-Review):**
`fetch_name_suggestions`s Cache-Key ist branchen+sprachen-geschlüsselt,
trifft bei wiederholter Branche zuverlässig. `fetch_creative_atoms`s
Cache-Key (`llm_service.py:276-279`) hängt **zusätzlich** von
`num_services`/`num_consumables`/`num_storables` ab — zwei Firmen mit
gleicher Branche, aber unterschiedlichen Produkt-Stückzahlen, treffen
diesen Cache **nicht**. "Wiederholte Branchen treffen den Cache natürlich"
gilt uneingeschränkt nur für `name_suggestions`.

**D6 — Fortschrittsanzeige braucht echte Umstrukturierung — Umfang größer
als ursprünglich benannt, jetzt mit konkretem Lösungsweg.** `RunRecord.
modules`/`module_order` (`web/jobs.py:99-100`) sind flache, nach Modul-Code
geschlüsselte Dicts — eindeutig für eine Firma, mehrdeutig für N ("wessen
CRM-Zeile ist das?"). **Cold-Review fand einen zusätzlichen, härteren
Fork:** `orchestrator.py`s `on_start(name)`/`on_done(name, ok)`-Signatur
ist im Code selbst als **gesperrt** dokumentiert (`jobs.py:344-346`) — sie
lässt sich nicht um einen Firmen-Parameter erweitern, ohne diese Sperre zu
brechen. **Entscheidung:** `orchestrator.py` bleibt unangetastet;
stattdessen definiert `_execute()`s Pro-Firma-Schleife **pro Iteration
neue** `on_start`/`on_done`-Closures (das tut sie heute bereits einmalig,
`jobs.py:319`), die den Modul-Code firmen-qualifizieren, BEVOR er
`record.modules`/`self._publish(...)` erreicht (z. B. Key `f"{firmen_idx}:
{module_code}"` statt `module_code`) — kein 🔒-Touch an `orchestrator.py`
nötig. Gleiches Muster für die weiteren, bisher übersehenen flachen
Strukturen: `module_errors` (`:101`), `submit()`s einmaliger
`active_progress_keys(ctx, selected)`-Aufruf (`:198`) wird zur Schleife
über alle Firmen (verkettete, firmen-qualifizierte Liste), `estimate_
record_counts` (`:205`, `run_config.py:498`) ebenso — dessen Labels sind
Text-geschlüsselt ("Kontakte", "Produkte"), brauchen also firmen-
qualifizierte Labels ("Kontakte (Firma 1)") statt qualifizierter Keys.
Betrifft `web/sse.py`s Event-Payloads (Shape bleibt `(type, data)`,
unverändert) und `static/app.js`s Rendering (muss firmen-qualifizierte Keys
gruppiert darstellen, mit einem echten Anzeige-Namen pro Gruppe — siehe D11,
`target.name`/abgeleiteter Name ist genau dieses Label, nicht nur der
bloße Schleifen-Index). **Drei weitere, von Runde 1 übersehene Stellen
(Cold-Review Runde 2):**
1. **`RunRecord.public_dict()` (`jobs.py:119`)** macht `MODULE_LABELS.get(key,
   key)` — mit qualifiziertem Key `"0:crm"` gibt es dafür keinen Eintrag,
   Fallback zeigt den rohen Key statt "CRM". Firmen-Präfix muss vor dem
   Label-Lookup abgetrennt werden, nicht danach. **`web/app.py:403` hat
   denselben `MODULE_LABELS.get(k, k)`-Fallback** (Cold-Review Runde 6,
   von Runde 4 nicht mitgenannt) — für `/api/preflight`s eigene Antwort,
   braucht denselben Fix.
2. **`ctx.skipped_modules` → `record.modules` (`jobs.py:347-350`)** vergleicht
   heute rohe Modul-Codes gegen `record.modules`-Keys — mit qualifizierten
   Keys trifft dieser Vergleich nie, `MODULE_SKIPPED` würde damit still
   aufhören zu funktionieren (dieselbe Silent-Disable-Klasse wie B1). Unter
   D10 hat jede Firma ihre eigene `ctx`, dieser Abgleich muss also in die
   Schleife wandern und mit dem jeweiligen Iterations-Index qualifizieren.
3. **`PROGRESS_KEY_MAP` (`jobs.py:320`/`:326`)** übersetzt `"Stammdaten"` →
   `"stammdaten"` — das Firmen-Präfix muss **nach** dieser Übersetzung
   angewendet werden, sonst bricht die Stammdaten-Zeile für jede Firma.

**Zwei weitere Call-Sites, bisher niemandem zugeordnet (Cold-Review Runde 2):**
- **`POST /api/preflight`** (`web/app.py:373-407`) ist ein zweiter
  `build_context`-Aufrufer, liefert heute Firma-1-Skalare zurück (`mode`,
  `industry`, `record_estimate`, `record_total`, …) — muss dieselbe
  Pro-Firma-Umstellung wie `submit()` bekommen, sonst lügt die
  Prüfansicht bei einem N-Firmen-Lauf.
- **`web/app.py:353`**s Wächter (`if body.get("skip_master_data") and not
  body.get("use_existing")`) — **Korrektur (Cold-Review Runde 3): bleibt
  NICHT unverändert funktionsfähig, wie Runde 2 behauptete.** `use_existing`
  existiert im neuen Payload gar nicht mehr (ersetzt durch `target.
  reuse_master_data`, siehe D11s Existing-Data-Merge-Punkt), und
  `skip_master_data` liegt jetzt pro Block, nicht mehr Top-Level — beide
  Top-Level-Lookups laufen ins Leere (`None and ...`), der Wächter feuert
  still nie mehr. Genau die Silent-Disable-Klasse, die der Code-Kommentar
  direkt daneben selbst benennt. **Entscheidung:** Wächter wird umgeschrieben
  auf "irgendeine Firma hat `skip_master_data=true` **und** weder
  `target.reuse_master_data` noch eine andere Bestandsdaten-Quelle für
  diese Firma" — pro Firma geprüft, nicht mehr Top-Level.

**D7 — Laufzeit-Ehrlichkeit: N Firmen multiplizieren die Gesamtlaufzeit
ungefähr linear.** `web/jobs.py`s eigener Docstring: "ein voller Lauf
dauert 2–5 Minuten" pro Firma heute. N Firmen ≈ N × das (sequentiell,
geteilte Drossel, siehe D1) — N=3 etwa 6–15 Min, N=10 etwa 20–50 Min.
Braucht explizite Nutzer-Kommunikation (Vorab-Schätzung, "das dauert")
und wahrscheinlich eine weiche Obergrenze für N in der UI — genaue Zahl
nicht Teil dieses Spikes.

**D8 — Bestehende-Firma-Wiederverwendung braucht zwei neue, getrennte
Fetches — und die Konsumenten-Seite, nicht nur den Fetch selbst (Cold-Review-
Ergänzung).** (a) Eine echte `res.company`-Liste — `connect_service.py`s
`existing_companies` ist ein Namens-Fallstrick, meint `res.partner`-
Kundenkontakte, nicht echte `res.company` (`:111-116`) — braucht einen
neuen, sauber benannten Fetch. (b) Eine `company_id`-gescopte Variante von
`fetch_existing_data` für den Opt-in "auch die Partner/Produkte dieser
bestehenden Firma wiederverwenden" — die heutige Funktion ist ungefiltert
und Firma-1-geformt. **Zusätzlich betroffen:** `build_context`s
`use_existing`-Zweig (`run_config.py:474-476`) hängt heute unbedingt an
**einen** geteilten `ctx.company_ids`/`ctx.product_ids`-Pool — für den
Pro-Firma-Wiederverwendungs-Toggle muss das zur Pro-Firma-Zuweisung werden
(passt zu D10/D11: jede Firma bekommt ihre eigene `ctx`, also ihren eigenen
Merge-Punkt, kein gemeinsamer Pool mehr über Firmen hinweg). Auch
`ConnectResult.existing_company_ids`/`existing_product_ids`
(`connect_service.py:71-72`) und `as_public_dict`s Skalar-Zählungen
(`:102-103`) sind heute Ein-Firma-geformt.

**D8-Ergänzung — Kollision mit D4 aufgelöst, jetzt entschieden (Cold-Review
Runde 2):** D4 stellte fest, dass die von diesem Tool erzeugten Partner/
Produkte firmenneutral sind (`company_id=False`) und über Firmen hinweg
technisch teilbar. Ein `company_id`-gescopter Fetch (D8a) findet aber per
Definition **keine** firmenneutralen Records — beide Ideen passten bisher
nicht zusammen. **Entscheidung:** für **neu angelegte** Firmen bekommen die
frisch erzeugten Partner/Produkte ab jetzt explizit `company_id` = die
jeweilige Firma gesetzt (Realismus-Entscheidung, nicht technische Pflicht —
D4 bleibt als Fakt gültig, wird hier bewusst nicht ausgenutzt, weil das
Anforderungs-Ziel "pro Firma eigene Branche" ohnehin auf eigene Kataloge
hindeutet). Für **bestehende, wiederverwendete** Firmen liefert D8b genau
diese firmen-gescopten Records zurück — passt jetzt zusammen, weil die
Records, die gefunden werden sollen, auch wirklich `company_id`-markiert
sind. **Umsetzungsweg — Runde 4s Annahme live widerlegt, Runde 5
korrigiert:** Runde 4 nahm an, D14s Kontext-Injektion mache jede
Code-Änderung in `data_factory.py`/`master_data.py` überflüssig. **Live
nachgeprüft 2026-09-04:** `res.partner`/`product.product` verhalten sich
**anders** als `sale.order`/`crm.lead` — `create()` mit `context=
{'allowed_company_ids': [N], 'company_id': N}`, aber **ohne** `company_id`
im Vals-Dict, ergibt `company_id=False` (firmenneutral), nicht `N`. Der
Kontextmechanismus greift nur bei Modellen, deren `company_id`-Feld über
`default=lambda self: self.env.company` definiert ist (wie bei
`sale.order`/`crm.lead`, D14s live getestete Fälle) — `res.partner`/
`product.product` haben diesen Default nicht. **Entscheidung (korrigiert):**
`master_data.py`s `_create_partners`/`_create_products` bekommen doch einen
kleinen Code-Touch — `company_id` explizit im Vals-Dict setzen, wenn die
aktuelle Firmen-Iteration eine reale Ziel-Firma hat. Das ändert D14s
Kernaussage "kein Touch an den 7 betroffenen Modul-Dateien" — `master_data.py`
ist eine achte, zusätzliche Datei, aber ein sehr kleiner, lokaler Eingriff
(ein Vals-Feld, keine Struktur-Änderung). Bestätigt live umsetzbar (Runde 6):
`_create_partners` baut `company_vals_list` in einer expliziten Schleife
(`master_data.py:167-174`), `_create_products` hat eine einzelne `all_vals`-
Liste vor einem `create_batch`-Aufruf (`:50`, `:100`) — beide haben eine
natürliche Ein-Zeilen-Einfügestelle, `ctx` liegt in beiden im Scope.

**Fallback-Ergänzung (Cold-Review Runde 6, echte neunte/zehnte Stelle,
bisher niemandem zugeordnet):** `orchestrator.py`s eigene Fallback-Ersteller
— `_ensure_fallback_partners` (`:145`, `client.create('res.partner', ...)`)
und `_ensure_fallback_products` (`:160`, `client.create('product.product',
...)`) — erzeugen exakt dieselben zwei Modelle, ohne `company_id` im
Vals-Dict, über einen Pfad außerhalb von `master_data.py`. Erreichbar, wenn
eine Firma mit weniger als 2 Produkten oder gescheiterten Stammdaten
endet — die entstehenden Records blieben dauerhaft firmenneutral, D8bs
gescopter Fetch findet sie nie wieder. **Entscheidung:** akzeptiert, kein
Fix — D4 lizenziert firmenneutrale Records bereits als technisch
unproblematisch, und dieser Pfad ist per Definition ein Fallback für einen
bereits fehlgeschlagenen Normalfall, kein Kern-Feature. Explizit
festgehalten, damit ein WP-Autor es nicht übersieht und versehentlich doch
anfasst.

**D9 — UI-Fluss: neuer Bildschirm nach Verbindung, vor/statt Konfiguration.**
Bestätigt 3 echte Ansichten heute (`static/index.html:20-22`: Verbindung →
Konfiguration → Generierung — CLAUDE.mds "4-View-Konsole" ist veraltet,
"Prüfen" ging seit S10 in Konfiguration auf). Neuer Fluss: Verbindung →
[Firmenauswahl: wie viele, je neu oder bestehend] → [Pro-Firma-
Konfigurationsbildschirm, N-mal wiederholt: Branche, Land (falls neu),
Wiederverwendungs-Toggle (falls bestehend), volles bestehendes
Konfiguration-Modul-Set] → Generierung. Größere Frontend-Umstrukturierung
als jede bisherige UI-Änderung in diesem Repo.

**D10 — `RunContext`-Lebenszyklus: eine frische, unabhängige `ctx` pro
Firma, keine geteilte (Cold-Review: als Lücke identifiziert, hier
entschieden).** Eine geteilte `ctx` über alle Firmen hinweg hätte reale
Nebenwirkungen: `ctx.analytic_account_ids` ist memoized
(`config.py:172`/`odoo_actions.get_or_create_analytic_accounts:373`) — Firma
2..N würden Firma 1s Kostenstellen-Plan stillschweigend erben;
`company_ids`/`product_ids`/`invoice_ids` würden sich über Firmen hinweg
aufsummieren, sodass Firma 2s Belege versehentlich Firma 1s Partner ziehen
(laut D4 technisch unproblematisch, aber falsch für Realismus UND für
Pro-Firma-Statistiken). **Entscheidung:** jede Firma bekommt ihre eigene,
vollständig unabhängige `RunContext` — spiegelt exakt, wie ein
Einzelfirmen-Lauf heute funktioniert, nur N-mal wiederholt.
`installed_modules`/`model_access`/`feature_flags` sind Verbindungs-
Ergebnisse (nicht Firma-spezifisch) — pro Iteration identisch
hineinkopiert (live/Code-geprüft: nichts im Produktivcode schreibt diese
Felder nach `build_context` um, `build_context` kopiert sie bereits per
`set(...)`/`dict(...)`, `run_config.py:454-471` — N unabhängige Kopien
"kostenlos"), keine Aggregation nötig.

**D10-Korrektur — Widerspruch zu D11 aufgelöst: Ziel-Firma-Auflösung
gehört zwingend in `_execute()`s Schleife, nicht in `build_context_list`
(Cold-Review Runde 2, echter Blocker — Runde-1-Fassung von D10 und D11
widersprachen sich). Begründung präzisiert (Cold-Review Runde 3 — die
Runde-2-Begründung "`submit()` hat keinen Client" war ungenau: `Journaling
Client` ist auf Modulebene importiert, `session.base_url`/`database`/
`odoo_key` liegen in `submit()` bereits vor, technisch könnte dort einer
gebaut werden).** Der tatsächlich tragende Grund ist eine Reihenfolge, nicht
ein fehlendes Objekt: `run_id = self._next_run_id()` läuft bei
`jobs.py:197`, **nach** `build_context` bei `:185`; `RunJournal.__init__`
verweigert eine `run_id`, die `_RUN_ID_RE` nicht besteht (`run_journal.py:
59-61` — Zeile in Runde 4 korrigiert). Eine in `build_context_list`
angelegte Firma hätte also **beweisbar
kein Journal**, das sie erfassen könnte — uncleanbare Residue durch
Konstruktion, nicht nur durch Konvention. `run_config.py`s eigener
Modul-Docstring (Zeile 3-5, "no Odoo calls") bleibt als zweiter,
unabhängiger Grund gültig. **Endgültige Reihenfolge:** `submit()` baut die
Liste der N frischen `RunContext`s Odoo-frei (`build_context_list`, D11 —
Kriterien/Auswahl, aber noch KEINE Ziel-Firma); `_execute()`s Schleife löst
**pro Iteration, unmittelbar vor dem `orchestrator.run()`-Aufruf für diese
Firma**, über den `JournalingClient` die Ziel-Firma auf (neu anlegen mit
Land+Name, oder bestehende Id per D8 übernehmen) und befüllt erst dann
`ctx.res_company_ids` — das ist der einzige Zeitpunkt, an dem D3s "aus
`ctx.res_company_ids[0]` der aktuellen Iteration" tatsächlich einen
definierten Wert hat. **Mechanische Folge, in Runde 2 nicht genannt:**
`job["ctx"]` (heute `jobs.py:339`/`:347`, eine einzelne `RunContext`) wird
zu `job["contexts"]` — einer Liste — das ist auch eine Änderung in
`submit()`, nicht nur in `_execute()`.

**D11 — Config-Schema-Form: Liste von Payload-Blöcken, jeder Block hat
die Form des heutigen Gesamt-Payloads plus Firma-Zielfelder — Runde-1-
Fassung korrigiert, drei reale Lücken geschlossen (Cold-Review Runde 2),
**zwei weitere Blocker in Runde 3 gefunden und geschlossen**.** Der
heutige `POST /api/runs`-Payload beschreibt bereits genau EINE Firma
(Modus, Branche, Stammdaten-Zahlen, Modul-Toggles) — `build_criteria`/
`build_selections` bleiben dadurch **komplett unverändert wiederverwendbar**,
einmal pro Listen-Element aufgerufen — **mit einer Injektion, siehe
Korrektur 5**. Korrigierte Form:
```json
{
  "existing_data_consent": "granted",
  "companies": [
    {"target": {"mode": "new", "country": "DE", "name": "Musterfirma GmbH"},
     "mode": "both", "industry": "...", "master_data": {...}, "modules": {...},
     "skip_master_data": false},
    {"target": {"mode": "existing", "company_id": 7, "reuse_master_data": true},
     "mode": "both", "industry": "...", "master_data": {...}, "modules": {...},
     "skip_master_data": false}
  ]
}
```
**Fünf Korrekturen gegenüber dem Erstentwurf (1-3 Runde 2, 4-5 Runde 3):**
1. **`"mode": "both"` fehlte in jedem Block.** `build_criteria` defaulted
   ohne dieses Feld auf `"master"` (`run_config.py:179`), `build_selections`
   liefert bei `mode != "both"` immer leer zurück (`:206`) — das ursprüngliche
   Beispiel hätte pro Firma nur Stammdaten erzeugt, jedes `"modules"`-Objekt
   wäre toter Code gewesen.
2. **`target.name` neu, weil `res.company.name` Pflichtfeld ist** — der
   Erstentwurf ließ die neue Firma namenlos.
3. **`existing_data_consent` bleibt Top-Level, wird NICHT pro Firma
   dupliziert** — siehe eigene Konsens-Entscheidung unten.
4. **BLOCKER, Runde 3 — `target.name` ist PFLICHT, nicht optional mit
   LLM-Fallback wie ursprünglich vorgeschlagen.** Runde 2s Fallback-Idee
   ("leer lassen, `_execute()` leitet aus `ctx.name_banks.get(
   'company_names')` ab") ist unmöglich: `ctx.name_banks` wird in der
   Produktivcode-Basis **ausschließlich** innerhalb von `orchestrator.
   run()` gesetzt (`orchestrator.py:61`), zum Zeitpunkt der Ziel-Firma-
   Auflösung (D10-Korrektur — läuft **vor** diesem Aufruf) ist es noch der
   Dataclass-Default `{}` (`config.py:108`). `.get('company_names')` wäre
   `None`, `res.company.name` (Pflichtfeld) bekäme keinen Wert, der
   `create()`-Aufruf scheitert. `orchestrator.py` bleibt außerdem laut D6
   unangetastet — den Aufruf vorzuziehen ist keine Option. Zusätzlich wäre
   `company_names` ohnehin der falsche Pool (Kundenkontakt-Namen,
   `master_data.py:160`, keine Firmennamen). **Entscheidung:** `target.name`
   ist ein Pflichtfeld im Payload für `mode="new"` (UI liefert einen
   sinnvollen Vorschlag/Default, siehe D9). Löst gleichzeitig D6s fehlendes
   Anzeige-Label — bereits bei `submit()` bekannt, kein Warten auf
   `orchestrator.run()` nötig.
5. **BLOCKER, Runde 3 — `build_selections` liest Consent aus genau dem
   Dict, das pro Block hereinkommt, nicht aus einem Top-Level-Feld.**
   `run_config.py:227`: `"use_db_names": payload.get("existing_data_consent")
   == CONSENT_GRANTED` — mit `existing_data_consent` als echtem Top-Level-
   Feld (Korrektur 3) und `build_selections` pro Block aufgerufen, sieht
   dieser Ausdruck `None == "granted"`, **für jede Firma dauerhaft `False`**.
   Die Konsens-Entscheidung selbst zitiert diese Zeile als Beleg, ohne zu
   bemerken, dass sie das per-Block-Dict liest, nicht das Top-Level-Feld.
   **Entscheidung:** `build_context_list` injiziert das Top-Level-Consent-
   Feld in jeden Block, bevor `build_context` aufgerufen wird —
   `{**block, "existing_data_consent": payload.get("existing_data_consent")}`.
   "Komplett unverändert wiederverwendbar" gilt damit **mit dieser einen
   Injektion**, nicht ohne.

**Existing-Data-Merge-Punkt, Runde 3 — aufgelöst, nicht nur benannt.**
Runde 2 fand `build_context`s heutigen `use_existing`-Merge (`run_config.py:
474-476`, Firma-1-geformt) als betroffen, Runde 3 fand die vorgeschlagene
`build_context_list`-Signatur (6 Verbindungs-Parameter) unvollständig — das
echte `build_context` nimmt **8**, inklusive `existing_company_ids`/
`existing_product_ids`. Der saubere Weg ist aber nicht, diese zwei
Parameter einfach zu ergänzen, sondern sie ganz aus dem neuen Pfad zu
entfernen: **der alte, Firma-1-geformte `use_existing`-Mechanismus wird
für den Pro-Firma-Pfad vollständig durch D8bs neuen `company_id`-gescopten
Fetch ersetzt**, nicht daneben weitergeführt. Konsequenz aus D9s "unify"-
Entscheidung (Firma 1 ist jetzt auch ein Payload-Listen-Element): "Firma 1
nutzt vorhandene Daten" ist im neuen Modell einfach `target.mode="existing"`
+ `target.company_id=1` + `reuse_master_data=true` für das erste
Listen-Element — kein Sonderfall, keine zwei parallelen Mechanismen.
`build_context_list(payload, *, language_name, language_code,
llm_model_name, installed_modules, feature_flags, model_access) ->
List[Tuple[RunContext, Set[str]]]` behält die 6 reinen
Verbindungs-Parameter, **ohne** `existing_company_ids`/`existing_product_ids`
— `ctx.company_ids`/`product_ids` bleiben für jede Firma leer, bis
`_execute()`s Schleife sie (nur bei `reuse_master_data=true`) über D8bs
Fetch befüllt. **Reihenfolge in der Schleife — Runde 3s Fassung deckte nur
4 Schritte ab, D14/D15 (Runde 4) fügten zwei weitere hinzu, ohne sich
gegenseitig einzuordnen (Runde 5, Blocker: die einzige Stelle, die
"vollständig" beanspruchte, war es nicht). Jetzt wirklich vollständig, mit
der Begründung für jede Positionierung:**
**`try` umschließt Schritte 2-7, nicht nur Schritt 7 (Cold-Review Runde 6,
Blocker — Runde 5 ließ offen, ob "ein `try` pro Iteration" Schritt 2 oder
erst Schritt 7 umschließt; ein `try` nur um Schritt 7 macht `STATUS_PARTIAL`
für seinen wahrscheinlichsten Auslöser — Firma-Erzeugung selbst schlägt
fehl, Schritt 2 — wirkungslos).** Ein pro Iteration geführtes
`failed_company_indices: Set[int]` (lokal in `_execute()`, außerhalb der
Schleife initialisiert) wird bei jedem Fang dieses `try`s um den aktuellen
Iterations-Index ergänzt — dieselbe Menge löst sowohl `jobs.py:366`s
bisher unentschiedenes "Drei-Wege-Unterscheidung **oder** Pro-Firma-
Tracking" (Runde 4 ließ das offen) als auch `STATUS_PARTIAL`s Berechnung
(`len(failed_company_indices) > 0` und `< len(companies)` → `PARTIAL`;
`== len(companies)` → `FAILED`; `== 0` → `DONE`):
0. **Pro-Iteration-Closures bauen** (Cold-Review Runde 6, in Runde 5s
   Liste fehlend): D6s firmen-qualifizierende `on_start`/`on_done` müssen
   **vor** Schritt 7 existieren, nach `PROGRESS_KEY_MAP`s Übersetzung
   (D6), damit `orchestrator.run()` sie übergeben bekommt.
1. **`_default_context` zurücksetzen** (`None`) — verhindert, dass die
   Firmen-`create()` selbst (Schritt 2) noch unter der vorigen Iteration
   firmen-Kontext läuft.
2. **Ziel-Firma auflösen** → `ctx.res_company_ids` befüllen
   (D10-Korrektur, deckt beide Zweige: neu anlegen und bestehende Id
   übernehmen).
3. **D14: `_default_context` setzen** auf `{'allowed_company_ids':
   [ctx.res_company_ids[0]], 'company_id': ctx.res_company_ids[0]}` —
   **muss vor Schritt 4 laufen**, sonst legt D15s Warehouse-Erzeugung
   unter der Firma der *vorigen* Iteration an (`create_second_warehouse`
   erhält zwar `company_id` explizit als Parameter, aber ein noch nicht
   gesetzter/veralteter `_default_context` wäre für andere, in derselben
   Iteration folgende Aufrufe ohne expliziten `company_id`-Vals-Eintrag
   riskant).
4. **D15: Warehouse anlegen** (nur bei neu angelegter Firma).
5. **Falls `reuse_master_data`:** D8bs Fetch gegen die aufgelöste
   `company_id`, `ctx.company_ids`/`product_ids` befüllen — **vor**
   `orchestrator.run()`, weil `orchestrator.py:142`s
   Fallback-Partner-Erzeugung bereits auf `ctx.company_ids` kurzschließt.
6. **D12: Kostenstellen-Cache einsäen.**
7. **`orchestrator.run()` aufrufen**, mit den in Schritt 0 gebauten
   Closures.

**`finally` (des `try`s um Schritte 2-7):** **D12: Kostenstellen-Cache
ernten — mit `None`-Schutz** (Cold-Review Runde 6, Blocker — ein
unbedingtes Ernten nach einem Fehlschlag in Schritt 2-5, bevor Schritt 6
je lief, würde den geteilten Cache mit `ctx.analytic_account_ids`s
Dataclass-Default `None` überschreiben, und die nächste Firma legt einen
zweiten Kostenstellen-Plan an — exakt das Duplikat-Problem, das D12
beheben soll): `if ctx.analytic_account_ids is not None: shared_cache =
ctx.analytic_account_ids`. Nur ein echtes Ergebnis (auch `[]`, siehe D12s
erste Präzisierung) überschreibt den geteilten Cache; ein `None` (Schritt
6 nie erreicht) lässt ihn unverändert.

(D8bs Fetch in Schritt 5 und D12s Einsäen in Schritt 6 sind untereinander
reihenfolge-unabhängig — beide müssen nur vor Schritt 7 passiert sein,
nicht in einer bestimmten Reihenfolge zueinander.) Braucht eine neue
`_as_list`-Validierungs-Helper (`run_config.py`s bestehende Coercion-Helper
— `_as_dict`/`_as_int`/`_as_pct`/`_as_bool`/`_enabled`, `:116-153` — haben
keine Listen-Variante),
inkl. serverseitiger Obergrenze für N (nicht nur eine UI-Beschränkung —
`POST /api/runs` ist auch direkt aufrufbar, eine reine Frontend-Grenze wäre
keine echte Grenze; die genaue Zahl bleibt Implementierungsdetail, die
serverseitige Durchsetzung selbst ist hiermit entschieden). Löst D2s
Namensraum-Frage: das Firmen-Array selbst braucht keinen neuen Bezeichner
im Python-Code (es ist Payload-JSON), aber `RunContext.res_company_ids`
(D2/D3) ist der einzige neue Python-seitige "Firma"-Name.

**Konsens-Entscheidung (Cold-Review Runde 2, echtes Datenschutz-Regressionsrisiko
geschlossen; Mechanismus in Runde 3 konkretisiert, vorher nur behauptet):**
`existing_data_consent` bleibt ein **einziges Top-Level-Feld**, nicht pro
Firma dupliziert. Grund: `build_selections` liest es bereits heute für
`crm_chatter.use_db_names` (`run_config.py:227`) — ein Payload, der für
Firma 1 zustimmt und Firma 2 ablehnt, würde trotzdem denselben LLM mit
denselben Prompt-Regeln füttern; die Frage ist inhärent global, nicht pro
Firma. **Konkreter Mechanismus (Runde 3 — Runde 2 behauptete "`validate_
consent` wird erweitert", ohne Signatur oder Call-Site zu nennen; `payload.
get("use_existing")` als Einzeldict-Lookup kann "mindestens eine Firma"
strukturell nicht beantworten):**
```python
def validate_consent(payload, *, reuse_requested: bool = False) -> Optional[str]:
    ...
    if (_as_bool(payload.get("use_existing")) or reuse_requested) and consent != CONSENT_GRANTED:
        raise ConfigError(...)
```
**Blocker, Runde 4 — die Signatur allein reicht nicht, der Prädikat-Body
muss mitgeschrieben werden.** Runde 3 nannte Signatur + Call-Site, aber
nicht die Bedingung selbst — `validate_consent`s heutiges Gate ist
`if _as_bool(payload.get("use_existing")) and consent != CONSENT_GRANTED`
(`run_config.py:170`); im neuen Payload setzt **nichts** mehr
`use_existing`, `reuse_requested` bliebe also ein ungenutzter Parameter
und das Consent-Gate wäre für jeden Lauf **dauerhaft tot** — exakt die
Silent-Disable-Klasse, die dieser ganze Mechanismus schließen soll. Die
Bedingung muss `or reuse_requested` ergänzen (oben eingefügt). Aufruf
**einmal**, aus `build_context_list`, **bevor** die Firmen-Schleife
`build_context` pro Element aufruft:
```python
reuse_requested = any(
    _as_dict(c.get("target"), "target").get("reuse_master_data")
    for c in companies
)
consent = validate_consent(payload, reuse_requested=reuse_requested)
```
`build_context`s eigener interner Aufruf bei `:450` (Zeile in Runde 4
korrigiert) **bleibt bestehen** —
prüft dann nur noch redundant gegen den bereits injizierten, pro Block
gleichen Consent-Wert (siehe D11 Korrektur 5), kein Schaden, hält den
Einzel-Firma-Pfad (z. B. Tests) unverändert funktionsfähig. Schließt das
Runde-2-Risiko, dass `reuse_master_data` den `use_existing`-Namen ersetzt,
ohne dass irgendetwas mehr die Zustimmungs-Sperre auslöst.

**D12 — Kostenstellen (`get_or_create_analytic_accounts`) sind Lauf-weit
gemeinsam, nicht pro Firma — Gegenteil-Behandlung zu D3/D10, echter Fund
aus Cold-Review Runde 2, live bestätigt.** D10s frische `ctx` pro Firma
löst das Firma-1-Erbschafts-Problem, das D10 ursprünglich nannte — reißt
dabei aber ein neues Loch: `ctx.analytic_account_ids` memoized nur
**innerhalb einer** `ctx` (`config.py:172`); mit N frischen `ctx`s ruft
jede Firma `get_or_create_analytic_accounts` mit `None` auf und legt einen
**eigenen neuen** `account.analytic.plan` "Kostenstellen" an —
**live bestätigt 2026-09-04:** sechs solcher Plan-Duplikate stehen bereits
auf `demo-test5` (ids 4-9, aus früheren Testläufen dieser Sitzung), alle 18
zugehörigen `account.analytic.account`-Records (Vertrieb/Produktion/
Verwaltung × 6 Pläne) tragen `company_id=[1, 'demo test']` — bestätigt den
bestehenden Docstring in `odoo_actions.py:364` ("company_id defaultet auf
die aktuelle Firma des API-Users"), per direktem Nachtest ohne
`company_id` im Vals-Dict reproduziert. (Eine erste, ungefilterte Stichprobe
zeigte scheinbar `company_id=False`-Konten — Fehlspur: das waren
unabhängige, projekt-verknüpfte Analytic-Accounts aus S7/R8s
`service_tracking`-Automatik, nicht die Kostenstellen-Accounts selbst; nach
Filtern auf `plan_id in [Kostenstellen-Pläne]` bestätigte sich
`company_id=1` durchgängig.) **Zusätzlich live bestätigt:** ein
Firma-1-gescoptes Kostenstellen-Konto funktioniert trotzdem einwandfrei in
einer `analytic_distribution` auf einer Firma-2-`sale.order.line` —
`create()` **und** `action_confirm()` beide erfolgreich (löst Runde 2s
offene Frage, ob S15/R20s Analytic-Feature auf Nicht-Primär-Firmen
bricht — **bricht nicht**, ist aber trotzdem falsch/verschwenderisch:
N Firmen würden N Plan-Duplikate anlegen, alle Firma-1-gescoped, statt
eines geteilten). **Entscheidung:** `get_or_create_analytic_accounts`
bekommt **kein** Pro-Firma-Verhalten wie D3s Helfer — stattdessen hält
`_execute()`s äußere Schleife den Cache **außerhalb** jeder einzelnen
`ctx` (eine lokale Variable vor der Firmen-Schleife), sät ihn vor jedem
`orchestrator.run()`-Aufruf in die frische `ctx.analytic_account_ids` ein
und erntet das Ergebnis danach zurück in die geteilte Variable — "seed and
harvest" pro Iteration, kein Config-Schema-Touch nötig (Cold-Review Runde 3:
`ctx.analytic_account_ids` ist ein gewöhnliches Dataclass-Feld ohne
Property/Validierung, `config.py:172` — von außerhalb beschreibbar, keine
technische Hürde; Aufrufmuster über alle drei Konsumenten `sale.py:159`/
`purchase.py:230`/`expenses.py:88` bestätigt: alle laufen innerhalb der
jeweils aktuellen Firmen-Iteration, "ernten nach `orchestrator.run()`"
kann also nichts verpassen). **Zwei Präzisierungen aus Runde 3:**
1. **Ernten von `[]` (echter Fehlschlag, kein "noch nicht versucht")
   bewusst unbedingt, nicht bedingt.** Ein `[]`-Ergebnis bedeutet laut
   `odoo_actions.py:366-371` "versucht, echt leer — nicht erneut versuchen".
   Unbedingtes Ernten propagiert einen einmaligen Fehlschlag (z. B.
   `model_access`-Sperre) für den Rest des Laufs — bewusst so gewählt,
   weil die Alternative (pro Firma neu versuchen) exakt das
   Duplikat-Plan-Risiko zurückbringt, das D12 beheben soll. Ein einzelner
   Fehlschlag deaktiviert Analytics lauf-weit, statt N-fach zu duplizieren.
2. **Kollision mit D6 aufgelöst:** D6s Pro-Firma-Label-Qualifizierung von
   `estimate_record_counts` (z. B. "Kontakte (Firma 1)") **schließt
   `"Kostenstellen"` explizit aus** — bleibt eine einzige, unqualifizierte
   Zeile auf Lauf-Ebene (`= 3`, nicht `×N`), sobald mindestens eine Firma
   Analytics ausgewählt hat. Ohne diesen Ausschluss hätte D6s eigene Regel
   D12s "bleibt exakt 3" automatisch zu `3×N` gemacht.

**D13 — `res.company`-Cleanup: Fix bereits bekannt, jetzt eingeplant statt
offen gelassen.** D1 behauptete, "ein Lauf löschen räumt alles" — stimmt
für `res.company` heute nicht: `run_journal.ARCHIVE_FALLBACK_MODELS`
(`run_journal.py:273`) enthält `res.company` nicht, `delete_run` hätte
keinen Fallback, sobald `unlink` an referenzierten Records scheitert.
**Der Fix ist bereits aus einer früheren Live-Probe dieser Sitzung bekannt**
(R17-Spike, `ODOO_GOTCHAS.md`): `write(active=False)` auf eine Firma mit
bereits angehängtem Warehouse UND auf eine Firma mit vollem Kontenplan
(1312 Konten) funktioniert beide Male, `unlink` scheitert an FK-Constraints
in beiden Fällen. **Entscheidung:** `"res.company"` wird zu
`ARCHIVE_FALLBACK_MODELS` hinzugefügt (ein Set-Eintrag, kein neues
Verhalten) — kein permanenter Rückstand akzeptiert, wo ein bekannter,
billiger Fix existiert. **Zwei Ergänzungen aus Cold-Review Runde 3:**
1. **Wiederverwendete bestehende Firmen werden nie journalisiert (bewusst,
   nicht versehentlich) und daher nie archiviert.** `search_read` (D8b, für
   `target.mode="existing"`) läuft nicht über den `JournalingClient` —
   genau richtig: eine bereits bestehende, potenziell echte Firma darf
   "Lauf löschen" niemals archivieren können. Nur per `create()`
   **selbst angelegte** Firmen landen im Journal und sind damit
   löschbar/archivierbar. Diese Sicherheitseigenschaft ist erwünscht,
   sollte aber explizit im WP stehen, damit niemand versehentlich den
   Ziel-Firma-Resolver so umbaut, dass er auch bestehende Firmen über den
   Journaling-Client anfasst.
2. **Live-Beleg deckt nicht den Fall ab, den `delete_run` tatsächlich
   trifft.** Die zitierte Live-Probe testete Archivieren einer Firma mit
   bereits bestehendem Server-seitigem Rest (Warehouse bzw. Kontenplan),
   nicht eine Firma, die noch **journalisierte** Records anderer Modelle
   hält, während `delete_run` diese in umgekehrter Erzeugungs-Reihenfolge
   abarbeitet. **Prämisse korrigiert (Runde 5): tritt NICHT nur bei
   unterschiedlicher Modul-Auswahl auf, sondern grundsätzlich, sobald N≥2**
   — `delete_run` gruppiert nach erstem Auftreten in umgekehrter
   Journal-Reihenfolge (`run_journal.py:305-311`); Firma Ns `res.company`-
   Erzeugung ist der **erste** Eintrag ihrer Iteration, taucht in
   umgekehrter Reihenfolge also **nach allen Records dieser Firma, aber
   vor allen Records jeder früheren Firma** auf — selbst bei identischer
   Modul-Auswahl je Firma. `res.company`/`stock.warehouse` als Gruppe
   werden dadurch grundsätzlich **vor** Firma 1..N−1s Records abgearbeitet,
   nicht nur im gemischten Fall. Ergebnis bleibt vermutlich unkritisch (beide
   landen laut D13 in `archived`, was bereits als Erfolg gilt), aber der
   Auslöser ist häufiger als ursprünglich angenommen. **WP-Vorgabe:**
   Cleanup live verifizieren an einem 2-Firmen-Lauf — **identische**
   Modul-Auswahl reicht bereits, um den Reihenfolge-Effekt zu triggern,
   eine unterschiedliche Auswahl ist kein notwendiger Testfall mehr, aber
   weiterhin sinnvoll als Zusatzfall. **Zwei Ergänzungen
   (Runde 4):** der Archiv-Fallback ist **ein einziger atomarer `write()`
   über die gesamte Id-Gruppe** (`run_journal.py:332`) — eine einzelne
   nicht archivierbare Firma schickt **alle** Firmen dieses Modells nach
   `failed`, keine nach `archived` (dieselbe Asymmetrie, die die Datei
   bereits für `stock.location` dokumentiert). Der WP-Abnahme-Schritt oben
   braucht außerdem eine explizite Bestanden-Bedingung: `res.company` in
   `archived` statt `deleted` **ist** Erfolg, nicht Fehlschlag — ohne diese
   Klarstellung könnte ein nicht-leeres `failed`/`archived`-Split
   fälschlich als Problem gelesen werden.

**D14 — Wie Transaktions-Records tatsächlich der richtigen Firma
zugeordnet werden: Odoo-Kontext-Injektion auf dem geteilten Client, live
bestätigt, kein Modul-Code-Touch nötig (Cold-Review Runde 4, echter
Blocker — bis hierhin entschied D1-D13 zwar, DASS N Firmen existieren,
aber niemand entschied, WER `sale.py`/`crm.py`/`accounting.py`/`hr.py`/
`project.py`/`recruiting.py`/`documents.py`s erzeugte Records mit der
richtigen `company_id` versieht).** Nachgeprüft: keines dieser 7 Module
setzt `company_id` in seinen Vals-Dicts — nur `purchase.py`/`mrp.py`/
`inventory.py` tun es bereits, über `get_main_company_id` (D3). Ohne
Fix würden N Firmen-Iterationen ihre CRM-Opportunities, Verkaufsaufträge,
Rechnungen, Mitarbeiter, Projekte, Bewerbungen und Dokumente alle in der
API-User-Standardfirma (Firma 1) anlegen — unabhängig davon, welche Firma
gerade "dran" ist. **Live bestätigt 2026-09-04:** ein `sale.order` **ohne**
`company_id` im Vals-Dict, aber mit `context={'allowed_company_ids': [N],
'company_id': N}` im `create()`-Aufruf, bekommt automatisch `company_id=N`
zugewiesen — Odoos Standard-Multi-Company-Kontextmechanismus (normalerweise
über `with_company()` in der UI) funktioniert genauso über die JSON2-API.
Gleiches bestätigt für `create_batch` (2 `sale.order`s in einem Aufruf) und
für ein zweites, unabhängiges Modell (`crm.lead`) — kein Einzelfall.
**Entscheidung:** `odoo_client.py` (nicht `JournalingClient` — Runde 5
bestätigt: `JournalingClient.create`/`create_batch` leiten `context`
unverändert an `super()` weiter, `run_journal.py:192-207`, kein Bypass
möglich) bekommt ein neues, mutable Attribut (`self._default_context:
Optional[dict]`), gemischt **ausschließlich** in den vier öffentlichen
Methoden `create`/`create_batch`/`write`/`call_method` — **explizit
NICHT** in `_post`/`_send` (Runde 5: sonst würde auch jeder `search`/
`search_read`-Aufruf firmen-gescoped, inklusive `get_main_company_id`s
eigener Lookup-Queries und D8bs Fetch — beide brauchen ungefilterten
Zugriff). **Merge-Semantik (Runde 5 präzisiert, Runde 6 korrigiert —
fehlender `None`-Schutz hätte jeden Cleanup-Aufruf zum Absturz gebracht):**
`{**(self._default_context or {}), **(context or {})}` — Schlüssel-Ebene,
nicht Ganzdict-Ersetzung; expliziter Aufrufer-Context gewinnt bei
Schlüssel-Konflikten. `self._default_context` ist `None` außerhalb einer
Firmen-Schleife (z. B. der frische Client, den `delete_run`/`app.py:488`
für Cleanup baut) — `{**None}` wirft `TypeError`, das `or {}` ist also
nicht kosmetisch, sondern verhindert, dass jeder Cleanup-Aufruf sofort
scheitert. **Korrektur (Runde 5): "heute übergibt kein
Modul-Code einen `context`" ist falsch** — `modules/accounting.py:48-50`
übergibt bereits `context={'active_model': ..., 'active_ids': [...]}` an
den `sale.advance.payment.inv`-Wizard-Aufruf. Genau dieser eine Fall
beweist, dass Schlüssel-Ebene-Merge nötig ist (nicht Ganzdict-Ersetzung) —
sonst ginge dieser bestehende Context beim ersten Mehrfirmen-Lauf verloren.
`_execute()`s Schleife setzt `_default_context` pro Iteration auf
`{'allowed_company_ids': [ctx.res_company_ids[0]], 'company_id':
ctx.res_company_ids[0]}`, **unmittelbar nach** der Ziel-Firma-Auflösung
(D10-Korrektur) und **vor** dem `orchestrator.run()`-Aufruf für diese
Firma — siehe die vervollständigte Reihenfolge weiter unten (D11-Ergänzung
Runde 5). **Invariante (Runde 5, für spätere Sitzungen festgehalten):**
der Mechanismus ist nur sicher, weil der Client heute lokal in `_execute()`
gebaut und nie zwischen Läufen wiederverwendet wird (`jobs.py:314`,
Cleanup baut einen eigenen frischen Client, `app.py:488`) — ein künftiges
Client-Pooling (naheliegende Optimierung, §3-D10s Drossel-Zustand lebt ja
schon auf derselben Instanz) würde `_default_context` zu einer Race
Condition machen. Kleiner, gut lokalisierter 🔒-Touch an `odoo_client.py`
(wie D10 selbst), Architekten-Freigabe nötig, aber **kein** Touch an den
7 betroffenen Transaktions-Modul-Dateien **speziell für D14s eigenen
Mechanismus** (Runde 6 präzisiert: `documents.py` gehört zu diesen 7,
wird aber unabhängig davon von D3 angefasst — beide Aussagen widersprechen
sich nicht, D14 allein bräuchte keinen `documents.py`-Touch, D3 tut es aus
einem anderen Grund). Siehe D8-Ergänzung oben für den achten, kleinen
Sonderfall `master_data.py`, und die Fallback-Ergänzung unten (Runde 6)
für zwei weitere, bisher unzugeordnete Stellen in `orchestrator.py`.

**D15 — Kein automatisches Warehouse pro Firma (R17-Fakt) trifft jetzt
drei Module — Fix entschieden (Cold-Review Runde 4, Umfang in Runde 5
korrigiert).** Mit D3/D14 liefert `get_main_company_id` für Firma N
zuverlässig N — aber `purchase.py` (`get_default_warehouse`), `inventory.py`
(dieselbe Lookup) und `mrp.py` (`get_manufacturing_picking_type_id`) finden
dafür **kein** Warehouse, weil keins existiert (R17s eigener, weiterhin
gültiger Fund). **Korrektur Runde 5 — "das ganze Modul tut nichts" gilt
nicht für alle drei:** `purchase.py`/`inventory.py` degradieren tatsächlich
über `logger.warning` + `return` (ganzes Modul leer). `mrp.py` nicht — dort
überspringt ein fehlendes Warehouse nur Fertigungsaufträge
(`mrp.py:400-403`) und die davon abhängigen Qualitätsprüfungen; Produkte,
Stücklisten und Arbeitszentren werden trotzdem angelegt. Für jede neu
angelegte Firma bricht das lautlos zwei komplette Module plus einen Teil
eines dritten. **Entscheidung:** `_execute()`s Schleife ruft für jede
**neu angelegte** Firma (nicht für wiederverwendete bestehende — die haben
vermutlich schon eins) `odoo_actions.create_second_warehouse(client,
ctx.res_company_ids[0])` auf (R14, live-getesteter Pfad, nimmt
`company_id` bereits als Parameter) — Reihenfolge siehe D11-Ergänzung
Runde 5 unten (muss **nach** D14s Kontext-Setzung laufen). Trotz des
irreführenden Namens "second" (historisch, für Firma 1) ist die Funktion
genau das, was hier gebraucht wird: ein Warehouse für eine gegebene
`company_id`.

**Zwei Lücken aus Cold-Review Runde 5, noch ungeklärt:**
1. **Kollision mit dem bestehenden `second_warehouse`-Häkchen — entschieden
   (Cold-Review Runde 6, Lesart 2 statt Runde 5s eigener Präferenz).**
   Dieses Feature existiert bereits und ist Nutzer-sichtbar (`config.py:57`,
   `run_config.py:354`/`:587` Parsing+Vorschau, `static/app.js:545`
   Checkbox `stock-wh2`, `modules/inventory.py:45`/`:66` Konsum) — für
   Firma 1 bedeutet es "leg ein zweites Warehouse an". D15 legt zusätzlich
   **unconditional** ein Warehouse für jede neu angelegte Firma an, auf
   derselben Funktion. **Entscheidung:** das Häkchen gilt **pro Firma**
   (jeder Block trägt ohnehin sein eigenes `modules.stock`, D11), nicht
   nur für Firma 1 — "jede Firma bekommt ihr erstes Warehouse über D15
   (falls neu angelegt), plus ein zweites, falls ihr eigenes Häkchen
   angehakt ist". Der zunächst einfacher wirkende Sonderfall "Häkchen nur
   für Firma 1" widerspricht D9s "unify"-Entscheidung direkt (Firma 1 hätte
   sonst wieder eine Extra-Regel, die D9 gerade abschaffen wollte) und
   kostet unterm Strich **mehr** Code, nicht weniger — die
   Pro-Firma-Modul-Config existiert bereits, "nur für Firma 1" bräuchte
   zusätzlich eine explizite Sonderfall-Prüfung. Vorschau-Zahl
   (`run_config.py:587`) muss entsprechend pro Firma gezählt werden.
2. **Namensgebung.** `odoo_actions.py:123-126` benennt jedes neue Warehouse
   hartcodiert `"Lager 2 (<suffix>)"`/Code `WH2<suffix>` — für eine neue
   Firma ist das aber ihr **erstes und einziges** Warehouse, sichtbar
   falsch beschriftet in einer Demo. Eindeutigkeit über N Aufrufe ist kein
   Problem (Zufalls-Suffix, keine Einmal-pro-Lauf-Annahme in der Funktion)
   — nur das Label ist falsch. Günstiger Fix: nach `create_second_warehouse`
   ein `write()` mit einem passenderen Namen (z. B. aus dem Firmennamen
   abgeleitet), zwei Zeilen, kein Eingriff in R14s eigenen, getesteten Code.

**Weitere Zuordnungen aus Cold-Review Runde 4+5 (keine eigene Nummer,
schließen kleinere Lücken in bestehenden Entscheidungen):**
- **N=1-Regressionskriterium — präzisiert (Runde 6): gilt nur für D14
  allein, nicht für den Endzustand.** D14 setzt `_default_context` auch
  für Firma 1s Iteration, wo heute gar kein `context` übergeben wird. Nach
  R17 Punkt 3 sollte das für diesen API-User wirkungslos sein (keine
  Record-Rule-Maskierung, keine `company_ids`-Einschränkung). **Die
  Kriterium-Formulierung aus Runde 5 ("komplette bestehende Suite bleibt
  unverändert grün") ist am Ende der Implementierung nicht erfüllbar** —
  D11s `companies: [...]`-Payload bricht zwangsläufig
  `test_web_api_unit.py:94`s Einzel-Firma-geformtes `_PAYLOAD`. Gemeint
  und WP-relevant ist: grün **direkt nach D14 allein**, vor D11s
  Payload-Änderung. **Reihenfolge-Vorgabe fürs WP:** D14 (Kontext-
  Injektion) als eigener, erster Commit landen, VOR D11 (Payload-Form) —
  nur so ist dieses Kriterium zu einem bestimmten Zeitpunkt tatsächlich
  prüfbar, nicht erst am Ende, wo es nicht mehr gelten kann.
- **`target`-Block-Validierung braucht einen Besitzer.** Weder
  `build_context_list` noch `build_context` noch `_execute()` prüfen
  heute (im Plan) `target.name` (Pflicht, D11 Korrektur 4), `target.
  company_id` (Pflicht bei `mode="existing"`), einen gültigen `target.mode`,
  oder N gegen die Server-Obergrenze (D11). Ohne das validiert `_as_list`
  nur Listen-Form, nicht Inhalt — ein fehlendes Feld schlägt sonst
  **mitten im Lauf** fehl, Minuten nach dem `202`, statt sofort als
  `ConfigError` → HTTP 400 bei `submit()`. **Entscheidung:**
  `build_context_list` validiert `target` vollständig, bevor irgendeine
  Firma verarbeitet wird — genau der Fehler-Zeitpunkt, den `run_config.py`
  für jedes andere Feld schon garantiert.
- **D6s Label-Qualifizierung braucht einen Ort.** D12s "Kostenstellen
  bleibt unqualifiziert" braucht eine konkrete Stelle, an der diese
  Ausnahme geprüft wird — Entscheidung: `estimate_record_counts` bekommt
  einen optionalen `company_label`-Parameter (leer = heutiges
  Einzel-Firma-Verhalten unverändert); der Aufrufer in `submit()`/
  `/api/preflight` ruft die Funktion pro Firma mit ihrem Label auf und
  qualifiziert danach jedes zurückgegebene Label **außer** `"Kostenstellen"`
  — dieses eine bleibt roh und wird nur beim ersten Auftreten übernommen
  (nicht summiert), da es lauf-weit exakt einmal existiert (D12).
- **Bestehende Firma + `reuse_master_data=false`** bekommt trotzdem frische
  Partner/Produkte (wie eine neue Firma) — **Begründung korrigiert (Runde
  6): nicht D14s Kontext-Injektion** (die greift für `res.partner`/
  `product.product` gerade NICHT automatisch, D8-Ergänzungs Live-Fund),
  **sondern D8-Ergänzungs eigener expliziter `company_id`-Vals-Write in
  `master_data.py`**, der genau danach fragt "hat diese Iteration eine
  reale Ziel-Firma", nicht danach, ob diese Firma neu oder bestehend ist.
  Kein Sonderfall nötig, die Schlussfolgerung bleibt richtig, nur der
  genannte Mechanismus war falsch.
- **`target.country` bleibt auf `res.company`/Kontenplan beschränkt**, geht
  NICHT in `data_factory`s `target_countries`-Parameter für die generierte
  Partner-Adresspool-Auswahl ein — bewusst kleinerer Scope für den ersten
  Wurf (Anforderungs-Punkt 4 nennt den Parameter nur als Beleg, dass die
  Infrastruktur existiert, nicht als Pflicht, sie sofort zu nutzen). Kann
  in einer späteren Erweiterung nachgezogen werden, ohne diesen Plan neu
  aufzurollen.
- **Teststrategie fehlte in D1-D13 komplett**, trotz CLAUDE.mds acht
  Pattern-Pflicht. Betroffen: Pattern 1 (leerer `companies`-Array — sollte
  `_as_list`s Minimalgrenze bereits als `ConfigError` abfangen, nicht als
  leerer Lauf durchlaufen), Pattern 3 (`target.mode` ungültig → Skip mit
  Fehler, kein stiller Crash), Pattern 4 (Read-back pro Firma, nicht nur
  einmal fürs Ganze), Pattern 5 (fehlende `existing_data_consent` bei
  `reuse_master_data=true` → `ConfigError`, nicht stiller Datenschutz-
  Bruch), Pattern 8 (D14s Kontext-Injektion ist kein LLM-Batching-Fall,
  aber dieselbe "nicht pro Record" Disziplin gilt für den Kostenstellen-
  Cache aus D12).

**Bewusst offen gelassen, WP-Autor entscheidet (Cold-Review Runde 6 —
keine Architektur-Fragen mehr, reine Implementierungs-Details):**
- **`/api/preflight`s Antwort-Form unter N Firmen** (Liste pro Firma vs.
  Aggregat) — D6 stellt fest, dass die heutige Firma-1-Form "lügt", ohne
  die neue Form festzulegen.
- **Wer prüft, dass ein `target.company_id` bei `mode="existing"`
  tatsächlich existiert.** `build_context_list` ist strukturell
  Odoo-frei (D10-Korrektur), kann also nicht gegen die Odoo-Instanz
  prüfen — entweder gegen D8as Verbindungs-Zeit-Liste (aktuell keiner
  von `build_context_list`s 6 Parametern) oder in `_execute()`s Resolver
  (Schritt 2).
- **Schicksal des heutigen, ungescopten `fetch_existing_data`/
  `ConnectResult.existing_company_ids`.** Der Existing-Data-Merge-Punkt
  (D11) entfernt sie aus `build_context_list` — bleiben nur noch für die
  Verbindungs-Anzeige ("0 Kunden, 500 Produkte gefunden") relevant, oder
  ganz gestrichen? S16-NEU-Anforderung 3 wollte genau diese Frage geklärt
  sehen.
- **`ctx.res_company_ids`s Kardinalität nie explizit festgeschrieben.**
  Jeder Konsument liest `[0]`; unter D10 hält jede `ctx` genau eine Firma.
  R17s eigene Begründung für die flache Listenform war aber "eine Liste
  sagt nichts über die Anzahl aus" — ein WP-Autor könnte das als "alle N
  Firmen-Ids in eine Liste" lesen, wodurch `[0]` immer Firma 1 wäre und
  D14 jede Iteration lautlos falsch scopen würde. Ein klarstellender Satz
  im WP nötig: `res_company_ids` hält immer genau einen Eintrag, pro
  `ctx`.
- **D8bs Wiederverwendungs-Fetch-Domain, falls der heutige
  `fetch_existing_data`-Domain als Vorlage dient:** `customer_rank > 0`
  filtert Partner, die `data_factory.build_company` nie setzt — ein
  Partner aus einem reinen Stammdaten-Lauf einer früheren Sitzung würde
  vom Wiederverwendungs-Fetch nicht gefunden.

**Eingestuft nach Ladungsfähigkeit (Cold-Review-Kategorisierung, Runde 2
präzisiert):**
- **Bereits entschieden, nicht mehr offen:** Cleanup-Granularität bleibt
  komplett-oder-nichts pro `run_id` — `RunJournal.record()`
  (`run_journal.py:76`) gruppiert nur nach Modell, hat keine
  Firmen-Dimension zum Aufteilen; eine Firmen-Dimension einzuführen wäre
  eine eigene, hier nicht gerechtfertigte Änderung. `res.company`-Cleanup
  selbst: siehe D13.
- **Ohne erneute Review bei Implementierung entscheidbar:** die **exakte
  Zahl** der weichen Obergrenze für N (**Korrektur Runde 2:** die
  Durchsetzung selbst ist **nicht** UI-only — siehe D11s `_as_list`, das
  ist jetzt entschieden; nur der konkrete Zahlenwert bleibt offen); ob die
  Firmenauswahl (bestehend) einen eigenen Zugriffsrechte-Check über
  `model_access` hinaus braucht.
- **Zurückgestuft — braucht doch eine Entscheidung, kein loser Schleifen-
  Zusatz (Cold-Review Runde 3, korrigiert gegenüber Runde 2s Einordnung;
  **Runde 4 fand die Runde-3-Fassung selbst unvollständig — "nichts sonst
  ändert sich" war falsch gegen fünf echte Call-Sites**):** Teilausfall-
  Verhalten. `RunRecord.status` (`jobs.py`) ist heute ein Skalar mit nur
  `STATUS_DONE`/`STATUS_FAILED`, `record.error` ein einzelner String —
  "Firma 3 von 5 schlägt fehl, 1/2/4/5 gelingen" hat **keine
  Repräsentation** in dieser Form. **Entscheidung (Wortschatz):** neuer
  Status-Wert `STATUS_PARTIAL` für "mindestens eine Firma erfolgreich,
  mindestens eine komplett fehlgeschlagen"; `record.error` bleibt ein
  einzelner String (erster Firma-Ebene-Fehlschlag) — die Pro-Firma-Details
  sind bereits über D6s firmen-qualifizierte Modul-Zeilen sichtbar, keine
  strukturierte Aufteilung nötig.

  **Sieben bestehende Zwei-Zustand-Stellen, die ein dritter Status
  bricht, plus eine geprüft-unveränderte (Runde 4 fand 6 Stellen und
  beschriftete sie fälschlich "Fünf" — Runde 5 korrigiert die Überschrift
  und ergänzt zwei weitere Fund-Punkte):**
  1. **`jobs.py:366`** — `record.modules[key] = (MODULE_FAILED if
     record.status == STATUS_FAILED else MODULE_DONE)`. Unter
     `STATUS_PARTIAL` landet das im `else`-Zweig — **jede nie gelaufene
     Modul-Zeile der gescheiterten Firma wird fälschlich "fertig"**,
     genau das Gegenteil dessen, was die Wortschatz-Entscheidung oben als
     Begründung nennt ("Modul-Ebene zeigt bereits, was verloren ging").
     **Aufgelöst (Runde 6):** `failed_company_indices` (siehe die
     Schleifen-Reihenfolge oben) entscheidet das Entweder-Oder — für die
     Modul-Zeilen einer Firma **in** dieser Menge gilt `MODULE_FAILED`,
     für alle anderen (unabhängig vom Gesamt-`record.status`) `MODULE_DONE`,
     nicht mehr nur `record.status == STATUS_FAILED` global.
  2. **`static/app.js:1333`** — `if (data.status === "done" || data.status
     === "failed") { setHidden("btn-cleanup", !data.journal_records); }`
     — der "Lauf löschen"-Button erscheint bei `STATUS_PARTIAL` **nie**,
     ausgerechnet dort, wo ein halb erzeugter N-Firmen-Lauf Cleanup am
     nötigsten braucht.
  3. **`jobs.py:244`** — `prune()` filtert auf `rec.status in (STATUS_DONE,
     STATUS_FAILED)`. Partielle Läufe werden **nie** geprunt — derselbe
     unbeschränkte Leck, den `prune()`s eigener Docstring als Grund für
     seine Existenz nennt, kehrt für jeden Teilausfall zurück.
  4. **`static/app.js:1305`** (D6s Timer-Feature, diese Sitzung) — dieselbe
     Zwei-Wert-Prüfung stoppt den Laufzeit-Ticker; bei `STATUS_PARTIAL`
     tickt "Laufzeit" unbegrenzt weiter, "Verbleibend" wird nie auf `0:00`
     gesetzt.
  5. **`static/app.js:1209-1216`** `statusLabel()` fällt für einen
     unbekannten Status auf `"Ausstehend"` zurück (auch an `:926`,
     Feedback-Modal, wiederverwendet) — ein fertiger Teil-Lauf würde
     "Lauf beendet (Ausstehend)" anzeigen.
  6. **`tests/unit/test_web_api_unit.py:320`/`:438`/`:480`** — pollen
     `while record.status != STATUS_DONE`, bevor sie assertieren; ein
     Teil-Lauf spinnt bis zum Timeout und lässt den Test fehlschlagen statt
     eine echte Aussage zu treffen. **Ergänzung Runde 5:** `:323`s
     direkt anschließendes `assert record.status == STATUS_DONE` gehört
     zur selben Stelle, von Runde 4 nicht separat genannt — alle vier
     Zeilen (drei Polls + der Assert) müssen `STATUS_PARTIAL` als
     gültiges Testende kennen.
  7. **`static/app.js:1293`** (Runde 5) — `setText("stat-status",
     data.status)` rendert den rohen englischen Status ungefiltert;
     `"partial"` erschiene unübersetzt in der UI. Vorbestehende Schwäche
     (heute zeigt es bereits roh `"done"`/`"failed"`), aber D6 überarbeitet
     diesen Bereich ohnehin — im selben WP mit übersetzen.

  **Geprüft, ausdrücklich UNVERÄNDERT (Runde 5, damit niemand das
  versehentlich "repariert"):** `jobs.py:174`s `active_for_session`-Filter
  (`r.status in (STATUS_QUEUED, STATUS_RUNNING)`) bleibt korrekt — ein
  `STATUS_PARTIAL`-Lauf ist per Definition beendet, nicht aktiv, gehört
  also nicht in diese Menge.

  "Restliche Firmen trotzdem versuchen" bleibt der einfache Teil — exakter
  `try`/`finally`-Zuschnitt (welche Schritte umschlossen sind, `None`-
  Schutz beim Ernten, `failed_company_indices`) siehe die vollständige
  Schleifen-Reihenfolge weiter oben (D11-Ergänzung, Runde 6).

### S16-NEU — WP-Sequenz (2026-09-04, nach sechs Cold-Review-Runden)

Größer als jeder bisherige Sprint dieses Repos — S15/R20 war "cross-cutting
(4+ Dateien), bewusst isoliert"; dieser Umfang berührt `config.py`,
`odoo_client.py`, `run_journal.py`, `modules/master_data.py`,
`odoo_actions.py`, `run_config.py`, `web/jobs.py`, `web/app.py`,
`static/index.html`, `static/app.js`, plus neue Tests für jede dieser
Dateien. Sechs statt der üblichen 1-3 Cold-Review-Runden waren nötig,
bevor überhaupt eine WP-Sequenz sinnvoll geschrieben werden konnte — WP2
enthält daher **D14 als eigenen, ersten Commit** (siehe N=1-
Regressionskriterium oben), nicht gebündelt mit dem Rest.

| WP | Inhalt | 🔒 | Voraussetzung |
|---|---|---|---|
| **WP1** ✅ | Architektur-Spike: D1-D15, sechs Cold-Review-Runden, alle live-verifizierten Fakten (Kontext-Injektion, Kontenplan-Mechanismus, Umhäng-Verweigerung, Analytic-Cross-Company, Archiv-Fallback) — siehe oben | nein | — |
| **WP2a** | `odoo_client.py`: D14 (`_default_context`, Merge in `create`/`create_batch`/`write`/`call_method`, `None`-Schutz) als **eigener erster Commit** — N=1-Regressionskriterium hier prüfen (bestehende Suite bleibt grün), bevor WP2b/WP3 folgen | ja | WP1 |
| **WP2b** | Restliche Infrastruktur: `config.py` (`RunContext.res_company_ids`, D2) 🔒; `master_data.py` (D8-Ergänzung, expliziter `company_id`-Write in `_create_partners`/`_create_products`); `odoo_actions.py` (D3: `get_main_company_id`/`get_main_company_info` werden `company_id`-Parameter-bewusst, 5 Call-Sites in `expenses.py`/`mrp.py`/`inventory.py`/`purchase.py`/`documents.py` angepasst; `create_second_warehouse` unverändert, nur neu aufgerufen); `run_journal.py` (D13: `"res.company"` zu `ARCHIVE_FALLBACK_MODELS`) | ja | WP2a |
| **WP3** | `run_config.py`: `build_context_list` (D11, 6 Verbindungs-Parameter, kein `existing_company_ids`/`existing_product_ids`), `_as_list`-Helper + `target`-Block-Validierung (Name/Land/Firma-Id/Server-Obergrenze, als `ConfigError`), `validate_consent` (Konsens-Entscheidung: `reuse_requested`-Kwarg + `or`-Prädikat), Consent-Injektion pro Block, D8bs `company_id`-gescopte Fetch-Variante, `estimate_record_counts`/`active_progress_keys` pro Firma mit `company_label`-Parameter (D6, "Kostenstellen" ausgenommen, D12) | ja | WP2b |
| **WP4** | `web/jobs.py`: die vollständige 8-Schritt-Pro-Firma-Schleife in `_execute()` (D10-Korrektur/D14/D15/D8b/D12, `job["ctx"]` → `job["contexts"]`), `STATUS_PARTIAL` + `failed_company_indices` + alle 7 betroffenen Zwei-Zustand-Stellen (`jobs.py:366`/`:244`, Tests), Pro-Iteration-`on_start`/`on_done`-Closures (D6) | ja | WP3 |
| **WP5** | `web/app.py`: `/api/preflight` pro Firma (D6), `MODULE_LABELS`-Fallback-Fix (`:403`, D6); `static/index.html`/`app.js`: neuer Firmenauswahl-Bildschirm + Pro-Firma-Konfigurationsbildschirm (D9), firmen-qualifizierte Fortschrittsanzeige inkl. Anzeige-Label aus `target.name` (D6/D11), die 7 STATUS_PARTIAL-Frontend-Stellen (`app.js:1333`/`:1305`/`:1209-1216`/`:926`/`:1293`) | nein | WP4 |
| **WP6** | Peer-Review vor Merge (S5-S15-Verfahren, Diff statt Plan-Text), grüner Live-`test_suite.py` — inkl. der drei live zu verifizierenden Fragen aus D13 (Cleanup-Reihenfolge bei N≥2, identische Modul-Auswahl reicht bereits) und D15 (Warehouse-Erzeugung unter korrekter Firma) | — | WP2-WP5 Code steht |

**Pro Arbeitspaket verbindlich:** dieselben Testing Design Patterns wie
jedes bisherige Sprintpaket (siehe CLAUDE.md und die Pattern-Zuordnung
oben, "Weitere Zuordnungen aus Cold-Review Runde 4+5").

---

## 6. Umsetzungsreihenfolge — ausführliche Sprint-Tabelle (Stand vor 2026-09-05)

`ROADMAP.md` §5 trug die Begründungen, Testzahlen, Review-Funde und PR-Links jedes
Sprints in je einer Tabellenzelle — die S11- und S12-Zellen waren rund 1450 Zeichen
lang. Am 2026-09-05 auf `Sprint | Inhalt` reduziert; die Langfassung steht hier
unverändert.

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
| **S12 — Quick Wins** ✅ (2026-09-02) | R11 (Lost Opportunities, archiviert — `ROADMAP_ARCHIVE.md`s R11-Statusblock), R16 Produkt-Ebene (Barcode, WP1 erledigt, Location-Ebene bleibt in S13 offen — R16-Abschnitt bleibt daher hier), R19 (Expenses, archiviert — `ROADMAP_ARCHIVE.md`s R19-Statusblock) — WP3✅→WP1✅→WP2✅→WP4✅→WP5✅, siehe "S12 — WP-Sequenz" in `ROADMAP_ARCHIVE.md` §5 | Drei kleine Erweiterungen, aber R19s Registrierungskette und R11s `orchestrator.py`-Einfügung stellten sich im Cold-Review als echte Blocker heraus (4 gefunden, alle eingearbeitet) — "additiv, Freigabe ist Formsache" war zu pauschal. R11s `orchestrator.py`-Einfügung (zwischen `sale`/`hr`) hat jetzt echte Architekten-Freigabe (2026-09-02), kein reiner Anhang wie bei R19/S6-S8. WP3 live verifiziert: beide offenen Verhaltensfragen (hr.expense `approval_state`, crm.lead `won_status`) brauchen nur `write()`, keine Action-Methoden. WP5s Vor-Merge-Review fand 2 weitere Blocker (genehmigte `hr.expense` blockierte `delete_run` komplett, `linked_opportunity_ids` ohne Testabdeckung auf dem echten Code-Pfad), beide live gefixt. Unit 371/371, Live-Integration 87/87 grün — WP1s/WP4s Läufe hatten zusätzlich den pre-existing, unabhängigen `ODOO_ACTIONS`-Rate-Limit-Flake (D10), inzwischen als Backlog-Item erfasst. Sprint abgeschlossen, [PR #29](https://github.com/pahuodoo/odoo-daten-generator/pull/29) nach `main` gemerged (2026-09-02) |
| **S13 — Lager-Tiefe** 🆕 | R14 (Multi-Warehouse), R15 (Lagerplätze, inkl. R16 Location-Ebene), R13 (Seriennummern-/Chargenverfolgung, MRP-Anbindung gestrichen — siehe R13) | Alle drei bauen auf `inventory.py`/`stock.*`-Modellen auf. R13 braucht R15 nicht zwingend (`stock.lot.location_id` ist optional), profitiert aber von den gleichzeitig entstehenden Sub-Locations — ein Sprint für den gesamten Lager-Realismus-Ausbau |
| **S14 — Prozess-Tiefe** 🆕 | R12 (Nachbestellregeln, in `inventory.py`), R18 (Quality Checks, Erweiterung des bestehenden `mrp.py`-Pfads) | Beide sind eher "MRP/Inventory-Investition aus S1/S8 weiter ausnutzen" als "auf S13 aufbauen" (Peer-Review-Korrektur: `quality.point` hat kein Location-Feld, "an `wh_qc_stock_loc_id` andocken" war keine reale Mechanik) — dennoch sinnvoll in einem Sprint gebündelt, da beide dieselbe operative Prozess-Ebene vertiefen |
| **S15 — Analytic Accounting (R20)** 🆕 | `account.analytic.plan`/`account.analytic.account` + `analytic_distribution`-Wiring über `sale.py`/`purchase.py`/`accounting.py`/`expenses.py` | Cross-cutting (4+ Dateien) bewusst isoliert in eigenem Sprint, damit der Review-Diff überschaubar bleibt; profitiert von R19 (Expenses, S12), falls dessen Zeilen mit-verkabelt werden sollen |
| **S16-NEU — Multicompany, N Firmen (R17, ersetzt Minimal-Scope)** ✅ Architektur freigegeben (2026-09-04) | N Firmen (neu-oder-bestehend), pro Firma Branche+Land+voller Pipeline-Durchlauf. D1-D15, sechs Cold-Review-Runden (Architektur konvergiert, Runde 6 bestätigt), WP-Sequenz WP1-WP6 geschrieben — siehe "S16-NEU — Architektur-Spike"/"S16-NEU — WP-Sequenz" in §5 dieses Dokuments | Größter Einzel-Umfang aller S-Sprints bisher — berührt 10+ Dateien. Ursprünglicher Minimal-Scope (eine Firma, dreifach cold-reviewed) durch neue Nutzeranforderungen ersetzt, nicht durch Review-Fund — dessen Instanz-Fakten (Umhäng-Verweigerung, keine Record-Rule-Maskierung, u. a.) bleiben gültig und sind in D1-D15 eingeflossen. Zentraler Live-Fund: Odoo-Kontext-Injektion scoped Transaktions-Records ohne Modul-Code-Touch (D14), aber NICHT für `res.partner`/`product.product` (D8-Ergänzung, `master_data.py` braucht doch einen kleinen Touch). WP2a landet D14 bewusst als eigenen ersten Commit (N=1-Regressionskriterium). **Nachtrag 2026-09-05:** umgesetzt und gemerged ([PR #35](https://github.com/pahuodoo/odoo-daten-generator/pull/35)) — der Zellentext oben ist der Planungsstand vor der Implementierung, Ergebnis siehe R17-Statusblock. |

**Pro Arbeitspaket verbindlich** (aus CLAUDE.md Testing Design Patterns):
- Empty-Pool-Guards (P1) für jede neue `random.choice/sample`-Stelle
- LLM-None-Guards (P2) für jeden neuen/geänderten LLM-Pfad
- Feature-Flag-Skip (P3) für jede neue GUI-Option
- Read-Back-Validierung (P4) in jedem neuen Integrationsschritt
- 🔒-Punkte (Pipeline-Reihenfolge, JSON2-Fallbacks, Config-Schema, Cache-Namen) vor Umsetzung explizit freigeben lassen

---

## 7. Nachträglich archiviert (2026-09-05) — R12, R18, R20

> **Zur Belegtiefe dieser drei Blöcke:** aus dem ausgelieferten Code rekonstruiert
> (2026-09-05), nicht aus dem Sprint-Verlauf — sie standen bis dahin in `ROADMAP.md`
> fälschlich als „🆕 Geplant", obwohl S14/S15 sie längst umgesetzt hatten. Sie sind
> deshalb knapper als die Statusblöcke, die direkt nach ihrem Sprint entstanden
> (vgl. R11/R19). Jede Aussage oben ist an der genannten Datei-/Zeilenstelle prüfbar;
> was der Code nicht hergibt — Review-Funde, Testzahlen — steht hier bewusst nicht.


### R12 ✅ Erledigt (als Teil von Sprint S14) — Nachbestellregeln / Replenishment

`modules/inventory.py`: `stock.warehouse.orderpoint`-Regeln für einen konfigurierbaren
Anteil der Produkte (`orderpoints_pct`), Min-/Max-Mengen als eigenständige Config-Werte
(`orderpoint_min_qty`/`orderpoint_max_qty`), nicht aus `avg_qty` abgeleitet — sonst
degenerieren sie auf dem `avg_qty=0`-Pfad zu 0.0 (S14/Befund 7, dieselbe Begründung wie
bei `tracking_serial_max`).

Der Orderpoint-Zweig ist vom Quant-Seeding **entkoppelt**: ein Lauf mit `avg_qty=0` und
`orderpoints_pct>0` erreicht die Schleife weiterhin (`inventory.py:99-104` — bewusst kein
`return` im Vorab-Gate). Regeln zielen nur auf Produkte, die dieser Lauf nachweislich
selbst angelegt hat (`:125`), und respektieren die Odoo-Eindeutigkeit über
(Produkt, Warehouse, Location) (`:129`).

<details>
<summary>Ursprünglicher Planungstext aus <code>ROADMAP.md</code></summary>

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

</details>

### R18 ✅ Erledigt (als Teil von Sprint S14) — Quality Checks

`modules/mrp.py`: `quality.point`-Erzeugung (`create_quality_point`, `:105`) hinter dem
Flag `create_quality_points`, dazu ein konfigurierbarer Fehleranteil `quality_fail_pct`
(`:133-134`), der nur wirkt, wenn das Flag gesetzt ist.

Zwei live geklärte Punkte: `quality.alert.team` wird gesucht statt erzeugt (`:467`), und
`action_confirm` allein erzeugt **keine** `quality.check` (`:507-511`) — der Pfad ist
zusätzlich auf eine eigene `model_access`-Probe gegattert, damit ein blockiertes
`quality.check` nicht das ganze MRP-Modul mitreißt. Die Zuordnung
Fertigungsauftrag→Stückliste (`mo_bom_map`, `:395`) existiert genau für diese Verknüpfung.

<details>
<summary>Ursprünglicher Planungstext aus <code>ROADMAP.md</code></summary>

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

</details>

### R20 ✅ Erledigt (2026-09-03/04, als Sprint S15) — Analytic Accounting

`account.analytic.plan` + `account.analytic.account` als Kostenstellen, lazy und memoisiert
über `odoo_actions.get_or_create_analytic_accounts`; `analytic_distribution` verkabelt in
`sale.py`, `purchase.py`, `expenses.py` und `project.py`, jeweils über einen eigenen,
unabhängig konfigurierbaren Anteil (`analytic` shape in `config.py`).

Cross-cutting über 4+ Dateien, deshalb bewusst als eigener Sprint isoliert. `RunContext.
analytic_account_ids` ist `Optional[List[int]]` mit `None`-Sentinel, nicht bare Liste: drei
unabhängige Module können den Helfer als erstes aufrufen, und eine reine
Truthiness-Prüfung könnte „noch nie versucht" nicht von „versucht, nichts gefunden"
unterscheiden — würde also bei jedem weiteren Aufruf einen zweiten Plan anlegen.
`odoo_actions.MODEL_ACCESS_PROBES` deckt beide Modelle mit ab (`:299-310`).

<details>
<summary>Ursprünglicher Planungstext aus <code>ROADMAP.md</code></summary>

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

</details>

### S17 — WP-Sequenz (Schema-Härtung: D5, D16, D8-Teil)

**Stand 2026-09-06, Branch `s17-schema-haertung`.** Planungsdokument war
`.claude/plans/`-lokal, in Register-und-Matrix-Form nach dem `sprint-review`-Skill.
Drei Cold-Review-Runden vor der ersten Code-Zeile (Verlauf: `SPRINT_LOG.md`).

| WP | Inhalt | Commit |
|---|---|---|
| WP0 | Architekten-Freigabe (`config.py` 🔒) + drei Cold-Review-Runden | — |
| WP1 | Sicherungsnetz: Netz A (`build_selections` → Golden) + Netz B (7 Module → aufgezeichnete Odoo-Aufrufsequenz), erzeugt auf `main` vor der ersten Refactor-Zeile, danach eingefroren | `8aa3f51` |
| WP2 | D16: `ctx.company_ids` → `partner_company_ids`, inkl. `ConnectResult.existing_company_ids` und `build_context`-Kwarg; D8-Beifang (`# Besitzer:`-Zeilen) | `3e6c2a3` |
| WP3 | D5: 10 `Optional[<X>Config] = None`-Dataclasses, 5 `isinstance`-Guards ersetzt, ~78 Teststellen | `e7d586c` |
| WP4 | Dokumentation | dieser Commit |

**Vier Entscheidungen, die beim Weiterarbeiten gelten:**

1. **Objekt vorhanden = Feature aktiv** (`S17-D2`). Die `enabled`-Schlüssel von
   `crm_chatter`/`crm_activities`/`hr_timeoff`/`analytic` entfallen. Tragfähig, weil
   `build_selections` alle zehn Felder ausschließlich innerhalb eines `if _enabled(...)`-Blocks
   zuweist und kein Produktionscode ein Config-Objekt teilbefüllt konstruiert — beides in
   zwei Review-Runden unabhängig nachgeprüft.
2. **Dataclass-Defaults = Fallbacks der Lesestellen** in `modules/`, nicht die
   Payload-Defaults aus `build_selections`. Die weichen breit ab (`stock.avg_qty` 0 vs. 50,
   `mrp.num_manufacturing_orders` 0 vs. 5, `hr_recruitment.create_skills` False vs. True).
   Nur die Lesestellen-Werte halten die Umschreibung der Testkonstruktionen
   verhaltenserhaltend: im Produktionspfad wird ein Dataclass-Default nie gezogen.
3. **`<feld>={}` in Tests wird `None`, nie `<X>Config()`.** Ein defaultkonstruiertes Objekt
   ist truthy und kippt Pattern-3-Tests von „aus" auf „an".
4. **`orchestrator.py` blieb bis auf zwei Rename-Zeilen unangetastet.** Der Gate-Block
   `elif not sel: continue` funktioniert mit `None` unverändert. Das ursprünglich geplante
   strikte `ModuleSelections.get` wurde ausgegliedert → `ROADMAP.md`s D20.

**Die teuerste Falle des Sprints:** fünf `isinstance(<cfg>, dict)`-Guards
(`documents.py`, `inventory.py`, `mrp.py`, `recruiting.py`, `master_data.py`).
`isinstance(MrpConfig(), dict)` ist `False` — sie hätten vier Module still abgeschaltet und
weiterhin Erfolg an `on_module_done` gemeldet, ohne je eine `.get()`-Zeile zu erreichen.
Sie entziehen sich damit genau der `AttributeError`-Absicherung, auf die ein solcher
Typ-Refactor sich sonst stützt. Gefunden in Cold-Review Runde 1.


---

### D6 ✅ Namens-Hygiene: `gemini` → `llm` — erledigt (S18, 2026-09-06)

Ursprünglicher Text: *„Parameter heißt in allen Modulsignaturen `gemini`, Provider ist
primär Groq; `RunContext.gemini_model_name` ist ungenutztes Erbe. Umbenennen (`llm`,
`llm_model_name`), rein mechanisch."*

**Umgesetzt in zwei Schritten.** WP1: `RunContext.gemini_model_name` **gelöscht**, nicht
umgebannt — das Feld hatte repoweit null Lesestellen, war also write-only; damit wurde der
`llm_model_name`-Parameter tot und fiel mit weg (beide `run_config.py`-Signaturen, die
Weiterreichung aus `build_context_list`, vier Aufrufstellen in `web/`). Config-Schema ist
🔒; Architekten-Freigabe zum Löschen statt Umbenennen am 2026-09-06 erteilt.
WP2: 231 Bezeichner in 32 Dateien — das bare `gemini` plus die zusammengesetzten
`gemini_stages_map`, `mock_gemini`, `gemini_empty`. Dazu 11 Prosa-Zeilen, die den
Fallback-Provider als Namensgeber für die LLM-Schicht benutzten.

**Vier Fundstellen bleiben bewusst stehen** und dürfen nicht „mit aufgeräumt" werden:
`server_config.py:77,78` (`[gemini]` ist ein `config.ini`-Sektionsname — Umbenennen bricht
still bestehende Betreiber-Konfigurationen), `connect_service.py:187,189`
(Provider-Literale), `llm_service.py:91,121` und `:6,78` (meinen tatsächlich Gemini als
Fallback-Provider), `static/index.html` (Anzeigetext).

**Nachweis ohne neues Sicherungsnetz.** S17s bestehendes Netz war bereits das richtige
Instrument: Netz A (`asdict(ModuleSelections)`) und Netz B (aufgezeichnete
Odoo-Call-Sequenz je Modul) blieben mit **unveränderten Goldens** grün. Kein Golden konnte
sich bewegen — kein Modul las das gelöschte Feld je. Ein eigens gebautes Netz über
`RunContext`-Feldwerte wäre sogar unkonstruierbar gewesen, weil WP1 eines dieser Felder
löscht.

---

### D8 ✅ Kleinigkeiten — abgeschlossen (S18)

**Abgeschlossen 2026-09-06 mit S18** — 6 erledigt, 2 geprüft und verworfen, **0 offen**:

- ✅ **Erledigt (S17/WP5):** `test_mrp_live.py` gelöscht, nicht verschoben. Alle vier geprüften Funktionen sind in `tests/integration/test_mrp.py` abgedeckt, kein Runner rief das Skript auf, und sein eigener Docstring ordnete die Löschung an („Delete this file after all steps pass").
- ✅ **Erledigt (S17, war ohnehin stale):** der `.claude/worktrees/docker-autoupdate/`-Punkt. `git worktree list` zeigt nur `main`, `.claude/worktrees/` ist leer — der Worktree existierte zum Zeitpunkt der Erfassung schon nicht mehr.
- ✅ **Erledigt (S17/WP2):** `# Besitzer:`-Zeilen in `config.py` für die Felder mit mehr als einem Schreiber. Korrigierte Liste gegenüber der ursprünglichen Erfassung: `supplier_ids` (2), `bill_ids` (2), `product_ids` (5), `partner_company_ids` (4) und **`analytic_account_ids`** (2, fehlte). **`confirmed_order_ids` gehörte nie dazu** — viele Leser, aber genau ein Schreiber (`sale.py:121`).
- ⚪ **Geprüft und verworfen (2026-09-05):** Lint-Ausbau `ruff --select B,C901` gemessen — 97 Treffer (56 × C901, 41 × B: B905 19, B008 10, B904 7, B007 3, B023 2). **Kein einziger echter Bug** darunter: C901 feuert flächig auf die absichtlich langen prozeduralen Modul-Dateien (400–550 Zeilen), beide B023-Fälle liegen in Tests und sind harmlos (Closure wird noch in derselben Schleifen-Iteration aufgerufen). Kein eigenes Item — `ruff.toml`s `select = ["F"]`-Begründung bleibt gültig. Hier notiert, damit der Vorschlag nicht ungemessen wiederkehrt.
- ✅ **Erledigt (durch Umbau, nicht gezielt):** die ursprüngliche `odoo_client._post:46`-Stelle mit dem immer-wahren `response is not None`-Check existiert so nicht mehr — `odoo_client.py`s Fehlerbehandlung wurde in S9/S10 komplett umgebaut (`_record_failure`-Frame-Stack). Die verbleibenden `response is not None`-Checks (z. B. `create_batch`s HTTPError-Handler, `has_create_access`) sind echte Null-Checks, keine toten Bedingungen mehr.
- ✅ **Erledigt:** `LLMService.ping()` existiert (`llm_service.py:262`) und wird von `connect_service.py:245` verwendet — ohnehin gegenstandslos, da `gui.py` seit S9 komplett entfernt ist.
- ✅ **Erledigt (S18/WP3):** die Provider-Wahl hat ein Feld. Die Backend-Kette existierte bereits vollständig — `connect_service.detect_provider(llm_key, explicit)` (`:186`), `probe(llm_provider=…)`, `web/app.py` liest `body.get("llm_provider")`, `web/session.py` speichert, `web/jobs.py` löst für den Lauf erneut auf; es fehlte nur das Frontend-Feld. Default „Automatisch" lässt das Feld im Request weg, damit snifft `detect_provider` wie bisher am `gsk_`-Präfix. Die alte Fundstellenangabe `connect_service.py:126` war stale, korrekt ist `:189`.
- ⚪ **Geprüft und verworfen (2026-09-06, S18):** der unconditional `fetch_name_suggestions`-Aufruf (`orchestrator.py:61`, nicht `:54` — auch diese Nummer war stale). Drei Befunde entkräften den Punkt: (a) der Aufruf endet in `_cached_llm_call` (`llm_service.py:337`), Wiederholungsläufe lesen eine JSON-Datei statt das LLM zu fragen; (b) **sieben** Module lesen `ctx.name_banks` unabhängig von `skip_master_data` (`master_data`, `crm`, `hr`, `project`, `mrp`, `accounting`, `purchase`); (c) die beiden Fallback-Seeder laufen bei `orchestrator.py:70-71` **außerhalb** des Gates und lesen `name_banks` (`:146,159`). Das in der ursprünglichen Erfassung genannte `skip_master_data`-Gate ist damit das falsche Gate — ein korrektes wäre „braucht irgendein gewähltes Modul die Namensbänke", was in nahezu jeder realen Konfiguration wahr ist. Hier notiert, damit der Vorschlag nicht ungemessen wiederkehrt.


---

### S18 — WP-Sequenz (Namens-Hygiene: D6, D8), abgeschlossen 2026-09-06

> **Merge-Vorbehalt:** dieser Eintrag ist vorab geschrieben; S18 liegt beim Verfassen auf
> `s18-namens-hygiene`. Planning-document rule 3 verlangt den Umzug *beim* Merge — beim
> Mergen prüfen, dass diese Zeile entfernt wird.

Geplant nach dem `sprint-review`-Verfahren, drei kalte Review-Runden (6 / 4 / 3 Blocker).
**D21 war ursprünglich WP1 und wurde nach Runde 3 herausgelöst** — 8 der 13 Blocker lagen
in seiner Mechanik, und Runde 3 fand erneut Blocker in genau dem, was Runde 2 gefixt hatte
(Abbruchbedingung `sprint-review`-Skill §4). Der durchgearbeitete Entwurfsstand steht in
`ROADMAP.md`s D21-Abschnitt, nicht hier — D21 ist weiter offen.

| WP | Inhalt | Nachweis |
|---|---|---|
| WP1 | `RunContext.gemini_model_name` löschen, `llm_model_name`-Parameter mit | Unit 493/493, Live 95/95; S17-Goldens unverändert |
| WP2 | `gemini` → `llm` als Bezeichner (231 Stellen) + 11 Prosa-Zeilen | Unit 493/493, Live 95/95; S17-Goldens unverändert |
| WP3 | Anbieter-Auswahlfeld im Frontend (D8) | Unit 494/494 (neuer P3-Test), Live 95/95; live gegengeprüft: Anbieter=Gemini + `gsk_`-Schlüssel → Request geht an `generativelanguage.googleapis.com` |
| WP4 | Doku: `ROADMAP.md`, `ROADMAP_ARCHIVE.md`, `SPRINT_LOG.md`, `CLAUDE.md`, `bandit.yaml` | — |

**Nebenbefund aus WP4:** `bandit.yaml`s B101-Skip war gegenstandslos — seine Begründung
nannte `test_mrp_live.py`, das S17 gelöscht hat, und bandit läuft ohne den Skip grün (null
Treffer). Entfernt statt die falsche Begründung umzuschreiben; eine Sicherheits-Lint-
Unterdrückung, die nichts unterdrückt, soll nicht dastehen.
