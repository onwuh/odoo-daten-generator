---
name: sprint-review
description: Regelwerk für Sprint-Planung und Cold-Review in diesem Repo — Plan-Form (Entscheidungsregister + Abdeckungsmatrix), Briefing für den fremden Review-Agenten, repo-spezifische Prüfliste, Abbruchbedingung. Aufrufen vor dem Schreiben JEDES Sprint-Plans und vor JEDER Review-Runde.
---

# Sprint-Review — Regelwerk

Ersetzt das bis 2026-09-05 nur mündlich überlieferte „S5-S11-Verfahren", das in
`CLAUDE.md` und `SPRINT_LOG.md` achtmal referenziert und nirgends definiert war.

**Anwenden bei:** neuem Sprint-Plan, neuem Architektur-Spike, jeder Cold-Review-Runde,
jedem Vor-Merge-Review.

---

## 1. Plan-Form — Register und Matrix, nicht Prosa

Ein Sprint-Plan besteht aus **zwei Tabellen plus kurzem Fließtext**, nicht aus einer
Entscheidungs-Erzählung. Prosa-Ketten sind die Stelle, an der neue Fehler entstehen
(Beleg: Abschnitt 6).

### 1a. Entscheidungsregister

Eine Zeile pro Entscheidung. **In place überschrieben**, nicht ergänzt — das Register
zeigt den *aktuellen Stand*, nicht seine Entstehung.

| ID | Entscheidung | Beleg | Live geprüft | Betroffene Dateien | 🔒 |
|---|---|---|---|---|---|
| `S17-D1` | … in einem Satz … | Woher die Tatsache stammt | ja / **nein** / n. z. | `modules/x.py`, `config.py` | ja/nein |

Regeln:

- **ID-Präfix ist Pflicht:** sprint-lokale Entscheidungen heißen `S<N>-D<n>`. Bares `D<n>`
  ist `ROADMAP.md` §3 vorbehalten. (Ohne Präfix kollidieren die Namensräume — siehe
  `ROADMAP.md`s D19, die Kollision ist in Commit `f052fe3` bereits eingetreten.)
- **Spalte „Live geprüft" darf nicht leer bleiben.** `nein` ist eine zulässige, aber
  sichtbare Antwort. Eine Entscheidung, die auf einer ungeprüften Tatsachenbehauptung
  ruht, ist damit im Register erkennbar statt im Fließtext versteckt.
- **Eine Zeile, ein Satz.** Braucht eine Entscheidung mehr, gehört die Begründung in
  einen kurzen Absatz *unter* die Tabelle — nicht in die Zelle.
