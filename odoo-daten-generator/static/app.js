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
    runId: null,
    source: null,
    moduleRows: {},
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

  function showView(name) {
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
    updateConfigSummary();
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

  function buildCard(def, installed) {
    // "documents" is not an Odoo module: it writes ir.attachment records, which
    // are core, so it is never gated on the installed set.
    var isPseudo = def.key === "documents";
    var disabled = !isPseudo && installed.indexOf(def.key) === -1;

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
    box.addEventListener("change", updateConfigSummary);
    sw.appendChild(box);
    sw.appendChild(el("span", "track"));
    head.appendChild(sw);
    cardEl.appendChild(head);

    if (disabled) {
      cardEl.appendChild(el("div", "m-note",
        "Nicht installiert in dieser Odoo-Instanz — die Karte bleibt sichtbar, damit erkennbar ist, was fehlt."));
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
        var sub = el("div");
        sub.style.paddingLeft = "22px";
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
        var actSub = el("div");
        actSub.style.paddingLeft = "22px";
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
        var sub = el("div");
        sub.style.paddingLeft = "22px";
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
        sub.style.paddingLeft = "22px";
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
      key: "documents", icon: "docs", title: "Dokumente (PDFs)", badge: "kein Odoo-Modul", span2: true,
      note: "Erzeugt PDF-Dateien lokal und hängt sie als Anhang an — unabhängig von installierten Apps, daher immer verfügbar.",
      build: function (body) {
        body.appendChild(checkLine("doc-bills", "PDF-Rechnungen für Eingangsrechnungen", "", true));
        body.appendChild(checkLine("doc-cvs", "Lebenslauf-PDFs für Bewerber", "", true));
      },
      collect: function () {
        return { enabled: true, bill_pdfs: checked("doc-bills"), cv_pdfs: checked("doc-cvs") };
      },
    },
  ];

  function renderModuleGrid(installed) {
    var gridEl = $("module-grid");
    clear(gridEl);
    MODULE_DEFS.forEach(function (def) {
      gridEl.appendChild(buildCard(def, installed));
    });
    updateFuture();
    updateConfigSummary();
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
    $("module-section").style.display = b.dataset.mode === "both" ? "" : "none";
    updateConfigSummary();
  });

  $("chk-skip-master").addEventListener("change", function () {
    var skip = checked("chk-skip-master");
    $("master-data-block").style.display = skip ? "none" : "";
    if (skip) $("chk-use-existing").checked = true;
    updateConfigSummary();
  });

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
        $("panel-login").style.display = "none";
        $("panel-connect").style.display = "";
      })
      .catch(function (err) {
        setText("login-hint", err.message);
      })
      .finally(function () { button.disabled = false; });
  });

  // --------------------------------------------------- Guard A (client half)
  // UX only. The server enforces this independently and is the only authority;
  // this just fails fast and names the reason instead of discovering the wrong
  // target mid-run.
  var DEMO_URL_RE = /^https:\/\/demo-[a-z0-9-]+\.odoo\.com\/?$/i;
  var urlField = $("f-url");
  function validateUrlField() {
    var ok = DEMO_URL_RE.test((urlField.value || "").trim());
    urlField.classList.toggle("invalid", !ok);
    $("f-url-error").style.display = ok ? "none" : "";
    $("btn-connect").disabled = !ok;
    return ok;
  }
  urlField.addEventListener("input", validateUrlField);
  validateUrlField();

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
    $("panel-checklist").style.display = "";
    clear($("checklist"));

    api("/api/connect", {
      method: "POST",
      body: {
        url: urlField.value.trim(),
        db: $("f-db").value.trim(),
        odoo_key: $("f-key").value,
        llm_key: $("f-llmkey").value,
        llm_model: $("f-llmmodel").value.trim(),
      },
    }).then(function (data) {
      state.connect = data;
      renderChecklist(data.steps || []);
      setText("connect-hint", data.ok ? "Verbunden." : "Verbindung unvollständig.");
      // Keys are never echoed back and are cleared from the DOM after submit.
      $("f-key").value = "";
      $("f-llmkey").value = "";
      applyConnectResult(data);
      $("btn-to-config").disabled = !data.ok;
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
      version.style.display = "";
      version.textContent = data.odoo_version;
    } else {
      version.style.display = "none";
    }
    var lang = $("chip-lang");
    if (data.language_code) {
      lang.style.display = "";
      lang.textContent = data.language_code;
    }
    setText("server-tag", (urlField.value || "").replace(/^https:\/\//, ""));
    setText("foot-odoo-text", "Odoo: " + (data.ok ? "verbunden" : "Fehler"));
    setText("foot-llm-text", "LLM: " + (data.llm_provider || "nicht verbunden"));
    setText("existing-sub",
      "(" + (data.existing_companies || 0) + " Kunden, " + (data.existing_products || 0) + " Produkte gefunden)");
    renderModuleGrid(data.installed_modules || []);
  }

  // -------------------------------------------------------------- preflight
  $("btn-to-preflight").addEventListener("click", function () {
    api("/api/preflight", { method: "POST", body: buildPayload() })
      .then(function (data) {
        var target = $("preflight-target");
        clear(target);
        [["Instanz", data.target || "–"],
         ["Datenbank", data.database || "–"],
         ["Modus", data.mode === "both" ? "Stammdaten + Bewegungsdaten" : "Nur Stammdaten"],
         ["Branche", data.industry || "–"],
         ["Neue Stammdaten", data.skip_master_data ? "nein (nur vorhandene)" : "ja"]]
          .forEach(function (pair) {
            target.appendChild(el("span", "k", pair[0]));
            target.appendChild(el("span", "v", pair[1]));
          });

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

        setText("preflight-host", (data.target || "").replace(/^https:\/\//, ""));
        setText("preflight-modcount", (data.modules || []).length + " aktiv");
        setText("preflight-total", data.record_total);
        showView("preflight");
      })
      .catch(function (err) { window.alert(err.message); });
  });

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
    return "Ausstehend";
  }

  function appendConsole(line) {
    var box = $("console");
    box.appendChild(el("div", "console-line", line));
    while (box.childNodes.length > 800) box.removeChild(box.firstChild);
    box.scrollTop = box.scrollHeight;
  }

  $("btn-start-run").addEventListener("click", function () {
    var button = this;
    button.disabled = true;
    api("/api/runs", { method: "POST", body: buildPayload() })
      .then(function (data) {
        state.runId = data.run_id;
        clear($("console"));
        renderProgressList(data.modules || []);
        setText("stat-status", "in Warteschlange");
        $("panel-run-errors").style.display = "none";
        $("btn-cleanup").style.display = "none";
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
      $("panel-run-errors").style.display = "";
    }

    if (data.status === "done" || data.status === "failed") {
      $("btn-cleanup").style.display = data.journal_records ? "" : "none";
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
  api("/api/session").then(function (data) {
    state.csrf = data.csrf_token;
    $("panel-login").style.display = "none";
    $("panel-connect").style.display = "";
  }).catch(function () { /* not logged in yet — the login panel stays */ });
})();
