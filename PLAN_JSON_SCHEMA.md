# Plan-JSON-Schema (R7 Spike)

Grundlage für zwei Dinge:

1. **System-Prompt-Text für den Gemini-Gem** (Abschnitt 4) — der Gem soll ausschließlich
   gegen dieses Schema schreiben.
2. **Validierungs-Referenz für den Spike** (Abschnitt 5) — woran gemessen wird, ob
   Gem-Output brauchbar ist oder manuelle Eingabe gewinnt.

Alle Feldnamen/Typen/Defaults sind aus `odoo-daten-generator/config.py` und den
`modules/*.py`-Verbrauchsstellen verifiziert (Stand 2026-08-04), nicht angenommen.

## 1. Top-Level-Struktur

```json
{
  "criteria": { ... },
  "modules": { ... }
}
```

Nur diese zwei Keys auf oberster Ebene. Verbindungsdaten (URL/DB/API-Key), Sprache,
LLM-Modell, `installed_modules`, `feature_flags` gehören **nicht** ins Plan-JSON — die
kommen aus der bestehenden Connection-Screen-Erkennung zur Laufzeit, nie vom Gem.

## 2. `criteria` — entspricht `DemoCriteria`, alle Felder Pflicht

| Key | Typ | Bedeutung | Hinweis |
|---|---|---|---|
| `mode` | string | `"master"` oder `"both"` | exakt einer der zwei Werte, nichts anderes. `"master"` = nur Stammdaten, `modules` wird dann ignoriert |
| `industry` | string | Branche | Empfohlen: `"IT"`, `"Fertigung"`, `"Handel"` — für diese drei existieren kuratierte Fallback-Daten (`fallback_data.py`). Andere Werte funktionieren (gehen an LLM-Prompts), verlieren aber das Fallback-Netz falls LLM ausfällt |
| `num_companies` | int ≥ 0 | Anzahl Firmen (Kunden) | |
| `num_delivery_contacts` | int ≥ 0 | Lieferkontakte pro Firma | |
| `num_invoice_contacts` | int ≥ 0 | Rechnungskontakte pro Firma | |
| `num_other_contacts` | int ≥ 0 | sonstige Kontakte pro Firma | |
| `num_services` | int ≥ 0 | Dienstleistungsprodukte | |
| `num_consumables` | int ≥ 0 | Verbrauchsprodukte | |
| `num_storables` | int ≥ 0 | Lagerprodukte | |

## 3. `modules` — entspricht `ModuleSelections`, alle Felder optional

Fehlender Key → Dataclass-Default greift (Spalte "Default"). Bei `mode="master"` wird
diese Sektion nicht ausgewertet.

| Key | Typ | Default | Bedeutung |
|---|---|---|---|
| `crm` | int | 0 | Anzahl Opportunities |
| `leads` | int | 0 | Anzahl Leads |
| `sale` | int | 0 | Anzahl Verkaufsaufträge |
| `account` | int | 0 | Anzahl Rechnungen/Belege |
| `create_bank_transactions` | bool | false | |
| `hr` | int | 0 | Anzahl Mitarbeiter |
| `project` | int | 0 | Anzahl Projekte |
| `tasks_per_project` | int | 10 | |
| `hr_timesheet` | int | 0 | 0 = aus, >0 = an |
| `mrp` | object | `{}` | siehe 3.1 |
| `hr_recruitment` | object | `{}` | siehe 3.2 |
| `hr_timeoff` | object | `{}` | siehe 3.3 |
| `crm_chatter` | object | `{}` | siehe 3.4 — **Falle, unbedingt lesen** |
| `crm_activities` | object | `{}` | siehe 3.5 |

### 3.1 `mrp`

```json
{"num_products": 3, "components_per_bom": 4, "sub_boms_per_product": 1,
 "num_workcenters": 3, "num_manufacturing_orders": 10, "create_quality_points": false}
```

| Key | Typ | Default | |
|---|---|---|---|
| `num_products` | int | 0 | Modul läuft nur wenn > 0 |
| `components_per_bom` | int | 1 | |
| `sub_boms_per_product` | int | 0 | wird auf `components_per_bom` gekappt falls größer |
| `num_workcenters` | int | 3 | |
| `num_manufacturing_orders` | int | 0 | |
| `create_quality_points` | bool | false | |

### 3.2 `hr_recruitment`

```json
{"num_jobs": 2, "num_candidates": 8, "create_skills": true,
 "num_skill_types": 3, "skills_per_type": 4}
```