- **Widerlegte Entscheidungen werden überschrieben, nicht durchgestrichen.** Die
  Historie („Runde 3 kippte D11") gehört in `SPRINT_LOG.md`.

### 1b. Abdeckungsmatrix

Zeilen = betroffene Module/Dateien. Spalten = Belange, die der Sprint anfasst.
**Eine leere Zelle ist ein Blocker** — sichtbar, ohne dass ihn jemand erst denken muss.

|  | schreibt `company_id`? | neues `ctx`-Feld | Registrierungskette | Test | GUI |
|---|---|---|---|---|---|
| `modules/sale.py` | ja → wer setzt es? | — | n. z. | P4 | — |
| `modules/crm.py` | | | | | |

Spalten sind sprint-spezifisch. Verbindlich: **jedes berührte Modul bekommt eine Zeile —
auch die, bei denen „nichts zu tun" die Antwort ist.** Genau diese Zeilen fehlten in S16
und kosteten vier Runden (Abschnitt 6). Spalten trennen immer **lesend** von
**schreibend**.

---

## 2. Briefing für den Review-Agenten

Verfahren wie seit S5 praktiziert, hier erstmals festgeschrieben:

- **Fremder Agent, kalt.** Bekommt Plan-Text + Live-Repo. **Keine Konversationshistorie.**
  Das ist der tragende Teil: der Reviewer darf die Annahmen des Planers nicht erben.
- **Auftrag wörtlich mitgeben:**
  1. Jede Zeile mit `Live geprüft = nein` gegen das echte Repo oder die Live-Instanz
     nachprüfen. Falschbehauptungen sind harte Blocker.
  2. Jede leere Matrix-Zelle als Blocker melden oder mit Begründung als „n. z." schließen.
  3. Prüfen, ob die 🔒-Spalte vollständig ist (Abschnitt 3).
  4. Befunde als **Blocker** / **Should-Fix** trennen, jeweils mit Datei:Zeile.
- **Nicht mitgeben:** die Review-Historie früherer Runden. Sonst prüft Runde N die
  Erzählung von Runde N−1 statt den Plan.

---

## 3. Repo-Prüfliste (jede Runde)

- **🔒-Bereiche** aus `CLAUDE.md` „Do Not Touch": Pipeline-Reihenfolge in
  `orchestrator.py`, JSON2-Request-Format in `odoo_client.py`, Config-Schema
  (`config.py`-Dataclasses, inkl. `RunContext`), Cache-Namenskonvention.
  Berührung = explizite Architekten-Freigabe **vor** der Umsetzung.
- **Registrierungskette** für jedes neue orchestrierte Modul: die 7 Schritte in
  `ROADMAP.md`s „Referenz — Registrierungskette". Dorthin verlinken, nie neu herleiten.
  (Fehlerklasse B1: R19s Plan nannte 2 Dateien, das Feature lief nie.)
- **Testmuster P1–P8** aus `CLAUDE.md`: Empty-Pool-Guard, LLM-None-Guard,
  Feature-Flag-Skip, Read-Back-Validierung, Skip-on-Missing-Prerequisites,
  Many2one-Tupel, Verteilungstests, Batch-Enforcement. Jede neue/geänderte
  Verhaltensweise braucht das passende Muster.
- **Feldnamen nie annehmen.** Gegen `mcp__odoo-fields__*` oder die Live-Instanz prüfen.
  Bekannte Fallstricke in `odoo-daten-generator/ODOO_GOTCHAS.md`.
- **Rate-Limit im Blick:** Live-Instanz limitiert bei ~1 req/s. Ein Plan, der pro Record
  einen Aufruf vorsieht statt zu batchen, ist ein Blocker, kein Should-Fix.

---

## 4. Abbruchbedingung

Zwei Regeln, sonst nichts:

**Abbruch — Formwechsel:** findet eine Runde harte Blocker in genau dem, was die
Vorrunde gerade gefixt hat, ist nicht der Plan falsch, sondern seine Form. Keine weitere
Runde derselben Art. Stattdessen Plan in Register + Matrix (Abschnitt 1) überführen und
*diese* reviewen lassen. Spätestens ab Runde 3 gilt das auch ohne dieses Signal.

**Ausstieg — freigegeben:** zwei aufeinanderfolgende Runden ohne harten Blocker.
Umsetzung beginnt.

Wächst der Plan zwischen zwei Runden, statt zu schrumpfen, ist das ein Frühwarnzeichen
für den Formwechsel — kein eigener Terminator.

---

## 5. Nach dem Review — wohin was gehört

| Inhalt | Ziel |
|---|---|
| Aktuelle Entscheidungen, offene Arbeit | `ROADMAP.md` (Register, in place überschrieben) |
| Fertige WP-Sequenzen nach dem Sprint-Merge | `ROADMAP_ARCHIVE.md` |
| Runden-Verlauf, was welche Runde fand, Peer-Review-Ergebnis | `SPRINT_LOG.md` (append-only) |
| Live gefundene Odoo-Feld-/Verhaltensfakten | `ODOO_GOTCHAS.md` |

`ROADMAP.md` ist ein **Entscheidungsregister** (aktueller Stand, überschreibend),
`SPRINT_LOG.md` ein **Review-Log** (Narrativ, anwachsend). Diese beiden nicht mischen —
ihre Vermischung ist der Grund, warum der S16-Abschnitt auf 944 Zeilen wuchs.

Jedes Arbeitspaket endet mit grüner `test_suite.py` gegen die Live-Instanz
(`CLAUDE.md`-Pflicht).

---

## 6. Herkunft — warum diese Regeln so lauten

Am 2026-09-05 aus Git-Log und Repo-Dateien nachgezählt. **S16: 15 Planungs-Commits vor
der ersten Implementierungszeile** (`a9ac618` … `ff0d9e7`, erste Umsetzung `2c9f89d`),
**9 Cold-Review-Runden** in zwei Plan-Generationen (3 auf den verworfenen Minimal-Scope,
6 auf S16-NEU). Jede Regel oben hat dort ihren Auslöser:

| Befund | Regel |
|---|---|
| Runde 1: zwei behauptete statt geprüfte Tatsachen (nie existierendes Feld, falsch gezählte Helper) | Spalte „Live geprüft" (1a) |
| Runde 3: Blocker in genau dem, was Runde 2 gerade fixte | Abbruchbedingung (4) |
| Runde 4: keine der 13 Entscheidungen hatte zugeordnet, wer in 7 Modulen `company_id` setzt — die scheinbar zuständige betraf nur die 2 Helfer, die *lesen* | Abdeckungsmatrix, lesend/schreibend (1b) |
| Runde 5: widerlegte Runde 4 live (`res.partner`/`product.product` erben `company_id` nicht aus dem Kontext) | „Live geprüft" als eigene Spalte, nicht im Fließtext |

Der S16-Spike notiert selbst, *„lange Prosa-Ketten sind, wo die neuen Fehler entstehen"* —
und wuchs danach auf 944 Zeilen. Deshalb Tabellen statt Erzählung, überschreiben statt
anhängen.

**Dieser Skill unterliegt seiner eigenen Regel:** wächst er über ~150 Zeilen, ist etwas
darin Narrativ geworden und gehört nach `SPRINT_LOG.md`.
