/* Demodaten-Konsole — frontend (S9 WP7).
 *
 * Split out of the single-file mockup: the CSP is `default-src 'self'` with no
 * inline <script>, so this file cannot be an inline block and cannot pull code
 * from a CDN.
 *
 * XSS rule enforced throughout: innerHTML is used ONLY for string literals that
 * live in this file (icon paths, static markup). Every value that comes from the
 * server, the LLM, or an Odoo error message goes through textContent. Session
 * auth is a cookie, so an injected script would be session theft.
 */
(function () {
  "use strict";

  var state = {
    csrf: null,
    connect: null,
    defaults: null,
    consent: null,          // null | "granted" | "denied"
    runId: null,
    source: null,
    moduleRows: {},
    // S10/R10 (F3): a LATCH, not a live mirror of state.connect.ok — set once
    // on the first successful /api/connect and never cleared except by
    // logout. A live check would lock the user out of their own running run
    // (and "Diesen Lauf löschen") the moment they navigate back to
    // Verbindung and a re-connect attempt happens to fail.
    everConnected: false,
  };

  // ---------------------------------------------------------------- helpers
  function $(id) { return document.getElementById(id); }

  function clear(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  // Visibility is a class, never an inline style: the CSP is `style-src 'self'`
  // with no 'unsafe-inline', which makes a parsed style="display:none" attribute
  // in index.html a no-op — panels that should start hidden would render. Class
  // toggling is unaffected and works in both directions.
  function setHidden(node, hidden) {
    if (typeof node === "string") node = $(node);
    if (node) node.classList.toggle("is-hidden", !!hidden);
  }

  function setText(id, value) {
    var node = $(id);
    if (node) node.textContent = value === null || value === undefined ? "" : String(value);
  }

  function intVal(id, fallback) {
    var node = $(id);
    if (!node) return fallback;
    var parsed = parseInt(node.value, 10);
    return isNaN(parsed) ? fallback : parsed;
  }

  function checked(id) {
    var node = $(id);
    return !!(node && node.checked);
  }

  // ------------------------------------------------------------------- API
  function api(path, options) {
    options = options || {};
    var headers = { "X-Requested-With": "odoo-generator" };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    if (state.csrf) headers["X-CSRF-Token"] = state.csrf;
    return fetch(path, {
      method: options.method || "GET",
      credentials: "same-origin",
      headers: headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) {
          var message = (data && data.detail) ? data.detail : ("HTTP " + response.status);
          var err = new Error(message);
          err.status = response.status;
          throw err;
        }
        return data;
      });
    });
  }

  // ------------------------------------------------------------------- nav
  var views = document.querySelectorAll(".view");
  var navItems = document.querySelectorAll(".nav-item[data-view]");

  // S10/R10 (F3): only these two are gated. Verbindung is always reachable
  // (it's where you fix a broken connection from), and the rail-foot "?"
  // tutorial button (WP6) is not a nav-item at all, so a blanket rail lock
  // was never the right shape for this.
  var GATED_VIEWS = { config: true, run: true };

  function showView(name) {
    if (GATED_VIEWS[name] && !state.everConnected) return;
    Array.prototype.forEach.call(views, function (v) {
      v.classList.toggle("active", v.id === "view-" + name);
    });
    Array.prototype.forEach.call(navItems, function (n) {
      n.classList.toggle("active", n.dataset.view === name);
    });
  }
  Array.prototype.forEach.call(navItems, function (n) {
    n.addEventListener("click", function () { showView(n.dataset.view); });
  });
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-goto]");
    if (b) showView(b.dataset.goto);
  });

  // Visual lock to match: .nav-item[disabled] already has a greyed-out style
  // in app.css. Called once at init (starts locked) and again the moment
  // state.everConnected latches true.
  function updateNavLock() {
    Array.prototype.forEach.call(navItems, function (n) {
      if (GATED_VIEWS[n.dataset.view]) n.disabled = !state.everConnected;
    });
  }
  updateNavLock();

  // -------------------------------------------------------------- steppers
  function stepper(id, label, hint, val, min, max) {
    var row = el("div", "stepper-row");
    var lbl = el("div", "lbl", label);
    if (hint) lbl.appendChild(el("span", "hint", hint));
    row.appendChild(lbl);

    var wrap = el("div", "stepper");
    wrap.dataset.min = String(min);
    wrap.dataset.max = String(max);
    var dec = el("button", "dec", "−");
    dec.type = "button";
    var input = document.createElement("input");
    input.type = "text";
    input.inputMode = "numeric";
    input.id = id;
    input.value = String(val);
    var inc = el("button", "inc", "+");
    inc.type = "button";
    wrap.appendChild(dec);
    wrap.appendChild(input);
    wrap.appendChild(inc);
    row.appendChild(wrap);
    return row;
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".stepper button");
    if (!btn) return;
    var wrap = btn.closest(".stepper");
    var input = wrap.querySelector("input");
    var min = +wrap.dataset.min, max = +wrap.dataset.max;
    var v = parseInt(input.value, 10) || 0;
    v += btn.classList.contains("inc") ? 1 : -1;
    input.value = String(Math.max(min, Math.min(max, v)));
    onConfigChanged();
  });

  function slider(id, label, val, min, max, suffix) {
    var row = el("div", "slider-row");
    var top = el("div", "slider-top");
    top.appendChild(el("span", null, label));
    var out = el("span", "val", String(val) + (suffix === undefined ? "%" : suffix));
    out.id = id + "-val";
    top.appendChild(out);
    row.appendChild(top);
    var input = document.createElement("input");
    input.type = "range";
    input.id = id;
    input.min = String(min);
    input.max = String(max);
    input.value = String(val);
    input.addEventListener("input", function () {
      out.textContent = input.value + (suffix === undefined ? "%" : suffix);
      if (id === "crm-past" || id === "crm-today") updateFuture();
      // Debounced (schedulePreflightRefresh), so firing on every drag tick
      // costs nothing — only the value after the user stops dragging for
      // 400ms actually reaches the network.
      onConfigChanged();
    });
    row.appendChild(input);
    return row;
  }

  function checkLine(id, label, sub, isChecked) {
    var line = el("label", "check-line");
    var box = document.createElement("input");
    box.type = "checkbox";
    box.id = id;
    box.checked = !!isChecked;
    box.addEventListener("change", onConfigChanged);
    line.appendChild(box);
    line.appendChild(document.createTextNode(" " + label + " "));
    if (sub) line.appendChild(el("span", "sub", sub));
    return line;
  }

  function grid(children) {
    var g = el("div", "stepper-grid");
    children.forEach(function (c) { g.appendChild(c); });
    return g;
  }

  function updateFuture() {
    var past = intVal("crm-past", 0);
    var today = intVal("crm-today", 0);
    var tag = $("crm-future-tag");
    if (tag) tag.textContent = "Zukunft: " + Math.max(0, 100 - past - today) + "%";
  }

  // --------------------------------------------------------- module cards
  // Icon markup is a constant literal in this file — never a server value.
  var ICONS = {
    crm: '<path d="M2 3h12l-4.5 5.5V13l-3-1V8.5L2 3z"/>',
    sale: '<path d="M3 3h5l6 6-5 5-6-6V3z"/><circle cx="5.2" cy="5.2" r="1"/>',
    account: '<rect x="3" y="2" width="10" height="12" rx="1"/><line x1="5" y1="6" x2="11" y2="6"/><line x1="5" y1="9" x2="11" y2="9"/><line x1="5" y1="12" x2="9" y2="12"/>',
    hr: '<circle cx="8" cy="5.5" r="2.5"/><path d="M3 13c0-3 2.5-4.5 5-4.5s5 1.5 5 4.5"/>',
    project: '<line x1="4" y1="2" x2="4" y2="14"/><path d="M4 3h8l-2 2.5 2 2.5H4"/>',
    timesheet: '<circle cx="8" cy="8" r="6"/><line x1="8" y1="8" x2="8" y2="4.5"/><line x1="8" y1="8" x2="10.3" y2="9.5"/>',
    mrp: '<polygon points="8,2.3 12.3,4.9 12.3,10.1 8,12.7 3.7,10.1 3.7,4.9"/><circle cx="8" cy="7.5" r="1.8"/>',
    recruit: '<rect x="2" y="6" width="12" height="7" rx="1"/><rect x="6" y="3.3" width="4" height="2.7" rx="0.5"/><line x1="2" y1="9.5" x2="14" y2="9.5"/>',
    purchase: '<path d="M2.5 3h2l1.6 7.2h6.3"/><circle cx="6.8" cy="12.6" r="1"/><circle cx="11.6" cy="12.6" r="1"/><path d="M5 5.4h8.5l-1 4H5.9"/>',
    stock: '<rect x="2.4" y="6.6" width="4.6" height="4.6"/><rect x="9" y="6.6" width="4.6" height="4.6"/><rect x="5.7" y="2.2" width="4.6" height="4.4"/>',
    docs: '<path d="M4.5 2h4l3 3v9h-7V2z"/><path d="M8.5 2v3h3"/><line x1="6" y1="8.3" x2="10.5" y2="8.3"/><line x1="6" y1="10.6" x2="10.5" y2="10.6"/>',
  };

  function iconSvg(key) {
    var span = el("span", "m-icon " + key);
    // Constant literal — no interpolation of any dynamic value.
    span.innerHTML = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">' + (ICONS[key] || "") + "</svg>";
    return span;
  }

  function buildCard(def, installed, blocked) {
    // "documents" is not an Odoo module: it writes ir.attachment records, which
    // are core, so it is never gated on the installed set (but it CAN still be
    // in `blocked` — S10/R10 — if this API key can't write ir.attachment).
    var isPseudo = def.key === "documents";
    var notInstalled = !isPseudo && installed.indexOf(def.key) === -1;
    // S10/R10: a module can be installed and still unusable — the server
    // already decided this (run_config.effective_installed_modules, the same
    // function a run itself uses), so this renders that decision rather than
    // re-deriving it from installed/feature_flags on its own.
    var isBlocked = !notInstalled && blocked && blocked.indexOf(def.key) !== -1;
    var disabled = notInstalled || isBlocked;

    var cardEl = el("div", "m-card" + (disabled ? " disabled" : "") + (def.span2 ? " span2" : ""));
    var head = el("div", "m-head");
    head.appendChild(iconSvg(def.icon));
    head.appendChild(el("span", "title", def.title));
    if (def.badge) head.appendChild(el("span", "badge", def.badge));

    var sw = el("label", "switch m-switch");
    var box = document.createElement("input");
    box.type = "checkbox";
    box.id = "mod-" + def.key;
    box.checked = !disabled && def.defaultOn !== false;
    box.disabled = disabled;
    box.addEventListener("change", onConfigChanged);
    sw.appendChild(box);
    sw.appendChild(el("span", "track"));
    head.appendChild(sw);
    cardEl.appendChild(head);

    if (notInstalled) {
      cardEl.appendChild(el("div", "m-note",
        "Nicht installiert in dieser Odoo-Instanz — die Karte bleibt sichtbar, damit erkennbar ist, was fehlt."));
    } else if (isBlocked) {
      cardEl.appendChild(el("div", "m-note",
        "Installiert, aber dieser API-Schlüssel hat keine Schreibrechte dafür — siehe Schritt „Schreibrechte“ in Verbindung."));
    } else if (def.note) {
      cardEl.appendChild(el("div", "m-note", def.note));
    }

    var body = el("div", "m-body");
    def.build(body);
    cardEl.appendChild(body);
    return cardEl;
  }

  var MODULE_DEFS = [
    {
      key: "crm", icon: "crm", title: "CRM",
      build: function (body) {
        body.appendChild(grid([
          stepper("crm-opp", "Anzahl Opportunities", "", 10, 0, 200),
          stepper("crm-leads", "Anzahl Leads", "nur wenn Leads-Feature aktiv", 0, 0, 200),
        ]));
        body.appendChild(checkLine("crm-chatter-toggle", "Chatter-Konversationen erstellen", "", true));
        var sub = el("div", "sub-block");
        sub.appendChild(el("div", "sub-caption", "Konversationsstil"));
        var radios = el("div", "radio-set");
        [["notes_only", "Nur interne Notizen", false],
         ["mixed", "Gemischt (E-Mails + Notizen)", true],
         ["full_email", "Vollständige E-Mail-Konversation", false]].forEach(function (opt) {
          var line = el("label", "radio-line");
          var r = document.createElement("input");
          r.type = "radio";
          r.name = "crm-style";
          r.value = opt[0];
          r.checked = opt[2];
          line.appendChild(r);
          line.appendChild(document.createTextNode(" " + opt[1]));
          radios.appendChild(line);
        });
        sub.appendChild(radios);
        sub.appendChild(grid([stepper("crm-msgcount", "Nachrichten pro Opportunity", "", 4, 1, 8)]));
        body.appendChild(sub);

        body.appendChild(checkLine("crm-act-toggle", "Aktivitäten erstellen", "", true));
        var actSub = el("div", "sub-block");
        actSub.appendChild(slider("crm-past", "Vergangenheit %", 30, 0, 100));
        actSub.appendChild(slider("crm-today", "Heute %", 20, 0, 100));
        var tag = el("span", "future-tag", "Zukunft: 50%");
        tag.id = "crm-future-tag";
        actSub.appendChild(tag);
        body.appendChild(actSub);
      },
      collect: function () {
        var block = { enabled: true, count: intVal("crm-opp", 0), leads: intVal("crm-leads", 0) };
        if (checked("crm-chatter-toggle")) {
          var style = document.querySelector('input[name="crm-style"]:checked');
          block.chatter = {
            enabled: true,
            style: style ? style.value : "mixed",
            messages_per_opp: intVal("crm-msgcount", 4),
          };
        }
        if (checked("crm-act-toggle")) {
          block.activities = {
            enabled: true,
            past_pct: intVal("crm-past", 30),
            today_pct: intVal("crm-today", 20),
          };
        }
        return block;
      },
    },
    {
      key: "sale", icon: "sale", title: "Verkauf",
      build: function (body) {
        body.appendChild(grid([stepper("sale-count", "Anzahl Aufträge", "", 10, 0, 200)]));
        body.appendChild(slider("sale-confirm", "Bestätigt %", 65, 0, 100));
      },
      collect: function () {
        return { enabled: true, count: intVal("sale-count", 0), confirm_pct: intVal("sale-confirm", 65) };
      },
    },
    {
      key: "account", icon: "account", title: "Buchhaltung",
      build: function (body) {
        body.appendChild(grid([
          stepper("acc-count", "Anzahl Rechnungen", "", 10, 0, 200),
          stepper("acc-bills", "Anzahl Eingangsrechnungen", "", 5, 0, 200),
        ]));
        body.appendChild(checkLine("acc-bank", "Banktransaktionen erstellen", "(80% exakt, 20% Abweichung)", true));
      },
      collect: function () {
        return {
          enabled: true,
          count: intVal("acc-count", 0),
          bills: intVal("acc-bills", 0),
          bank_transactions: checked("acc-bank"),
        };
      },
    },
    {
      key: "hr", icon: "hr", title: "Personal",
      build: function (body) {
        body.appendChild(grid([stepper("hr-count", "Anzahl Mitarbeiter", "", 10, 0, 200)]));
        body.appendChild(checkLine("hr-to-toggle", "Urlaubsdaten erstellen", "", true));
        var sub = el("div", "sub-block");
        sub.appendChild(grid([stepper("hr-to-entries", "Urlaubsanträge pro Mitarbeiter", "", 2, 1, 20)]));
        sub.appendChild(slider("hr-avglen", "Ø Urlaubsdauer (Tage)", 5, 1, 30, ""));
        sub.appendChild(slider("hr-pf", "Vergangenheit ← → Zukunft", 30, 0, 100));
        sub.appendChild(grid([stepper("hr-timescale", "Zeitraum (Tage)", "", 180, 30, 730)]));
        sub.appendChild(slider("hr-validate", "Genehmigt %", 100, 0, 100));
        body.appendChild(sub);
      },
      collect: function () {
        var block = { enabled: true, count: intVal("hr-count", 0) };
        if (checked("hr-to-toggle")) {
          block.timeoff = {
            enabled: true,
            entries_per_employee: intVal("hr-to-entries", 2),
            avg_length_days: intVal("hr-avglen", 5),
            past_future_pct: intVal("hr-pf", 30),
            timescale_days: intVal("hr-timescale", 180),
            validate_pct: intVal("hr-validate", 100),
          };
        }
        return block;
      },
    },
    {
      key: "project", icon: "project", title: "Projekte",
      build: function (body) {
        body.appendChild(grid([
          stepper("proj-count", "Anzahl Projekte", "", 5, 0, 50),
          stepper("proj-tasks", "Aufgaben pro Projekt", "", 10, 0, 50),
        ]));
      },
      collect: function () {
        return { enabled: true, count: intVal("proj-count", 0), tasks_per_project: intVal("proj-tasks", 10) };
      },
    },
    {
      key: "hr_timesheet", icon: "timesheet", title: "Zeiterfassung",
      build: function (body) {
        body.appendChild(grid([stepper("ts-count", "Anzahl Zeiteinträge", "", 30, 0, 500)]));
      },
      collect: function () { return { enabled: true, count: intVal("ts-count", 0) }; },
    },
    {
      key: "mrp", icon: "mrp", title: "Fertigung",
      build: function (body) {
        body.appendChild(grid([
          stepper("mrp-products", "Anzahl Fertigungsprodukte", "", 3, 0, 50),
          stepper("mrp-comp", "Komponenten pro Stückliste", "", 4, 1, 20),
          stepper("mrp-subbom", "Komponenten mit Sub-Stückliste", "", 2, 0, 20),
          stepper("mrp-work", "Arbeitszentren", "nur wenn Routing-Feature aktiv", 3, 0, 10),
          stepper("mrp-orders", "Fertigungsaufträge", "", 5, 0, 20),
        ]));
        body.appendChild(checkLine("mrp-quality", "Qualitätsprüfpunkte", "nur wenn Quality-Feature aktiv", false));
      },
      collect: function () {
        return {
          enabled: true,
          num_products: intVal("mrp-products", 3),
          components_per_bom: intVal("mrp-comp", 4),
          sub_boms_per_product: intVal("mrp-subbom", 2),
          num_workcenters: intVal("mrp-work", 3),
          num_manufacturing_orders: intVal("mrp-orders", 5),
          create_quality_points: checked("mrp-quality"),
        };
      },
    },
    {
      key: "hr_recruitment", icon: "recruit", title: "Recruiting",
      build: function (body) {
        body.appendChild(grid([
          stepper("rec-jobs", "Anzahl Stellen", "", 5, 0, 50),
          stepper("rec-cand", "Anzahl Bewerbungen", "", 15, 0, 200),
        ]));
        body.appendChild(checkLine("rec-skills", "Kompetenzen erstellen", "", true));
        var sub = grid([
          stepper("rec-skilltypes", "Anzahl Kompetenzarten", "", 3, 0, 20),
          stepper("rec-skillsper", "Kompetenzen pro Kompetenzart", "", 4, 0, 20),
        ]);
        sub.classList.add("sub-block");
        body.appendChild(sub);
      },
      collect: function () {
        return {
          enabled: true,
          num_jobs: intVal("rec-jobs", 5),
          num_candidates: intVal("rec-cand", 15),
          create_skills: checked("rec-skills"),
          num_skill_types: intVal("rec-skilltypes", 3),
          skills_per_type: intVal("rec-skillsper", 4),
        };
      },
    },
    {
      key: "purchase", icon: "purchase", title: "Einkauf",
      build: function (body) {
        body.appendChild(grid([stepper("pur-count", "Anzahl Bestellungen", "", 8, 0, 200)]));
        body.appendChild(slider("pur-confirm", "Bestätigt %", 70, 0, 100));
        body.appendChild(el("div", "field-hint",
          "Bestätigte Bestellungen erzeugen in Odoo automatisch die zugehörige Eingangsrechnung."));
      },
      collect: function () {
        return { enabled: true, count: intVal("pur-count", 0), confirm_pct: intVal("pur-confirm", 70) };
      },
    },
    {
      key: "stock", icon: "stock", title: "Lager",
      build: function (body) {
        body.appendChild(grid([stepper("stock-qty", "Ø Bestand je Lagerartikel", "", 50, 0, 1000)]));
        body.appendChild(el("div", "field-hint",
          "Setzt Bestandskorrekturen auf lagerfähige Artikel und wendet sie an."));
      },
      collect: function () {
        return { enabled: true, avg_qty: intVal("stock-qty", 50) };
      },
    },
    {
      key: "documents", icon: "docs", title: "Dokumente (PDFs)", badge: "Beta · kein Odoo-Modul", span2: true,
      defaultOn: false,
      note: "Erzeugt PDF-Dateien lokal und hängt sie als Anhang an — unabhängig von installierten Apps, daher immer verfügbar.",
      build: function (body) {
        // Beta warning — only visible while this card's switch is on, since
        // .m-body itself is display:none until then (see app.css). That's
        // the "show a hint once someone enables it" behaviour, no extra JS.
        body.appendChild(el("div", "field-hint beta-hint",
          "Beta: Layout und Rechnungsdaten dieser PDFs werden noch verbessert — vor einer Kundenpräsentation prüfen."));
        body.appendChild(checkLine("doc-bills", "PDF-Rechnungen für Eingangsrechnungen", "", true));
        body.appendChild(checkLine("doc-cvs", "Lebenslauf-PDFs für Bewerber", "", true));
      },
      collect: function () {
        return { enabled: true, bill_pdfs: checked("doc-bills"), cv_pdfs: checked("doc-cvs") };
      },
    },
  ];

  function renderModuleGrid(installed, blocked) {
    var gridEl = $("module-grid");
    clear(gridEl);
    MODULE_DEFS.forEach(function (def) {
      gridEl.appendChild(buildCard(def, installed, blocked || []));
    });
    updateFuture();
    onConfigChanged();
  }

  // ------------------------------------------------------------ config view
  function renderMasterSteppers() {
    var kunden = $("stepper-kunden");
    clear(kunden);
    [stepper("s-unternehmen", "Anzahl Unternehmen", "", 3, 0, 20),
     stepper("s-liefer", "Lieferadressen pro Unternehmen", "", 1, 0, 5),
     stepper("s-rechnung", "Rechnungsadressen pro Unternehmen", "", 1, 0, 5),
     stepper("s-kontakte", "Weitere Kontakte pro Unternehmen", "", 1, 0, 5)]
      .forEach(function (n) { kunden.appendChild(n); });

    var produkte = $("stepper-produkte");
    clear(produkte);
    [stepper("s-dienst", "Dienstleistungen", "", 5, 0, 50),
     stepper("s-verbrauch", "Verbrauchsmaterialien", "", 3, 0, 50),
     stepper("s-lager", "Lagerartikel", "", 3, 0, 50)]
      .forEach(function (n) { produkte.appendChild(n); });
  }

  var modeSeg = $("mode-segmented");
  function currentMode() {
    var active = modeSeg.querySelector("button.active");
    return active ? active.dataset.mode : "master";
  }
  modeSeg.addEventListener("click", function (e) {
    var b = e.target.closest("button");
    if (!b) return;
    Array.prototype.forEach.call(modeSeg.children, function (c) {
      c.classList.toggle("active", c === b);
    });
    setHidden("module-section", b.dataset.mode !== "both");
    onConfigChanged();
  });

  // ------------------------------------------------- existing-data consent
  // Including existing records is the one setting that lets a value read out of
  // the target database reach an LLM prompt: modules/crm.py's chatter prompt
  // carries the customer's name and the salesperson's name. Everything else the
  // pipeline sends is LLM-invented or was created by this run, and existing
  // products are used as IDs only, never as text.
  //
  // Declining is not just a UI state — it is passed to the server, which then
  // sends "Kunde"/"Verkäufer" instead of the real names.
  var CONSENT_TEXT =
    "„Vorhandene Daten einbeziehen“ verwendet Kontakte und Produkte, die bereits in der "
    + "Zieldatenbank stehen. Für die Chatter-Konversationen wird dabei der Name des "
    + "Kunden und der des zuständigen Benutzers an den LLM-Anbieter übermittelt, damit "
    + "die Nachrichten die richtigen Personen ansprechen. Produkte, Beträge und alle "
    + "übrigen Felder werden ausschließlich im Code verarbeitet und nie gesendet.\n\n"
    + "Bei Ablehnung wird die Option abgewählt: der Lauf legt eigene Stammdaten an und "
    + "die Konversationen sprechen neutral von „Kunde“ und „Verkäufer“.";

  function updateConsentUi() {
    var wanted = checked("chk-use-existing");
    setHidden("consent-block", !wanted || state.consent === "granted");
    setText("consent-detail", CONSENT_TEXT);
    var label = $("consent-state");
    if (state.consent === "granted") {
      label.className = "inline-hint consent-yes";
      setText("consent-state", "Zugestimmt — kann jederzeit wieder abgewählt werden.");
    } else if (state.consent === "denied") {
      label.className = "inline-hint consent-no";
      setText("consent-state", "Abgelehnt.");
    } else {
      label.className = "inline-hint";
      setText("consent-state", "");
    }
  }

  $("chk-use-existing").addEventListener("change", function () {
    // Any change re-opens the question; a stale yes must not survive a toggle.
    state.consent = null;
    updateConsentUi();
    onConfigChanged();
  });

  $("btn-consent-yes").addEventListener("click", function () {
    state.consent = "granted";
    updateConsentUi();
  });

  $("btn-consent-no").addEventListener("click", function () {
    state.consent = "denied";
    $("chk-use-existing").checked = false;
    // Declining also releases "keine neuen Stammdaten", which forces the option
    // back on — otherwise the two settings would fight each other.
    $("chk-skip-master").checked = false;
    setHidden("master-data-block", false);
    updateConsentUi();
    onConfigChanged();
  });

  $("chk-skip-master").addEventListener("change", function () {
    var skip = checked("chk-skip-master");
    setHidden("master-data-block", skip);
    if (skip) {
      // Skipping master data is only meaningful with existing records, so it
      // implies the option — and therefore the same question.
      if (!checked("chk-use-existing")) state.consent = null;
      $("chk-use-existing").checked = true;
    }
    updateConsentUi();
    onConfigChanged();
  });

  $("f-industry").addEventListener("input", onConfigChanged);

  function activeModuleKeys() {
    if (currentMode() !== "both") return [];
    return MODULE_DEFS.filter(function (def) {
      return checked("mod-" + def.key);
    }).map(function (def) { return def.key; });
  }

  function updateConfigSummary() {
    var count = activeModuleKeys().length;
    var modeLabel = currentMode() === "both" ? "Stammdaten + Bewegungsdaten" : "Nur Stammdaten";
    setText("config-summary", count + " Module aktiv · Modus: " + modeLabel);
  }

  function buildPayload() {
    var payload = {
      mode: currentMode(),
      industry: ($("f-industry").value || "").trim(),
      use_existing: checked("chk-use-existing"),
      existing_data_consent: state.consent,
      skip_master_data: checked("chk-skip-master"),
      master_data: {
        num_companies: intVal("s-unternehmen", 3),
        num_delivery_contacts: intVal("s-liefer", 1),
        num_invoice_contacts: intVal("s-rechnung", 1),
        num_other_contacts: intVal("s-kontakte", 1),
        num_services: intVal("s-dienst", 5),
        num_consumables: intVal("s-verbrauch", 3),
        num_storables: intVal("s-lager", 3),
      },
      modules: {},
    };
    if (currentMode() === "both") {
      MODULE_DEFS.forEach(function (def) {
        if (checked("mod-" + def.key)) payload.modules[def.key] = def.collect();
      });
    }
    return payload;
  }

  // ------------------------------------------------------------------ auth
  $("btn-login").addEventListener("click", function () {
    var button = this;
    button.disabled = true;
    setText("login-hint", "Prüfe Zugangscode…");
    api("/api/auth", { method: "POST", body: { access_code: $("f-access").value } })
      .then(function (data) {
        state.csrf = data.csrf_token;
        $("f-access").value = "";
        setText("login-hint", "Angemeldet.");
        setHidden("panel-login", true);
        setHidden("panel-connect", false);
        loadDefaults();
        // S10/R10 (F1): after login, not on page load — the fields the
        // tutorial's steps 3+ reference aren't visible before this point.
        if (!tutorialSeen()) showTutorial();
      })
      .catch(function (err) {
        setText("login-hint", err.message);
      })
      .finally(function () { button.disabled = false; });
  });

  // ------------------------------------------------ operator defaults (BETA)
  // A blank field is filled server-side from config.ini. The API reports only
  // whether a key default exists — never the key — so this pre-fills the two
  // non-secret fields and labels the rest.
  function applyDefaults(d) {
    state.defaults = d;
    if (!d || !d.enabled) return;
    var note = $("defaults-note");
    if (d.url && !$("f-url").value) {
      $("f-url").value = d.url.replace(/\/+$/, "");
      validateUrlField();
    }
    if (d.llm_model) $("f-llmmodel").value = d.llm_model;
    if (d.has_odoo_key) $("f-key").placeholder = "leer lassen → Schlüssel vom Server";
    if (d.has_llm_key) $("f-llmkey").placeholder = "leer lassen → Schlüssel vom Server";
    if (d.has_odoo_key || d.has_llm_key) {
      setText("defaults-note",
        "Beta: Leere Felder werden mit den auf dem Server hinterlegten Werten gefüllt. "
        + "Eigene Werte eintragen überschreibt sie für diese Sitzung.");
      setHidden(note, false);
    }
  }

  function loadDefaults() {
    return api("/api/defaults").then(applyDefaults).catch(function () { /* optional */ });
  }

  // --------------------------------------------------- Guard A (client half)
  // UX only. The server enforces this independently and is the only authority;
  // this just fails fast and names the reason instead of discovering the wrong
  // target mid-run.
  var DEMO_URL_RE = /^https:\/\/demo-[a-z0-9-]+\.odoo\.com\/?$/i;
  var urlField = $("f-url");
  function validateUrlField() {
    var raw = (urlField.value || "").trim();
    // Blank is allowed only because the server may hold a default; it validates
    // the resolved URL with the same rule either way.
    var blankAllowed = raw === "" && state.defaults && state.defaults.url;
    var ok = blankAllowed || DEMO_URL_RE.test(raw);
    urlField.classList.toggle("invalid", !ok);
    setHidden("f-url-error", ok);
    $("btn-connect").disabled = !ok;
    return ok;
  }
  urlField.addEventListener("input", function () {
    validateUrlField();
    updateTutorialLink();
  });
  validateUrlField();

  // ------------------------------------------------------------- tutorial
  // S10/R10 (F1): first-time overlay. Not gated by the nav-lock — it must
  // stay reachable (via the rail-foot "?") even before any connection.
  var TUTORIAL_SEEN_KEY = "odoo-gen-tutorial-seen";

  function tutorialSeen() {
    try {
      return window.localStorage.getItem(TUTORIAL_SEEN_KEY) === "1";
    } catch (e) {
      // Private browsing / storage disabled — treat as "not seen" rather
      // than throwing, since this is a convenience, not a requirement.
      return false;
    }
  }

  function markTutorialSeen() {
    try { window.localStorage.setItem(TUTORIAL_SEEN_KEY, "1"); } catch (e) { /* see above */ }
  }

  function updateTutorialLink() {
    var container = $("tutorial-instance-link");
    var raw = (urlField.value || "").trim().replace(/\/+$/, "");
    if (!DEMO_URL_RE.test(raw)) {
      setHidden(container, true);
      return;
    }
    clear(container);
    var link = document.createElement("a");
    // Values a user typed, never a server/LLM value — still routed through a
    // real anchor rather than string-built HTML, per this file's XSS rule.
    link.href = raw;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "Instanz öffnen ↗";
    container.appendChild(link);
    setHidden(container, false);
  }

  function showTutorial() {
    updateTutorialLink();
    setHidden("tutorial", false);
  }

  function hideTutorial() {
    setHidden("tutorial", true);
  }

  // "Verstanden" is a permanent acknowledgment; "Später" and the × close
  // only dismiss for this visit, so the overlay offers itself again next time.
  $("tutorial-ok").addEventListener("click", function () {
    markTutorialSeen();
    hideTutorial();
  });
  $("tutorial-later").addEventListener("click", hideTutorial);
  $("tutorial-close").addEventListener("click", hideTutorial);
  $("btn-tutorial-reopen").addEventListener("click", showTutorial);

  // --------------------------------------------------------------- connect
  function renderChecklist(steps) {
    var list = $("checklist");
    clear(list);
    steps.forEach(function (step) {
      var row = el("div", "check-row " + (step.ok ? "ok" : "fail"));
      row.appendChild(el("span", "check-icon", step.ok ? "✓" : "✕"));
      row.appendChild(el("span", "check-label", step.label));
      // step.detail can carry an Odoo error message — textContent, always.
      row.appendChild(el("span", "check-detail", step.detail || ""));
      list.appendChild(row);
    });
  }

  $("btn-connect").addEventListener("click", function () {
    if (!validateUrlField()) return;
    var button = this;
    button.disabled = true;
    setText("connect-hint", "Verbindung wird geprüft…");
    setHidden("panel-checklist", false);
    // S10/R10 (F3): re-hidden at the START of every attempt, not just left
    // over from a previous one — otherwise a bindable "Weiter" button can
    // sit above an empty, not-yet-answered checklist while this fetch is
    // still in flight.
    setHidden("btn-to-config", true);
    clear($("checklist"));

    api("/api/connect", {
      method: "POST",
      body: {
        url: urlField.value.trim(),
        odoo_key: $("f-key").value,
        llm_key: $("f-llmkey").value,
        llm_model: $("f-llmmodel").value.trim(),
      },
    }).then(function (data) {
      state.connect = data;
      renderChecklist(data.steps || []);
      // data.ok = odoo_ok && llm_ok — the two FATAL steps. The other six can
      // be red (e.g. a blocked module, a stale existing-data read) without
      // blocking progress: Phase A's whole point is that a module without
      // write access gets disabled and the run continues, not that the
      // console goes dark. Gating on "every step green" would turn that
      // graceful degradation into a hard stop.
      setText("connect-hint", data.ok
        ? "Verbunden."
        : "Verbindung unvollständig — Odoo und LLM müssen erreichbar sein.");
      // Keys are never echoed back and are cleared from the DOM after submit.
      $("f-key").value = "";
      $("f-llmkey").value = "";
      if (data.ok) {
        state.everConnected = true;
        updateNavLock();
      }
      applyConnectResult(data);
      setHidden("btn-to-config", !data.ok);
    }).catch(function (err) {
      setText("connect-hint", err.message);
    }).finally(function () {
      button.disabled = false;
      validateUrlField();
    });
  });

  function applyConnectResult(data) {
    setText("chip-company-text", data.company_name || "unbekannt");
    var version = $("chip-version");
    if (data.odoo_version) {
      setHidden(version, false);
      version.textContent = data.odoo_version;
    } else {
      setHidden(version, true);
    }
    var lang = $("chip-lang");
    if (data.language_code) {
      setHidden(lang, false);
      lang.textContent = data.language_code;
    }
    // S10/R10 (F2): no input field for this any more — the server derives it
    // from the URL, so this chip is the only place it's still visible.
    var db = $("chip-db");
    if (data.database) {
      setHidden(db, false);
      db.textContent = data.database;
    } else {
      setHidden(db, true);
    }
    setText("server-tag", (urlField.value || "").replace(/^https:\/\//, ""));
    setText("foot-odoo-text", "Odoo: " + (data.ok ? "verbunden" : "Fehler"));
    setText("foot-llm-text", "LLM: " + (data.llm_provider || "nicht verbunden"));
    setText("existing-sub",
      "(" + (data.existing_companies || 0) + " Kunden, " + (data.existing_products || 0) + " Produkte gefunden)");
    state.consent = null;
    updateConsentUi();
    renderModuleGrid(data.installed_modules || [], data.blocked_modules || []);
  }

  // -------------------------------------------------------------- preflight
  function consentSatisfied() {
    if (!checked("chk-use-existing") || state.consent === "granted") return true;
    setHidden("consent-block", false);
    $("consent-block").scrollIntoView({ block: "center" });
    var label = $("consent-state");
    label.className = "inline-hint consent-no";
    setText("consent-state",
      "Bitte zuerst zustimmen oder ablehnen — ohne Entscheidung geht es nicht weiter.");
    return false;
  }

  // S10/R10 (F3+F5): there is no more separate Prüfen view to fetch this for
  // on a single click — the summary lives directly in the config view and
  // stays live, recomputed in the background as the config changes.
  // /api/preflight is pure arithmetic over the payload (build_context takes
  // every Odoo-derived value as a parameter; the endpoint itself does no
  // Odoo I/O), which is what makes calling it on every keystroke safe —
  // don't "optimise" this away later.
  var preflightTimer = null;
  function schedulePreflightRefresh() {
    // Guard against the initial render firing this before any connect has
    // happened at all — /api/preflight 409s without a connected session.
    if (!state.everConnected) return;
    if (preflightTimer) clearTimeout(preflightTimer);
    preflightTimer = setTimeout(refreshPreflightSummary, 400);
  }

  function refreshPreflightSummary() {
    api("/api/preflight", { method: "POST", body: buildPayload() })
      .then(function (data) {
        var mods = $("preflight-modules");
        clear(mods);
        (data.modules || []).forEach(function (m) {
          mods.appendChild(el("span", "chip", m.label));
        });

        var records = $("preflight-records");
        clear(records);
        var estimate = data.record_estimate || {};
        Object.keys(estimate).forEach(function (label) {
          records.appendChild(el("span", "k", label));
          records.appendChild(el("span", "v", estimate[label]));
        });
        records.appendChild(el("span", "k total", "Gesamt"));
        records.appendChild(el("span", "v total", data.record_total));
      })
      // A background refresh that fails (e.g. a session that just lapsed)
      // must not interrupt typing with an alert — the summary just goes
      // stale until the next successful refresh.
      .catch(function () { /* ambient refresh, not user-initiated */ });
  }

  function onConfigChanged() {
    updateConfigSummary();
    schedulePreflightRefresh();
  }

  // -------------------------------------------------------------------- run
  function renderProgressList(modules) {
    var list = $("progress-list");
    clear(list);
    state.moduleRows = {};
    modules.forEach(function (m) {
      var row = el("div", "progress-row");
      row.appendChild(el("span", "p-label", m.label));
      var status = el("span", "p-status " + m.status, statusLabel(m.status));
      row.appendChild(status);
      list.appendChild(row);
      state.moduleRows[m.key] = status;
    });
  }

  function statusLabel(status) {
    if (status === "running") return "Läuft…";
    if (status === "done") return "Fertig";
    if (status === "failed") return "Fehler";
    // S10/R10: set only after the fact, once orchestrator.run() has returned
    // and ctx.skipped_modules says a module did nothing despite on_done(ok=true) —
    // e.g. "documents" with no write access on ir.attachment.
    if (status === "skipped") return "Übersprungen (keine Rechte)";
    return "Ausstehend";
  }

  function appendConsole(line) {
    var box = $("console");
    box.appendChild(el("div", "console-line", line));
    while (box.childNodes.length > 800) box.removeChild(box.firstChild);
    box.scrollTop = box.scrollHeight;
  }

  $("btn-start-run").addEventListener("click", function () {
    // S10/R10 (F3+F5): this button now lives on the config view itself (the
    // separate Prüfen view is gone), so a failed consent check has nowhere
    // to navigate to — consentSatisfied() already scrolls the consent block
    // into view right here.
    if (!consentSatisfied()) return;
    var button = this;
    button.disabled = true;
    api("/api/runs", { method: "POST", body: buildPayload() })
      .then(function (data) {
        state.runId = data.run_id;
        clear($("console"));
        renderProgressList(data.modules || []);
        setText("stat-status", "in Warteschlange");
        setHidden("panel-run-errors", true);
        setHidden("btn-cleanup", true);
        setText("cleanup-hint", "");
        showView("run");
        subscribe(data.run_id);
      })
      .catch(function (err) { window.alert(err.message); })
      .finally(function () { button.disabled = false; });
  });

  function subscribe(runId) {
    if (state.source) state.source.close();
    // EventSource cannot set headers; the endpoint is a cookie-authenticated GET,
    // and GET needs no CSRF token. Reconnects replay from Last-Event-ID.
    var source = new EventSource("/api/runs/" + encodeURIComponent(runId) + "/events");
    state.source = source;

    source.addEventListener("log", function (e) {
      var payload = JSON.parse(e.data);
      appendConsole(payload.message);
    });
    source.addEventListener("module", function (e) {
      var payload = JSON.parse(e.data);
      var node = state.moduleRows[payload.key];
      if (node) {
        node.className = "p-status " + payload.status;
        node.textContent = statusLabel(payload.status);
      }
    });
    source.addEventListener("status", function (e) {
      applyRunStatus(JSON.parse(e.data));
    });
    source.addEventListener("end", function () {
      source.close();
      state.source = null;
      api("/api/runs/" + encodeURIComponent(runId)).then(applyRunStatus);
    });
    source.onerror = function () {
      // EventSource retries on its own; only report a stream that is really gone.
      if (source.readyState === EventSource.CLOSED) {
        appendConsole("[Verbindung zum Ereignisstrom verloren]");
      }
    };
  }

  function applyRunStatus(data) {
    setText("stat-status", data.status);
    setText("stat-calls", data.llm_calls);
    setText("stat-tokens", data.llm_tokens);
    setText("stat-errors", (data.api_errors || []).length);
    setText("stat-records", data.journal_records);

    (data.modules || []).forEach(function (m) {
      var node = state.moduleRows[m.key];
      if (node) {
        node.className = "p-status " + m.status;
        node.textContent = statusLabel(m.status);
      }
    });

    var errors = data.api_errors || [];
    if (errors.length) {
      var box = $("run-errors");
      clear(box);
      errors.forEach(function (err, idx) {
        // Server-side bodies are redacted before they reach here; rendering as
        // text is the second half of that guarantee.
        box.appendChild(el("div", "err-line",
          "[" + (idx + 1) + "] " + (err.status_code || "?") + " " +
          (err.error_message || "") + " " + (err.error_body || "")));
      });
      setHidden("panel-run-errors", false);
    }

    if (data.status === "done" || data.status === "failed") {
      setHidden("btn-cleanup", !data.journal_records);
    }
  }

  $("btn-cleanup").addEventListener("click", function () {
    if (!state.runId) return;
    if (!window.confirm("Alle " + $("stat-records").textContent +
                        " von diesem Lauf erstellten Datensätze in Odoo löschen?")) return;
    var button = this;
    button.disabled = true;
    setText("cleanup-hint", "Lösche…");
    api("/api/runs/" + encodeURIComponent(state.runId) + "/cleanup", { method: "POST", body: {} })
      .then(function (data) {
        setText("cleanup-hint",
          data.deleted + " von " + data.total + " Datensätzen gelöscht" +
          (data.failed && data.failed.length ? ", " + data.failed.length + " Modell(e) fehlgeschlagen" : "."));
      })
      .catch(function (err) { setText("cleanup-hint", err.message); })
      .finally(function () { button.disabled = false; });
  });

  // ------------------------------------------------------------------ init
  renderMasterSteppers();
  renderModuleGrid([]);
  updateConsentUi();
  api("/api/session").then(function (data) {
    state.csrf = data.csrf_token;
    setHidden("panel-login", true);
    setHidden("panel-connect", false);
    loadDefaults();
    if (!tutorialSeen()) showTutorial();
  }).catch(function () { /* not logged in yet — the login panel stays */ });
})();
