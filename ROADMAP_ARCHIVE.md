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