Modul läuft nur wenn `num_jobs > 0` oder `num_candidates > 0`.

### 3.3 `hr_timeoff`

```json
{"enabled": true, "entries_per_employee": 2, "avg_length_days": 5,
 "past_future_pct": 30, "timescale_days": 180, "validate_pct": 100}
```

`enabled` wird explizit geprüft (`.get("enabled")`) — ohne `"enabled": true` läuft
das Modul **nicht**, auch wenn andere Keys gesetzt sind. `past_future_pct` = Anteil
Zukunft in %, `validate_pct` = Anteil automatisch genehmigt in %.

### 3.4 `crm_chatter` — Falle

```json
{"style": "mixed", "messages_per_opp": 3}
```

`style` ∈ `"notes_only" | "mixed" | "full_email"` (Default `"mixed"`), `messages_per_opp`
Default `4`.

**Wichtig, weicht vom Muster der anderen Dicts ab:** Der Code prüft **nicht** ein
`enabled`-Feld, sondern nur ob das Dict nicht-leer ist (`if ctx.module_selections.crm_chatter:`).
Ein Plan mit `{"enabled": false}` ist ein nicht-leeres Dict → Chatter läuft trotzdem,
mit Defaults `style="mixed"`, `messages_per_opp=4`. **Zum Deaktivieren: Key ganz
weglassen oder `{}` senden — niemals `{"enabled": false}` erwarten, dass es wirkt.**

### 3.5 `crm_activities`

```json
{"enabled": true, "past_pct": 40, "today_pct": 20}
```

`enabled` wird hier (anders als bei `crm_chatter`) explizit geprüft. `past_pct + today_pct`
muss ≤ 100 sein — der Rest ist implizit `future_pct`.

## 4. System-Prompt-Text für den Gem (Entwurf)

```
Du erstellst Demo-Datenpläne für Odoo-Vertriebsdemos. Für jede Anfrage lieferst du
GENAU ZWEI Teile:

1. Einen kurzen Ablaufplan in Prosa (für den Menschen, der die Demo hält) — welche
   Story wird erzählt, welche Odoo-Screens werden in welcher Reihenfolge gezeigt.

2. Einen JSON-Codeblock, der GENAU dem folgenden Schema entspricht. Halte dich
   strikt an Feldnamen, Typen und die vorgegebenen Enum-Werte. Erfinde keine
   zusätzlichen Keys. Lass Module, die im Ablaufplan keine Rolle spielen, auf
   ihrem Default (meist 0 / {} / weggelassen).

[Abschnitt 2 und 3 dieses Dokuments hier einfügen]

Der JSON-Teil muss zum Ablaufplan-Text inhaltlich passen: wenn der Plan z.B. eine
Reklamation aus der Fertigung erzählt, muss "mrp" entsprechend befüllt sein — nicht
nur der Text darf die Geschichte erzählen, die Zahlen müssen sie auch erzeugen.
```

## 5. Spike-Validierungscheckliste

Gegen 3-5 vom Gem erzeugte Pläne (unterschiedliche Szenarien) prüfen, jeweils per
Wegwerf-Skript gegen dieses Schema:

1. **Enum-Drift** — `mode` exakt `"master"`/`"both"`? `crm_chatter.style` exakt eine
   der drei erlaubten Strings?
2. **Typen** — Zahlen als JSON-Zahl (nicht `"8"` als String), Booleans als `true`/`false`
   (nicht `"true"`)?
3. **Nested-Shapes** — Keys in `mrp`/`hr_recruitment`/`hr_timeoff`/`crm_chatter`/
   `crm_activities` exakt wie oben, keine Umbenennungen?
4. **Unbekannte Keys** — wie viele erfundene/falsch geschriebene Keys pro Plan? (Loader
   ignoriert sie mit Warnung — zu viele = Schema wird nicht verstanden)
5. **Numerische Plausibilität** — Werte in sinnvollem Rahmen (kein `num_companies: 500`)?
6. **Ablaufplan-JSON-Kohärenz** — erzählt der Prosa-Teil etwas, das die Zahlen nicht
   abbilden (z.B. Fertigungs-Story, aber `mrp` leer)?

**Exit-Kriterium:** Braucht ein typischer Gem-Plan mehr Nachkorrekturen an den GUI-Reglern
als er an Klicks gespart hätte, gewinnt manuelle Eingabe — Feature wird nicht weiterverfolgt.
