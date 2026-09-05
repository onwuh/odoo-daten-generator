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
    // S16: per-company config blocks. Each entry is a collectConfigBlock()
    // shape ({target, mode, industry, skip_master_data, master_data,
    // modules}). The Konfiguration screen stays a single set of
    // DOM controls — switching tabs saves the active block's current DOM
    // state here, then writes the newly active block back into the same DOM.
    companies: [],
    activeCompanyIndex: 0,
    realCompanies: [],    // res.company id+name (D8a), for the "existing" picker
    runStartedAt: null,   // seconds (server clock, from RunRecord.started_at)
    runModules: [],       // last known [{key,status},...], read by the timer tick
    timerHandle: null,
    feedbackRunId: null,
    feedbackPromptedRunId: null,
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

  // ------------------------------------------------------ field restore (S16)
  // Inverse of the intVal/checked readers above — writes a saved company
  // block's values back into the shared DOM controls when a tab is
  // activated. No event dispatch: restoreCompanyBlock() calls
  // onConfigChanged() itself, once, after every field is set.
  function setVal(id, value) {
    var node = $(id);
    if (node) node.value = String(value);
  }

  function setChecked(id, value) {
    var node = $(id);
    if (node) node.checked = !!value;
  }

  function setStepperValue(id, value) {
    setVal(id, value);
  }

  // Sliders render their value with a unit suffix ("%", "" for hr-avglen)
  // baked into the paired -val span's text at render time — reused here
  // instead of duplicating the per-slider suffix table.
  function setSliderValue(id, value) {
    var input = $(id);
    if (!input) return;
    input.value = String(value);
    var out = $(id + "-val");
    if (out) {
      var suffix = (out.textContent || "").replace(/^-?\d+/, "");
      out.textContent = String(value) + suffix;
    }
    if (id === "crm-past" || id === "crm-today") updateFuture();
  }

  function setRadioValue(name, value) {
    var nodes = document.querySelectorAll('input[name="' + name + '"]');
    Array.prototype.forEach.call(nodes, function (n) { n.checked = (n.value === value); });
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
    expense: '<rect x="3" y="4" width="10" height="9" rx="1"/><line x1="3" y1="7" x2="13" y2="7"/><circle cx="8" cy="10.3" r="1.3"/>',
    docs: '<path d="M4.5 2h4l3 3v9h-7V2z"/><path d="M8.5 2v3h3"/><line x1="6" y1="8.3" x2="10.5" y2="8.3"/><line x1="6" y1="10.6" x2="10.5" y2="10.6"/>',
    analytic: '<circle cx="8" cy="8" r="6"/><line x1="8" y1="8" x2="8" y2="2"/><line x1="8" y1="8" x2="12.2" y2="10.8"/>',
  };

  function iconSvg(key) {
    var span = el("span", "m-icon " + key);
    // Constant literal — no interpolation of any dynamic value.
    span.innerHTML = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">' + (ICONS[key] || "") + "</svg>";
    return span;
  }

  function buildCard(def, installed, blocked) {
    // "documents"/"analytic" are not real Odoo modules: "documents" writes
    // ir.attachment records (core Odoo), "analytic" (S15/R20) writes
    // account.analytic.* records that ship with the already-probed
    // account/sale/purchase apps, not a separately installable app of their
    // own — neither is ever gated on the installed set (but "documents" CAN
    // still be in `blocked`, S10/R10, if this API key can't write
    // ir.attachment; "analytic" has no such probe of its own).
    var isPseudo = def.key === "documents" || def.key === "analytic";
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

        body.appendChild(checkLine("crm-lost-toggle", "Verlorene Opportunities markieren", "", false));
        var lostSub = el("div", "sub-block");
        lostSub.appendChild(slider("crm-lost-pct", "Anteil verloren %", 20, 0, 100));
        lostSub.appendChild(el("div", "field-hint",
          "Wirkt nur auf Opportunities ohne verknüpften Auftrag — läuft nach dem Verkauf-Schritt."));
        body.appendChild(lostSub);
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
        if (checked("crm-lost-toggle")) {
          block.lost = { enabled: true, pct: intVal("crm-lost-pct", 20) };
        }
        return block;
      },
      restore: function (vals) {
        vals = vals || {};
        setStepperValue("crm-opp", vals.count !== undefined ? vals.count : 10);
        setStepperValue("crm-leads", vals.leads !== undefined ? vals.leads : 0);
        setChecked("crm-chatter-toggle", !!(vals.chatter && vals.chatter.enabled));
        if (vals.chatter) {
          setRadioValue("crm-style", vals.chatter.style || "mixed");
          setStepperValue("crm-msgcount", vals.chatter.messages_per_opp !== undefined ? vals.chatter.messages_per_opp : 4);
        }
        setChecked("crm-act-toggle", !!(vals.activities && vals.activities.enabled));
        if (vals.activities) {
          setSliderValue("crm-past", vals.activities.past_pct !== undefined ? vals.activities.past_pct : 30);
          setSliderValue("crm-today", vals.activities.today_pct !== undefined ? vals.activities.today_pct : 20);
        }
        setChecked("crm-lost-toggle", !!(vals.lost && vals.lost.enabled));
        if (vals.lost) {
          setSliderValue("crm-lost-pct", vals.lost.pct !== undefined ? vals.lost.pct : 20);
        }
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
      restore: function (vals) {
        vals = vals || {};
        setStepperValue("sale-count", vals.count !== undefined ? vals.count : 10);
        setSliderValue("sale-confirm", vals.confirm_pct !== undefined ? vals.confirm_pct : 65);
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
      restore: function (vals) {
        vals = vals || {};
        setStepperValue("acc-count", vals.count !== undefined ? vals.count : 10);
        setStepperValue("acc-bills", vals.bills !== undefined ? vals.bills : 5);
        setChecked("acc-bank", vals.bank_transactions !== undefined ? vals.bank_transactions : true);
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
      restore: function (vals) {
        vals = vals || {};
        setStepperValue("hr-count", vals.count !== undefined ? vals.count : 10);
        setChecked("hr-to-toggle", !!(vals.timeoff && vals.timeoff.enabled));
        if (vals.timeoff) {
          setStepperValue("hr-to-entries", vals.timeoff.entries_per_employee !== undefined ? vals.timeoff.entries_per_employee : 2);
          setSliderValue("hr-avglen", vals.timeoff.avg_length_days !== undefined ? vals.timeoff.avg_length_days : 5);
          setSliderValue("hr-pf", vals.timeoff.past_future_pct !== undefined ? vals.timeoff.past_future_pct : 30);
          setStepperValue("hr-timescale", vals.timeoff.timescale_days !== undefined ? vals.timeoff.timescale_days : 180);
          setSliderValue("hr-validate", vals.timeoff.validate_pct !== undefined ? vals.timeoff.validate_pct : 100);
        }
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
      restore: function (vals) {
        vals = vals || {};
        setStepperValue("proj-count", vals.count !== undefined ? vals.count : 5);
        setStepperValue("proj-tasks", vals.tasks_per_project !== undefined ? vals.tasks_per_project : 10);
      },
    },
    {
      key: "hr_timesheet", icon: "timesheet", title: "Zeiterfassung",
      build: function (body) {
        body.appendChild(grid([stepper("ts-count", "Anzahl Zeiteinträge", "", 30, 0, 500)]));
      },
      collect: function () { return { enabled: true, count: intVal("ts-count", 0) }; },
      restore: function (vals) {
        vals = vals || {};
        setStepperValue("ts-count", vals.count !== undefined ? vals.count : 30);
      },
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
        var qualitySub = grid([slider("mrp-quality-fail-pct", "Ausfallquote Qualitätsprüfungen", 0, 0, 100)]);
        qualitySub.classList.add("sub-block");
        body.appendChild(qualitySub);
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
          quality_fail_pct: intVal("mrp-quality-fail-pct", 0),
        };
      },
      restore: function (vals) {
        vals = vals || {};
        setStepperValue("mrp-products", vals.num_products !== undefined ? vals.num_products : 3);
        setStepperValue("mrp-comp", vals.components_per_bom !== undefined ? vals.components_per_bom : 4);
        setStepperValue("mrp-subbom", vals.sub_boms_per_product !== undefined ? vals.sub_boms_per_product : 2);
        setStepperValue("mrp-work", vals.num_workcenters !== undefined ? vals.num_workcenters : 3);
        setStepperValue("mrp-orders", vals.num_manufacturing_orders !== undefined ? vals.num_manufacturing_orders : 5);
        setChecked("mrp-quality", !!vals.create_quality_points);
        setSliderValue("mrp-quality-fail-pct", vals.quality_fail_pct !== undefined ? vals.quality_fail_pct : 0);
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
      restore: function (vals) {
        vals = vals || {};
        setStepperValue("rec-jobs", vals.num_jobs !== undefined ? vals.num_jobs : 5);
        setStepperValue("rec-cand", vals.num_candidates !== undefined ? vals.num_candidates : 15);
        setChecked("rec-skills", vals.create_skills !== undefined ? vals.create_skills : true);
        setStepperValue("rec-skilltypes", vals.num_skill_types !== undefined ? vals.num_skill_types : 3);
        setStepperValue("rec-skillsper", vals.skills_per_type !== undefined ? vals.skills_per_type : 4);
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
      restore: function (vals) {
        vals = vals || {};
        setStepperValue("pur-count", vals.count !== undefined ? vals.count : 8);
        setSliderValue("pur-confirm", vals.confirm_pct !== undefined ? vals.confirm_pct : 70);
      },
    },
    {
      key: "stock", icon: "stock", title: "Lager",
      build: function (body) {
        body.appendChild(grid([stepper("stock-qty", "Ø Bestand je Lagerartikel", "", 50, 0, 1000)]));
        body.appendChild(el("div", "field-hint",
          "Setzt Bestandskorrekturen auf lagerfähige Artikel und wendet sie an."));
        body.appendChild(grid([stepper("stock-sublocs", "Lagerplätze (Sub-Locations)", "", 0, 0, 10)]));
        body.appendChild(checkLine("stock-wh2", "Zweites Lager anlegen", "", false));
        body.appendChild(el("div", "field-hint",
          "Zweites Lager kann über die Bereinigungs-Funktion nicht vollständig entfernt werden, nur archiviert."));
        body.appendChild(slider("stock-lot-pct", "Chargen-Tracking (Lot)", 0, 0, 100));
        body.appendChild(slider("stock-serial-pct", "Seriennummern-Tracking", 0, 0, 100));
        body.appendChild(grid([stepper("stock-serial-max", "Max. Seriennummern je Produkt", "", 10, 1, 100)]));
        body.appendChild(el("div", "field-hint",
          "Chargen-/Seriennummern-Zuweisung braucht die Stammdaten-Erzeugung dieses Laufs — " +
          "wirkungslos bei \"Vorhandene Daten verwenden\" oder übersprungenen Stammdaten. " +
          "Seriennummern erzeugen deutlich mehr Einzeldatensätze als Chargen. Chargen-/" +
          "Seriennummern-Datensätze können über die Bereinigungs-Funktion nicht entfernt werden " +
          "(weder gelöscht noch archiviert)."));
        body.appendChild(slider("stock-orderpoints-pct", "Nachbestellregeln", 0, 0, 100));
        body.appendChild(grid([
          stepper("stock-orderpoint-min", "Mindestbestand", "", 5, 1, 100000),
          stepper("stock-orderpoint-max", "Maximalbestand", "", 20, 1, 100000),
        ]));
        body.appendChild(el("div", "field-hint",
          "Nachbestellregeln braucht die Stammdaten-/Fertigungs-Erzeugung dieses Laufs — " +
          "wirkungslos bei \"Vorhandene Daten verwenden\" oder übersprungenen Stammdaten."));
      },
      collect: function () {
        return {
          enabled: true,
          avg_qty: intVal("stock-qty", 50),
          sub_locations: intVal("stock-sublocs", 0),
          second_warehouse: checked("stock-wh2"),
          tracking_lot_pct: intVal("stock-lot-pct", 0),
          tracking_serial_pct: intVal("stock-serial-pct", 0),
          tracking_serial_max: intVal("stock-serial-max", 10),
          orderpoints_pct: intVal("stock-orderpoints-pct", 0),
          orderpoint_min_qty: intVal("stock-orderpoint-min", 5),
          orderpoint_max_qty: intVal("stock-orderpoint-max", 20),
        };
      },
      restore: function (vals) {
        vals = vals || {};
        setStepperValue("stock-qty", vals.avg_qty !== undefined ? vals.avg_qty : 50);
        setStepperValue("stock-sublocs", vals.sub_locations !== undefined ? vals.sub_locations : 0);
        setChecked("stock-wh2", !!vals.second_warehouse);
        setSliderValue("stock-lot-pct", vals.tracking_lot_pct !== undefined ? vals.tracking_lot_pct : 0);
        setSliderValue("stock-serial-pct", vals.tracking_serial_pct !== undefined ? vals.tracking_serial_pct : 0);
        setStepperValue("stock-serial-max", vals.tracking_serial_max !== undefined ? vals.tracking_serial_max : 10);
        setSliderValue("stock-orderpoints-pct", vals.orderpoints_pct !== undefined ? vals.orderpoints_pct : 0);
        setStepperValue("stock-orderpoint-min", vals.orderpoint_min_qty !== undefined ? vals.orderpoint_min_qty : 5);
        setStepperValue("stock-orderpoint-max", vals.orderpoint_max_qty !== undefined ? vals.orderpoint_max_qty : 20);
      },
    },
    {
      key: "hr_expense", icon: "expense", title: "Spesen",
      build: function (body) {
        body.appendChild(grid([stepper("exp-count", "Spesen je Mitarbeiter", "", 3, 0, 50)]));
        body.appendChild(slider("exp-approved", "Genehmigt %", 70, 0, 100));
        body.appendChild(el("div", "field-hint",
          "Braucht Mitarbeiter (Personal-Modul) — ohne die wird dieser Schritt übersprungen."));
      },
      collect: function () {
        return { enabled: true, count_per_employee: intVal("exp-count", 3), approved_pct: intVal("exp-approved", 70) };
      },
      restore: function (vals) {
        vals = vals || {};
        setStepperValue("exp-count", vals.count_per_employee !== undefined ? vals.count_per_employee : 3);
        setSliderValue("exp-approved", vals.approved_pct !== undefined ? vals.approved_pct : 70);
      },
    },
    {
      key: "analytic", icon: "analytic", title: "Kostenrechnung", badge: "kein Odoo-Modul",
      defaultOn: false,
      note: "Legt Kostenstellen an und verteilt sie auf einen Anteil der Verkaufs-/Einkaufs-/Spesenzeilen — wirkungslos ohne die jeweils zugehörige Karte.",
      build: function (body) {
        body.appendChild(slider("an-sale", "Verkauf %", 0, 0, 100));
        body.appendChild(slider("an-purchase", "Einkauf %", 0, 0, 100));
        body.appendChild(slider("an-expense", "Spesen %", 0, 0, 100));
      },
      collect: function () {
        return {
          enabled: true,
          sale_pct: intVal("an-sale", 0),
          purchase_pct: intVal("an-purchase", 0),
          expense_pct: intVal("an-expense", 0),
        };
      },
      restore: function (vals) {
        vals = vals || {};
        setSliderValue("an-sale", vals.sale_pct !== undefined ? vals.sale_pct : 0);
        setSliderValue("an-purchase", vals.purchase_pct !== undefined ? vals.purchase_pct : 0);
        setSliderValue("an-expense", vals.expense_pct !== undefined ? vals.expense_pct : 0);
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
      restore: function (vals) {
        vals = vals || {};
        setChecked("doc-bills", vals.bill_pdfs !== undefined ? vals.bill_pdfs : true);
        setChecked("doc-cvs", vals.cv_pdfs !== undefined ? vals.cv_pdfs : true);
      },
    },
  ];

  // S16/S4 (pre-merge cold review): buildCard() pre-checks each module's own
  // sensible default (installed + not blocked + defaultOn !== false) — but
  // every company block starts from defaultCompanyBlock()'s modules:{},
  // which restoreCompanyBlock() then applies by unchecking everything.
  // Snapshotting the grid's own just-rendered defaults right here, once per
  // connect, is what defaultCompanyBlock() hands back instead of a bare {}.
  var defaultModuleSnapshot = {};

  function captureDefaultModuleSnapshot() {
    defaultModuleSnapshot = {};
    MODULE_DEFS.forEach(function (def) {
      var box = $("mod-" + def.key);
      if (box && box.checked) defaultModuleSnapshot[def.key] = def.collect();
    });
  }

  function renderModuleGrid(installed, blocked) {
    var gridEl = $("module-grid");
    clear(gridEl);
    MODULE_DEFS.forEach(function (def) {
      gridEl.appendChild(buildCard(def, installed, blocked || []));
    });
    captureDefaultModuleSnapshot();
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
  // S16/B4 (pre-merge cold review): this used to be driven by a top-level
  // "Vorhandene Daten einbeziehen" checkbox (build_context's DB-wide
  // is_company+customer_rank fetch). D11 superseded that mechanism with
  // target.reuse_master_data (D8b, scoped to one specific existing
  // res.company) — build_context_list deliberately never wires the old
  // existing_company_ids/existing_product_ids kwargs, so the old checkbox
  // had gone silently inert while its own consent trigger stayed wired to
  // it. Consent now triggers off chk-target-reuse, the control that
  // actually reaches the server (run_config.build_context_list's
  // `reuse_requested = any(t.get("reuse_master_data") ...)`).
  //
  // Same privacy shape as before: modules/crm.py's chatter prompt carries
  // the reused company's customer/salesperson name to the LLM provider.
  // Declining is not just a UI state — it is passed to the server, which
  // then sends "Kunde"/"Verkäufer" instead of the real names.
  var CONSENT_TEXT =
    "„Vorhandene Stammdaten dieser Firma wiederverwenden“ verwendet Kontakte und Produkte, "
    + "die für die gewählte bestehende Firma bereits in der Zieldatenbank stehen. Für die "
    + "Chatter-Konversationen wird dabei der Name des Kunden und der des zuständigen "
    + "Benutzers an den LLM-Anbieter übermittelt, damit die Nachrichten die richtigen "
    + "Personen ansprechen. Produkte, Beträge und alle übrigen Felder werden ausschließlich "
    + "im Code verarbeitet und nie gesendet.\n\n"
    + "Bei Ablehnung wird die Option abgewählt: der Lauf legt eigene Stammdaten an und die "
    + "Konversationen sprechen neutral von „Kunde“ und „Verkäufer“.";

  function updateConsentUi() {
    var wanted = checked("chk-target-reuse");
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

  $("chk-target-reuse").addEventListener("change", function () {
    // Any change re-opens the question; a stale yes must not survive a toggle.
    state.consent = null;
    updateConsentUi();
  });

  $("btn-consent-yes").addEventListener("click", function () {
    state.consent = "granted";
    updateConsentUi();
  });

  $("btn-consent-no").addEventListener("click", function () {
    state.consent = "denied";
    $("chk-target-reuse").checked = false;
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
      // Skipping master data only makes sense against an existing company's
      // reused data — it implies the option, and therefore the same question.
      // (If the active tab's target isn't "existing", this combination still
      // reaches the server and gets a clear 400 rather than an empty run.)
      if (!checked("chk-target-reuse")) state.consent = null;
      $("chk-target-reuse").checked = true;
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

  function collectModules() {
    var out = {};
    if (currentMode() === "both") {
      MODULE_DEFS.forEach(function (def) {
        if (checked("mod-" + def.key)) out[def.key] = def.collect();
      });
    }
    return out;
  }

  // Module cards themselves (installed/blocked/disabled) don't change across
  // companies — one Odoo instance, one connect() result — only which modules
  // are turned ON and their field values differ per saved block.
  function restoreModules(modulesObj) {
    modulesObj = modulesObj || {};
    MODULE_DEFS.forEach(function (def) {
      var box = $("mod-" + def.key);
      if (!box) return;
      var present = Object.prototype.hasOwnProperty.call(modulesObj, def.key);
      box.checked = present && !box.disabled;
      def.restore(present ? modulesObj[def.key] : null);
    });
  }

  function updateConfigSummary() {
    var count = activeModuleKeys().length;
    var modeLabel = currentMode() === "both" ? "Stammdaten + Bewegungsdaten" : "Nur Stammdaten";
    setText("config-summary", count + " Module aktiv · Modus: " + modeLabel);
  }

  // ---------------------------------------------------- Firmenauswahl (S16)
  // The Konfiguration screen stays ONE set of DOM controls (no cloning): a
  // tab strip on top saves the active tab's current values into
  // state.companies[i] and writes the newly selected tab's saved values back
  // into the same controls. Full pipeline repeats per company server-side
  // (S16-NEU); this is just which company each screenful of settings belongs to.
  var targetModeSeg = $("target-mode-segmented");

  function targetMode() {
    var active = targetModeSeg.querySelector("button.active");
    return active ? active.dataset.mode : "new";
  }

  function collectTarget() {
    if (targetMode() === "existing") {
      var sel = $("f-target-existing");
      var companyId = sel && sel.value ? parseInt(sel.value, 10) : null;
      return { mode: "existing", company_id: companyId, reuse_master_data: checked("chk-target-reuse") };
    }
    return {
      mode: "new",
      name: ($("f-target-name").value || "").trim(),
      country: $("f-target-country").value || "DE",
    };
  }

  function restoreTarget(target) {
    target = target || { mode: "new", name: "", country: "DE" };
    var mode = target.mode === "existing" ? "existing" : "new";
    Array.prototype.forEach.call(targetModeSeg.children, function (c) {
      c.classList.toggle("active", c.dataset.mode === mode);
    });
    setHidden("target-new-fields", mode !== "new");
    setHidden("target-existing-fields", mode !== "existing");
    setVal("f-target-name", target.name || "");
    setVal("f-target-country", target.country || "DE");
    if (target.company_id !== undefined && target.company_id !== null) {
      setVal("f-target-existing", String(target.company_id));
    }
    setChecked("chk-target-reuse", !!target.reuse_master_data);
  }

  targetModeSeg.addEventListener("click", function (e) {
    var b = e.target.closest("button");
    if (!b) return;
    Array.prototype.forEach.call(targetModeSeg.children, function (c) {
      c.classList.toggle("active", c === b);
    });
    setHidden("target-new-fields", b.dataset.mode !== "new");
    setHidden("target-existing-fields", b.dataset.mode !== "existing");
    onConfigChanged();
  });

  // Target fields don't change the record estimate itself, but they do
  // change payload VALIDITY (a blank "new company" name 400s /api/preflight)
  // — without this, the live summary only catches up on the next unrelated
  // field edit instead of the moment a name is typed.
  $("f-target-name").addEventListener("input", onConfigChanged);
  $("f-target-country").addEventListener("change", onConfigChanged);
  $("f-target-existing").addEventListener("change", onConfigChanged);
  $("chk-target-reuse").addEventListener("change", onConfigChanged);

  function populateExistingCompanySelect() {
    var sel = $("f-target-existing");
    clear(sel);
    state.realCompanies.forEach(function (c) {
      var opt = document.createElement("option");
      opt.value = String(c.id);
      opt.textContent = c.name;
      sel.appendChild(opt);
    });
  }

  function companyNameById(id) {
    if (id === null || id === undefined) return null;
    var found = state.realCompanies.filter(function (c) { return c.id === id; })[0];
    return found ? found.name : null;
  }

  function defaultCompanyBlock() {
    return {
      target: { mode: "new", name: "", country: "DE" },
      mode: "master",
      industry: "IT-Dienstleistung",
      skip_master_data: false,
      master_data: {
        num_companies: 3, num_delivery_contacts: 1, num_invoice_contacts: 1,
        num_other_contacts: 1, num_services: 5, num_consumables: 3, num_storables: 3,
      },
      modules: JSON.parse(JSON.stringify(defaultModuleSnapshot)),
    };
  }

  function collectConfigBlock() {
    return {
      target: collectTarget(),
      mode: currentMode(),
      industry: ($("f-industry").value || "").trim(),
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
      modules: collectModules(),
    };
  }

  function restoreCompanyBlock(block) {
    block = block || defaultCompanyBlock();
    restoreTarget(block.target);
    Array.prototype.forEach.call(modeSeg.children, function (c) {
      c.classList.toggle("active", c.dataset.mode === block.mode);
    });
    setHidden("module-section", block.mode !== "both");
    setVal("f-industry", block.industry || "");
    updateConsentUi();
    setChecked("chk-skip-master", !!block.skip_master_data);
    setHidden("master-data-block", !!block.skip_master_data);
    var md = block.master_data || {};
    setStepperValue("s-unternehmen", md.num_companies !== undefined ? md.num_companies : 3);
    setStepperValue("s-liefer", md.num_delivery_contacts !== undefined ? md.num_delivery_contacts : 1);
    setStepperValue("s-rechnung", md.num_invoice_contacts !== undefined ? md.num_invoice_contacts : 1);
    setStepperValue("s-kontakte", md.num_other_contacts !== undefined ? md.num_other_contacts : 1);
    setStepperValue("s-dienst", md.num_services !== undefined ? md.num_services : 5);
    setStepperValue("s-verbrauch", md.num_consumables !== undefined ? md.num_consumables : 3);
    setStepperValue("s-lager", md.num_storables !== undefined ? md.num_storables : 3);
    restoreModules(block.modules || {});
    updateFuture();
    updateConfigSummary();
  }

  function saveActiveCompanyBlock() {
    if (!state.companies.length) return;
    state.companies[state.activeCompanyIndex] = collectConfigBlock();
  }

  function renderCompanyTabs() {
    var wrap = $("company-tabs");
    clear(wrap);
    state.companies.forEach(function (block, i) {
      var label;
      if (block.target && block.target.mode === "existing") {
        label = companyNameById(block.target.company_id) || ("Firma " + (i + 1));
      } else {
        label = (block.target && block.target.name) || ("Firma " + (i + 1));
      }
      var btn = el("button", "company-tab" + (i === state.activeCompanyIndex ? " active" : ""), label);
      btn.type = "button";
      btn.dataset.index = String(i);
      wrap.appendChild(btn);
    });
    $("btn-remove-company").disabled = state.companies.length <= 1;
  }

  function switchToCompany(index) {
    if (index === state.activeCompanyIndex) return;
    saveActiveCompanyBlock();
    state.activeCompanyIndex = index;
    restoreCompanyBlock(state.companies[index]);
    renderCompanyTabs();
    onConfigChanged();
  }

  $("company-tabs").addEventListener("click", function (e) {
    var b = e.target.closest(".company-tab");
    if (!b) return;
    switchToCompany(parseInt(b.dataset.index, 10));
  });

  $("btn-add-company").addEventListener("click", function () {
    state.companies.push(defaultCompanyBlock());
    switchToCompany(state.companies.length - 1);
  });

  $("btn-remove-company").addEventListener("click", function () {
    if (state.companies.length <= 1) return;
    var removed = state.activeCompanyIndex;
    state.companies.splice(removed, 1);
    state.activeCompanyIndex = Math.min(removed, state.companies.length - 1);
    restoreCompanyBlock(state.companies[state.activeCompanyIndex]);
    renderCompanyTabs();
    onConfigChanged();
  });

  function buildPayload() {
    saveActiveCompanyBlock();
    return {
      existing_data_consent: state.consent,
      companies: state.companies,
    };
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
        setHidden("btn-feedback-open", false);
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

  // -------------------------------------------------------------- feedback
  // Persistent button (rail-foot) AND an automatic popup after every run
  // finishes — the tool is early in rollout and wants heavy feedback volume
  // right now. The opt-out checkbox (auto-popup only) is the permanent brake;
  // statusLabel() is defined further down but usable here since function
  // declarations are hoisted within this file's enclosing IIFE.
  var FEEDBACK_OPTOUT_KEY = "odoo-gen-feedback-optout";

  function feedbackOptedOut() {
    try {
      return window.localStorage.getItem(FEEDBACK_OPTOUT_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  function setFeedbackOptedOut() {
    try { window.localStorage.setItem(FEEDBACK_OPTOUT_KEY, "1"); } catch (e) { /* see tutorialSeen() */ }
  }

  function openFeedback(opts) {
    opts = opts || {};
    var isAuto = opts.trigger === "auto";
    // Manual trigger has no opts.runId — fall back to the most recent run of
    // this session, so "click ✉ because something looked wrong after a run"
    // still attaches context. _feedback_run_context ignores a stale/unknown id.
    state.feedbackRunId = opts.runId || state.runId || null;
    $("feedback-message").value = "";
    var bugRadio = document.querySelector('input[name="feedback-category"][value="bug"]');
    if (bugRadio) bugRadio.checked = true;
    setText("feedback-hint", "");
    clear($("feedback-result"));
    setHidden("feedback-result", true);
    setHidden("feedback-optout-row", !isAuto);
    $("feedback-optout").checked = false;
    if (isAuto) {
      setText("feedback-context-hint", "Lauf beendet (" + statusLabel(opts.status) + ") — kurzes Feedback dazu?");
      setHidden("feedback-context-hint", false);
    } else {
      setHidden("feedback-context-hint", true);
    }
    setHidden("feedback-modal", false);
  }

  function hideFeedback() {
    // Checked BEFORE hiding so ×/Abbrechen/post-submit auto-close all honour it.
    if (!$("feedback-optout-row").classList.contains("is-hidden") && checked("feedback-optout")) {
      setFeedbackOptedOut();
    }
    setHidden("feedback-modal", true);
  }

  $("btn-feedback-open").addEventListener("click", function () { openFeedback({ trigger: "manual" }); });
  $("feedback-close").addEventListener("click", hideFeedback);
  $("feedback-cancel").addEventListener("click", hideFeedback);

  $("feedback-submit").addEventListener("click", function () {
    var message = $("feedback-message").value.trim();
    if (!message) {
      setText("feedback-hint", "Bitte eine Nachricht eingeben.");
      return;
    }
    var categoryInput = document.querySelector('input[name="feedback-category"]:checked');
    var payload = { category: categoryInput ? categoryInput.value : "bug", message: message };
    if (state.feedbackRunId) payload.run_id = state.feedbackRunId;
    var button = this;
    button.disabled = true;
    setText("feedback-hint", "Wird gesendet…");
    api("/api/feedback", { method: "POST", body: payload })
      .then(function (data) {
        setText("feedback-hint", "");
        clear($("feedback-result"));
        var link = document.createElement("a");
        link.href = data.url;
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = "Issue #" + data.number + " ansehen ↗";
        $("feedback-result").appendChild(link);
        setHidden("feedback-result", false);
        setTimeout(hideFeedback, 1500);
      })
      .catch(function (err) { setText("feedback-hint", err.message); })
      .finally(function () { button.disabled = false; });
  });

  function maybeAutoFeedback(runId, runStatus) {
    if (feedbackOptedOut()) return;
    if (state.feedbackPromptedRunId === runId) return;
    if (!$("feedback-modal").classList.contains("is-hidden")) return; // don't clobber a manual draft
    state.feedbackPromptedRunId = runId;
    openFeedback({ trigger: "auto", runId: runId, status: runStatus });
  }

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
    state.consent = null;
    updateConsentUi();
    renderModuleGrid(data.installed_modules || [], data.blocked_modules || []);

    // S16/D8a: reconnecting resets the company list to a single fresh block —
    // consistent with renderModuleGrid/updateConsentUi above also resetting.
    state.realCompanies = data.real_companies || [];
    populateExistingCompanySelect();
    state.companies = [defaultCompanyBlock()];
    state.activeCompanyIndex = 0;
    restoreCompanyBlock(state.companies[0]);
    renderCompanyTabs();
  }

  // -------------------------------------------------------------- preflight
  // S16/B4: checked at submit time, across ALL saved companies, not just the
  // active tab — reuse_master_data can be set on a company you're not
  // currently looking at, and the server's consent gate
  // (run_config.validate_consent's reuse_requested) is a run-wide OR, same
  // as this must be.
  function reuseRequestingCompanyIndex() {
    saveActiveCompanyBlock();
    for (var i = 0; i < state.companies.length; i++) {
      if (state.companies[i].target && state.companies[i].target.reuse_master_data) return i;
    }
    return -1;
  }

  function consentSatisfied() {
    var offender = reuseRequestingCompanyIndex();
    if (offender === -1 || state.consent === "granted") return true;
    // The consent block lives inside the Firmen panel's "existing" target
    // fields — if the offending company isn't the tab on screen, its own
    // target-existing-fields (and therefore consent-block) is display:none
    // via #target-existing-fields.is-hidden, so switch to it first or the
    // unhide below has nothing visible to unhide.
    if (offender !== state.activeCompanyIndex) switchToCompany(offender);
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
    // S16: qualified keys ("0:crm") carry a company_index — group the flat
    // module list under a "Firma N" header per company. Index is server-
    // assigned per target position, not a stored name, so the header always
    // matches jobs.py's own "Firma {i+1}" fallback label.
    var lastGroup;
    modules.forEach(function (m) {
      if (m.company_index !== lastGroup) {
        lastGroup = m.company_index;
        if (lastGroup !== null && lastGroup !== undefined) {
          list.appendChild(el("div", "progress-group-head", "Firma " + (lastGroup + 1)));
        }
      }
      var row = el("div", "progress-row");
      row.appendChild(el("span", "p-label", m.label));
      var status = el("span", "p-status " + m.status, statusLabel(m.status));
      row.appendChild(status);
      list.appendChild(row);
      state.moduleRows[m.key] = status;
    });
  }

  // ---- runtime timer ("Laufzeit" / "Verbleibend (ca.)") -------------------
  // Elapsed is exact (server started_at vs. wall clock). Remaining is a
  // self-correcting estimate from the fraction of modules finished so far
  // (elapsed / done * pending) — no per-module weighting data exists, and a
  // finished-module-count ratio is the same approach the rest of this app
  // uses for "(ca.)" estimates (see run_config.estimate_record_counts).
  // Absent before the first module completes; shown as "wird berechnet…".
  function formatDuration(totalSeconds) {
    var s = Math.max(0, Math.round(totalSeconds));
    var m = Math.floor(s / 60);
    var rem = s % 60;
    return m + ":" + (rem < 10 ? "0" : "") + rem;
  }

  function stopRunTimer() {
    if (state.timerHandle) {
      window.clearInterval(state.timerHandle);
      state.timerHandle = null;
    }
  }

  function tickRunTimer() {
    if (!state.runStartedAt) return;
    var elapsed = (Date.now() / 1000) - state.runStartedAt;
    setText("stat-elapsed", formatDuration(elapsed));

    var total = state.runModules.length;
    var done = state.runModules.filter(function (m) {
      return m.status === "done" || m.status === "failed" || m.status === "skipped";
    }).length;
    if (!total || !done) {
      setText("stat-remaining", "wird berechnet…");
    } else if (done >= total) {
      setText("stat-remaining", "0:00");
    } else {
      var remaining = (elapsed / done) * (total - done);
      setText("stat-remaining", "~" + formatDuration(remaining));
    }
  }

  function startRunTimer(startedAt) {
    stopRunTimer();
    state.runStartedAt = startedAt;
    tickRunTimer();
    state.timerHandle = window.setInterval(tickRunTimer, 1000);
  }

  function resetRunTimer() {
    stopRunTimer();
    state.runStartedAt = null;
    state.runModules = [];
    setText("stat-elapsed", "–");
    setText("stat-remaining", "–");
  }

  function statusLabel(status) {
    if (status === "running") return "Läuft…";
    if (status === "done") return "Fertig";
    if (status === "failed") return "Fehler";
    // S10/R10: set only after the fact, once orchestrator.run() has returned
    // and ctx.skipped_modules says a module did nothing despite on_done(ok=true) —
    // e.g. "documents" with no write access on ir.attachment.
    if (status === "skipped") return "Übersprungen (keine Rechte)";
    // Run-level (RunRecord.status), not a module status — some companies
    // succeeded, some failed outright (S16/STATUS_PARTIAL).
    if (status === "partial") return "Teilweise erfolgreich";
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
        resetRunTimer();
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
      var row = state.runModules.filter(function (m) { return m.key === payload.key; })[0];
      if (row) row.status = payload.status;
    });
    source.addEventListener("status", function (e) {
      applyRunStatus(JSON.parse(e.data));
    });
    source.addEventListener("end", function (e) {
      source.close();
      state.source = null;
      var fallbackStatus = null;
      try { fallbackStatus = JSON.parse(e.data).status; } catch (err) { /* no/unparsable payload */ }
      api("/api/runs/" + encodeURIComponent(runId))
        .then(function (data) { applyRunStatus(data); maybeAutoFeedback(runId, data.status); })
        .catch(function () { if (fallbackStatus) maybeAutoFeedback(runId, fallbackStatus); });
    });
    source.onerror = function () {
      // EventSource retries on its own; only report a stream that is really gone.
      if (source.readyState === EventSource.CLOSED) {
        appendConsole("[Verbindung zum Ereignisstrom verloren]");
      }
    };
  }

  function applyRunStatus(data) {
    setText("stat-status", statusLabel(data.status));
    setText("stat-calls", data.llm_calls);
    setText("stat-tokens", data.llm_tokens);
    setText("stat-errors", (data.api_errors || []).length);
    setText("stat-records", data.journal_records);

    state.runModules = (data.modules || []).map(function (m) {
      return { key: m.key, status: m.status };
    });
    if (data.started_at && state.runStartedAt !== data.started_at) {
      startRunTimer(data.started_at);
    }
    if (data.status === "done" || data.status === "failed" || data.status === "partial") {
      stopRunTimer();
      tickRunTimer();  // one final render at the frozen elapsed time
      setText("stat-remaining", "0:00");
    }

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

    if (data.status === "done" || data.status === "failed" || data.status === "partial") {
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
        var parts = [data.deleted + " von " + data.total + " Datensätzen gelöscht"];
        if (data.archived) parts.push(data.archived + " archiviert (nicht endgültig löschbar)");
        if (data.failed && data.failed.length) parts.push(data.failed.length + " Modell(e) fehlgeschlagen");
        setText("cleanup-hint", parts.join(", ") + ".");
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
    setHidden("btn-feedback-open", false);
    loadDefaults();
    if (!tutorialSeen()) showTutorial();
  }).catch(function () { /* not logged in yet — the login panel stays */ });
})();
