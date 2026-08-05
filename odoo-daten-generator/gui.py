#!/usr/bin/env python3
"""Native Desktop GUI for the Odoo Demo Data Generator.

Run from the odoo-daten-generator directory:
    python3 gui.py
"""

import os
import sys
import queue
import logging
import threading
import configparser

import customtkinter as ctk

# ---------------------------------------------------------------------------
# Backend imports
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from odoo_client import OdooJson2Client  # noqa: E402
import odoo_actions  # noqa: E402
from llm_service import LLMService, get_language_name  # noqa: E402
from config import DemoCriteria, ModuleSelections, RunContext  # noqa: E402
import orchestrator  # noqa: E402
from logging_setup import configure_logging, QueueLogHandler  # noqa: E402

configure_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
WANTED_MODULES = [
    "crm", "sale", "account", "hr", "project",
    "hr_timesheet", "mrp", "hr_recruitment",
]
MODULE_LABELS = {
    "crm": "CRM",
    "sale": "Verkauf",
    "account": "Buchhaltung",
    "hr": "Personal",
    "project": "Projekte",
    "hr_timesheet": "Zeiterfassung",
    "mrp": "Fertigung",
    "hr_recruitment": "Recruiting",
    "documents": "Dokumente (PDFs)",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _section_label(parent, text: str):
    """Bold section header with subtle separator line."""
    ctk.CTkLabel(
        parent,
        text=text,
        font=ctk.CTkFont(size=14, weight="bold"),
        anchor="w",
    ).pack(fill="x", pady=(14, 2))
    ctk.CTkFrame(parent, height=1, fg_color=("gray70", "gray35")).pack(fill="x", pady=(0, 6))


def _spin_row(parent, label: str, default: int, min_val: int = 0, max_val: int = 50):
    """Labelled integer spinner row.

    Returns (frame, IntVar).  Caller must pack the frame.
    """
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    label_widget = ctk.CTkLabel(frame, text=label, anchor="w", width=300)
    label_widget.pack(side="left", padx=(0, 10))

    var = ctk.IntVar(value=default)

    def _dec():
        v = var.get()
        if v > min_val:
            var.set(v - 1)

    def _inc():
        v = var.get()
        if v < max_val:
            var.set(v + 1)

    ctk.CTkButton(frame, text="−", width=28, height=28, command=_dec).pack(side="left")
    entry = ctk.CTkEntry(frame, textvariable=var, width=54, justify="center", height=28)
    entry.pack(side="left", padx=2)
    ctk.CTkButton(frame, text="+", width=28, height=28, command=_inc).pack(side="left")
    return frame, var


def _fetch_existing_data(client) -> tuple:
    """Fetch existing customer companies and sellable products from Odoo."""
    existing_companies = client.search_read(
        'res.partner',
        [["is_company", "=", True], ["customer_rank", ">", 0]],
        fields=["id"],
        limit=0,
    )
    existing_products = client.search_read(
        'product.product',
        [["active", "=", True]],
        fields=["id"],
        limit=500,
    )
    return (
        [r["id"] for r in existing_companies],
        [r["id"] for r in existing_products],
    )


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Odoo Demo-Daten Generator")
        self.geometry("960x760")
        self.minsize(820, 620)
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self._config = self._load_config()

        # Runtime state (populated by screen 2)
        self.client: OdooJson2Client | None = None
        self.llm: LLMService | None = None
        self.installed_modules: set = set()
        self.feature_flags: dict = {}
        self.company_name: str | None = None
        self.language_code: str = "de_DE"
        self.language_name: str = "German"
        self.suggested_industry: str | None = None
        self.detected_odoo_version: str | None = None
        self.existing_company_ids: list = []
        self.existing_product_ids: list = []

        # Log queue (used by screen 4)
        self._log_queue: queue.Queue = queue.Queue()

        self._show_screen1()

    # -----------------------------------------------------------------------
    # Config helpers
    # -----------------------------------------------------------------------

    def _load_config(self) -> configparser.ConfigParser:
        cfg = configparser.ConfigParser()
        cfg.read(os.path.join(_HERE, "config.ini"))
        return cfg

    def _cfg(self, section: str, key: str, fallback: str = "") -> str:
        try:
            return self._config.get(section, key) or fallback
        except (configparser.NoSectionError, configparser.NoOptionError):
            return fallback

    # -----------------------------------------------------------------------
    # Screen management
    # -----------------------------------------------------------------------

    def _clear(self):
        for w in self.winfo_children():
            w.destroy()

    # -----------------------------------------------------------------------
    # Screen 1: Verbindung konfigurieren
    # -----------------------------------------------------------------------

    def _show_screen1(self):
        self._clear()

        root_frame = ctk.CTkFrame(self)
        root_frame.pack(fill="both", expand=True, padx=30, pady=30)

        ctk.CTkLabel(
            root_frame,
            text="Verbindung konfigurieren",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(pady=(10, 24))

        form = ctk.CTkFrame(root_frame, fg_color="transparent")
        form.pack(fill="x", padx=30)

        def _field(lbl: str, default: str, show: str = "") -> ctk.StringVar:
            row = ctk.CTkFrame(form, fg_color="transparent")
            row.pack(fill="x", pady=5)
            ctk.CTkLabel(row, text=lbl, width=230, anchor="w").pack(side="left")
            var = ctk.StringVar(value=default)
            ctk.CTkEntry(row, textvariable=var, width=420, show=show).pack(side="left")
            return var

        odoo_url_var = _field("Odoo URL:", self._cfg("odoo", "url"))
        odoo_db_var = _field("Datenbank:", self._cfg("odoo", "db"))
        odoo_user_var = _field("Benutzername:", self._cfg("odoo", "username"))

        odoo_key_default = self._cfg("odoo", "api_key") or os.environ.get("ODOO_API_KEY", "")
        odoo_key_var = _field("Odoo API-Schlüssel:", odoo_key_default, show="•")

        ctk.CTkLabel(form, text="── LLM-Verbindung ──", anchor="w",
                     text_color=("gray50", "gray60")).pack(fill="x", pady=(14, 2))

        groq_key = self._cfg("llm", "api_key") or os.environ.get("GROQ_API_KEY", "")
        gemini_key = self._cfg("gemini", "api_key") or os.environ.get("GEMINI_API_KEY", "")
        llm_key_default = groq_key or gemini_key
        if groq_key:
            llm_model_default = self._cfg("llm", "model") or "llama-3.3-70b-versatile"
        else:
            llm_model_default = self._cfg("gemini", "model") or "gemini-1.5-flash"

        llm_key_var = _field("LLM API-Schlüssel:", llm_key_default, show="•")
        llm_model_var = _field("LLM Modell:", llm_model_default)

        status_label = ctk.CTkLabel(root_frame, text="", text_color="red")
        status_label.pack(pady=(12, 0))

        def _on_connect():
            url = odoo_url_var.get().strip()
            db = odoo_db_var.get().strip()
            odoo_key = odoo_key_var.get().strip()
            llm_key = llm_key_var.get().strip()
            llm_model = llm_model_var.get().strip()
            if not all([url, db, odoo_key, llm_key, llm_model]):
                status_label.configure(text="Bitte alle Pflichtfelder ausfüllen.")
                return
            self._show_screen2(url, db, odoo_key, llm_key, llm_model)

        ctk.CTkButton(root_frame, text="Verbinden →", width=220, command=_on_connect).pack(pady=24)

    # -----------------------------------------------------------------------
    # Screen 2: Verbindung wird hergestellt…
    # -----------------------------------------------------------------------

    def _show_screen2(self, url: str, db: str, odoo_key: str, llm_key: str, llm_model: str):
        self._clear()

        outer = ctk.CTkFrame(self)
        outer.pack(fill="both", expand=True, padx=30, pady=30)

        ctk.CTkLabel(
            outer,
            text="Verbindung wird hergestellt…",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(pady=(10, 24))

        status_frame = ctk.CTkFrame(outer, fg_color="transparent")
        status_frame.pack(fill="x", padx=40)

        rows: dict = {}

        def _add_row(key: str, label: str):
            row = ctk.CTkFrame(status_frame, fg_color="transparent")
            row.pack(fill="x", pady=4)
            icon = ctk.CTkLabel(row, text="⏳", width=30, font=ctk.CTkFont(size=16))
            icon.pack(side="left")
            ctk.CTkLabel(row, text=label, width=220, anchor="w").pack(side="left", padx=6)
            detail = ctk.CTkLabel(row, text="", anchor="w", text_color=("gray50", "gray60"))
            detail.pack(side="left", fill="x", expand=True)
            rows[key] = (icon, detail)

        _add_row("odoo", "Odoo-Verbindung")
        _add_row("company", "Firmenname")
        _add_row("language", "Sprache")
        _add_row("modules", "Installierte Module")
        _add_row("version", "Odoo-Version")
        _add_row("existing", "Vorhandene Stammdaten")
        _add_row("llm", "LLM-Verbindung")

        weiter_btn = ctk.CTkButton(outer, text="Weiter →", width=220,
                                   state="disabled", command=self._show_screen3)
        weiter_btn.pack(pady=24)

        ctk.CTkButton(outer, text="← Zurück", width=120,
                      fg_color="transparent", border_width=1,
                      command=self._show_screen1).pack()

        # --- Helpers for thread-safe row updates ---
        def _set(key: str, ok: bool, detail_text: str = ""):
            icon, detail = rows[key]
            icon.configure(text="✅" if ok else "❌")
            color = ("green", "#55aa55") if ok else ("red", "#cc4444")
            detail.configure(text=detail_text, text_color=color)

        def _pending(key: str):
            icon, _ = rows[key]
            icon.configure(text="⏳")

        def _run():
            all_ok = True

            # -- Odoo connection --
            self.after(0, lambda: _pending("odoo"))
            try:
                self.client = OdooJson2Client(url, db, odoo_key)
                self.client.search_read("res.lang", [["active", "=", True]],
                                        fields=["id"], limit=1)
                self.after(0, lambda: _set("odoo", True, "OK"))
            except Exception as exc:
                msg = str(exc)[:100]
                self.after(0, lambda m=msg: _set("odoo", False, m))
                all_ok = False

            # -- Company name --
            self.after(0, lambda: _pending("company"))
            if all_ok:
                try:
                    name = odoo_actions.get_main_company_name(self.client)
                    self.company_name = name
                    display = name or "–"
                    self.after(0, lambda d=display: _set("company", True, d))
                except Exception as exc:
                    msg = str(exc)[:100]
                    self.after(0, lambda m=msg: _set("company", False, m))

            # -- Language --
            self.after(0, lambda: _pending("language"))
            if all_ok:
                try:
                    lang_code = odoo_actions.get_main_company_language(self.client)
                    self.language_code = lang_code
                    self.language_name = get_language_name(lang_code)
                    display = f"{lang_code} ({self.language_name})"
                    self.after(0, lambda d=display: _set("language", True, d))
                except Exception as exc:
                    msg = str(exc)[:100]
                    self.after(0, lambda m=msg: _set("language", False, m))

            # -- Installed modules --
            self.after(0, lambda: _pending("modules"))
            if all_ok:
                try:
                    mods = odoo_actions.get_installed_modules(self.client, WANTED_MODULES)
                    self.installed_modules = mods
                    mod_str = ", ".join(MODULE_LABELS.get(m, m) for m in sorted(mods)) or "–"
                    self.after(0, lambda d=mod_str: _set("modules", True, d))
                    try:
                        self.feature_flags = odoo_actions.get_enabled_features(self.client, mods)
                    except Exception:
                        self.feature_flags = {}
                except Exception as exc:
                    msg = str(exc)[:100]
                    self.after(0, lambda m=msg: _set("modules", False, m))

            # -- Odoo server version (non-fatal: detection failure/unknown version
            # degrades gracefully, does not block "Weiter") --
            self.after(0, lambda: _pending("version"))
            if all_ok:
                try:
                    version = odoo_actions.get_server_version(self.client)
                    self.detected_odoo_version = version
                    if version:
                        warnings = odoo_actions.check_field_compatibility(self.client)
                        display = version
                        if warnings:
                            display += f" · {len(warnings)} Feld-Warnung(en) siehe Log"
                        self.after(0, lambda d=display: _set("version", True, d))
                    else:
                        self.after(0, lambda: _set("version", False, "unbekannt"))
                except Exception as exc:
                    msg = str(exc)[:100]
                    self.after(0, lambda m=msg: _set("version", False, m))

            # -- Existing master data --
            self.after(0, lambda: _pending("existing"))
            if all_ok:
                try:
                    c_ids, p_ids = _fetch_existing_data(self.client)
                    self.existing_company_ids = c_ids
                    self.existing_product_ids = p_ids
                    n_c, n_p = len(c_ids), len(p_ids)
                    display = f"{n_c} Kunden, {n_p} Produkte" if (n_c or n_p) else "Keine vorhanden"
                    self.after(0, lambda d=display: _set("existing", True, d))
                except Exception as exc:
                    msg = str(exc)[:100]
                    self.after(0, lambda m=msg: _set("existing", False, m))

            # -- LLM connection --
            self.after(0, lambda: _pending("llm"))
            provider = "groq" if llm_key.startswith("gsk_") else "gemini"
            try:
                self.llm = LLMService(llm_key, llm_model, provider)
                result = self.llm._call("Antworte nur mit dem Wort: OK", timeout=30)
                if not result:
                    raise RuntimeError("Leere LLM-Antwort")
                # Bonus: industry detection
                if self.company_name:
                    try:
                        industry = self.llm.determine_industry_from_company_name(self.company_name)
                        self.suggested_industry = industry
                    except Exception:
                        pass
                display = f"{provider} / {llm_model}"
                self.after(0, lambda d=display: _set("llm", True, d))
            except Exception as exc:
                msg = str(exc)[:100]
                self.after(0, lambda m=msg: _set("llm", False, m))
                all_ok = False

            if all_ok:
                self.after(0, lambda: weiter_btn.configure(state="normal"))

        threading.Thread(target=_run, daemon=True).start()

    # -----------------------------------------------------------------------
    # Screen 3: Konfiguration
    # -----------------------------------------------------------------------

    def _show_screen3(self):
        self._clear()

        outer = ctk.CTkFrame(self)
        outer.pack(fill="both", expand=True)

        # Fixed title bar
        title_bar = ctk.CTkFrame(outer, fg_color="transparent")
        title_bar.pack(fill="x", padx=30, pady=(20, 0))
        ctk.CTkLabel(
            title_bar,
            text="Konfiguration",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side="left")

        # Scrollable content area
        scroll = ctk.CTkScrollableFrame(outer)
        scroll.pack(fill="both", expand=True, padx=30, pady=10)

        # ---- Modus ----
        _section_label(scroll, "Modus")
        mode_var = ctk.StringVar(value="master")
        rb_master = ctk.CTkRadioButton(
            scroll,
            text="Nur Stammdaten anlegen (Kunden, Produkte)",
            variable=mode_var, value="master",
        )
        rb_master.pack(anchor="w", pady=2)
        rb_both = ctk.CTkRadioButton(
            scroll,
            text="Stammdaten anlegen UND Bewegungsdaten erstellen",
            variable=mode_var, value="both",
        )
        rb_both.pack(anchor="w", pady=2)

        use_existing_var = ctk.BooleanVar(value=bool(self.existing_company_ids or self.existing_product_ids))
        skip_master_var = ctk.BooleanVar(value=False)
        if self.existing_company_ids or self.existing_product_ids:
            n_c = len(self.existing_company_ids)
            n_p = len(self.existing_product_ids)
            ctk.CTkCheckBox(
                scroll,
                text=f"Vorhandene Daten einbeziehen ({n_c} Kunden, {n_p} Produkte)",
                variable=use_existing_var,
            ).pack(anchor="w", pady=(6, 2))
            ctk.CTkCheckBox(
                scroll,
                text="Keine neuen Stammdaten anlegen (nur vorhandene verwenden)",
                variable=skip_master_var,
            ).pack(anchor="w", pady=(2, 2))

        # ---- Branche ----
        _section_label(scroll, "Branche")
        industry_var = ctk.StringVar(value=self.suggested_industry or "IT-Dienstleistung")
        ctk.CTkEntry(scroll, textvariable=industry_var, width=420).pack(anchor="w", pady=4)

        # ---- Kundendaten + Produkte (wrapped so they can be hidden together) ----
        master_data_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        master_data_frame.pack(fill="x")

        # ---- Kundendaten ----
        _section_label(master_data_frame, "Kundendaten")
        f, n_companies = _spin_row(master_data_frame, "Anzahl Unternehmen", 3, 1, 20)
        f.pack(anchor="w", pady=2)
        f, n_delivery = _spin_row(master_data_frame, "Lieferadressen pro Unternehmen", 1, 0, 5)
        f.pack(anchor="w", pady=2)
        f, n_invoice = _spin_row(master_data_frame, "Rechnungsadressen pro Unternehmen", 1, 0, 5)
        f.pack(anchor="w", pady=2)
        f, n_other = _spin_row(master_data_frame, "Weitere Kontakte pro Unternehmen", 1, 0, 5)
        f.pack(anchor="w", pady=2)

        # ---- Produkte ----
        _section_label(master_data_frame, "Produkte")
        f, n_services = _spin_row(master_data_frame, "Dienstleistungen", 5, 0, 50)
        f.pack(anchor="w", pady=2)
        f, n_consumables = _spin_row(master_data_frame, "Verbrauchsmaterialien", 3, 0, 50)
        f.pack(anchor="w", pady=2)
        f, n_storables = _spin_row(master_data_frame, "Lagerartikel", 3, 0, 50)
        f.pack(anchor="w", pady=2)

        def _on_skip_toggle(*_):
            if skip_master_var.get():
                use_existing_var.set(True)
                master_data_frame.pack_forget()
            else:
                master_data_frame.pack(fill="x")

        skip_master_var.trace_add("write", _on_skip_toggle)

        # ---- Module ----
        module_section_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        module_section_frame.pack(fill="x", pady=(14, 0))
        module_title = ctk.CTkLabel(
            module_section_frame,
            text="Module (nur bei Bewegungsdaten)",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        )
        module_title.pack(fill="x")
        ctk.CTkFrame(module_section_frame, height=1,
                     fg_color=("gray70", "gray35")).pack(fill="x", pady=(2, 6))

        module_widgets: dict = {}  # key -> dict of IntVar / BooleanVar

        def _module_block(parent, key: str, label: str, subfields_fn):
            """Build one module checkbox + collapsible sub-field panel."""
            block = ctk.CTkFrame(parent, fg_color=("gray92", "gray18"), corner_radius=8)
            block.pack(fill="x", pady=3)

            header = ctk.CTkFrame(block, fg_color="transparent")
            header.pack(fill="x", padx=10, pady=(8, 4))

            chk_var = ctk.BooleanVar(value=False)
            chk = ctk.CTkCheckBox(header, text=label, variable=chk_var,
                                   font=ctk.CTkFont(weight="bold"))
            chk.pack(side="left")

            sub_frame = ctk.CTkFrame(block, fg_color="transparent")

            vars_dict = subfields_fn(sub_frame)
            vars_dict["_enabled"] = chk_var
            module_widgets[key] = vars_dict

            def _toggle(*_):
                if chk_var.get():
                    sub_frame.pack(fill="x", padx=20, pady=(0, 8))
                else:
                    sub_frame.pack_forget()

            chk_var.trace_add("write", _toggle)

        installed = self.installed_modules

        def _sub_crm(parent):
            f, v_count = _spin_row(parent, "Anzahl Opportunities", 10, 1, 200)
            f.pack(anchor="w", pady=2)
            v_leads = None
            if (self.feature_flags or {}).get('crm_leads'):
                f2, v_leads = _spin_row(parent, "Anzahl Leads", 0, 0, 200)
                f2.pack(anchor="w", pady=2)

            chatter_var = ctk.BooleanVar(value=True)
            ctk.CTkCheckBox(parent, text="Chatter-Konversationen erstellen",
                            variable=chatter_var).pack(anchor="w", pady=(6, 2))

            chatter_sub = ctk.CTkFrame(parent, fg_color="transparent")

            # Style selector
            ctk.CTkLabel(chatter_sub, text="Konversationsstil:", anchor="w").pack(anchor="w")
            chatter_style_var = ctk.StringVar(value="mixed")
            _style_row = ctk.CTkFrame(chatter_sub, fg_color="transparent")
            _style_row.pack(anchor="w", fill="x", pady=(0, 4))
            for _val, _lbl in [
                ("notes_only", "Nur interne Notizen"),
                ("mixed", "Gemischt (E-Mails + Notizen)"),
                ("full_email", "Vollständige E-Mail-Konversation"),
            ]:
                ctk.CTkRadioButton(
                    _style_row, text=_lbl, variable=chatter_style_var, value=_val
                ).pack(anchor="w", pady=1)

            # Messages per opportunity
            f_msg, v_msg_count = _spin_row(chatter_sub, "Nachrichten pro Opportunity", 4, 2, 8)
            f_msg.pack(anchor="w", pady=2)

            def _toggle_chatter_sub(*_):
                if chatter_var.get():
                    chatter_sub.pack(anchor="w", fill="x", padx=16, pady=(0, 4))
                else:
                    chatter_sub.pack_forget()

            chatter_var.trace_add("write", _toggle_chatter_sub)
            _toggle_chatter_sub()

            act_enabled_var = ctk.BooleanVar(value=True)
            ctk.CTkCheckBox(parent, text="Aktivitäten erstellen",
                            variable=act_enabled_var).pack(anchor="w", pady=(2, 2))

            act_frame = ctk.CTkFrame(parent, fg_color="transparent")

            # Slider: Vergangenheit %
            v_act_past = ctk.IntVar(value=30)
            v_act_past_str = ctk.StringVar(value="30%")
            v_act_past.trace_add("write", lambda *_: v_act_past_str.set(f"{v_act_past.get()}%"))
            _row_past = ctk.CTkFrame(act_frame, fg_color="transparent")
            _row_past.pack(anchor="w", fill="x")
            ctk.CTkLabel(_row_past, text="Vergangenheit %", anchor="w", width=200).pack(side="left")
            ctk.CTkSlider(_row_past, from_=0, to=100, variable=v_act_past,
                          number_of_steps=100).pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(_row_past, textvariable=v_act_past_str, width=40, anchor="e").pack(side="left")

            # Slider: Heute %
            v_act_today = ctk.IntVar(value=20)
            v_act_today_str = ctk.StringVar(value="20%")
            v_act_today.trace_add("write", lambda *_: v_act_today_str.set(f"{v_act_today.get()}%"))
            _row_today = ctk.CTkFrame(act_frame, fg_color="transparent")
            _row_today.pack(anchor="w", fill="x")
            ctk.CTkLabel(_row_today, text="Heute %", anchor="w", width=200).pack(side="left")
            ctk.CTkSlider(_row_today, from_=0, to=100, variable=v_act_today,
                          number_of_steps=100).pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(_row_today, textvariable=v_act_today_str, width=40, anchor="e").pack(side="left")

            # Computed label: Zukunft %
            v_future_str = ctk.StringVar(value="Zukunft: 50%")

            def _update_future(*_):
                future = max(0, 100 - v_act_past.get() - v_act_today.get())
                v_future_str.set(f"Zukunft: {future}%")

            v_act_past.trace_add("write", _update_future)
            v_act_today.trace_add("write", _update_future)
            ctk.CTkLabel(act_frame, textvariable=v_future_str, anchor="w").pack(anchor="w", pady=(2, 0))

            def _toggle_act(*_):
                if act_enabled_var.get():
                    act_frame.pack(anchor="w", fill="x", padx=10, pady=(0, 4))
                else:
                    act_frame.pack_forget()

            act_enabled_var.trace_add("write", _toggle_act)
            _toggle_act()

            return {
                "count": v_count,
                "leads_count": v_leads,
                "chatter_enabled": chatter_var,
                "chatter_style": chatter_style_var,
                "chatter_msg_count": v_msg_count,
                "act_enabled": act_enabled_var,
                "act_past": v_act_past,
                "act_today": v_act_today,
            }

        def _sub_sale(parent):
            f, v = _spin_row(parent, "Anzahl Aufträge", 10, 1, 200)
            f.pack(anchor="w", pady=2)

            ctk.CTkLabel(parent, text="Bestätigt (%)", anchor="w").pack(anchor="w")
            _default_confirm_pct = ModuleSelections().sale_confirm_pct
            v_confirm_pct = ctk.IntVar(value=_default_confirm_pct)
            v_confirm_pct_str = ctk.StringVar(value=f"{_default_confirm_pct}%")
            v_confirm_pct.trace_add("write", lambda *_: v_confirm_pct_str.set(f"{v_confirm_pct.get()}%"))
            _row_cp = ctk.CTkFrame(parent, fg_color="transparent")
            _row_cp.pack(anchor="w", fill="x")
            ctk.CTkSlider(_row_cp, from_=0, to=100, variable=v_confirm_pct,
                          number_of_steps=100).pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(_row_cp, textvariable=v_confirm_pct_str, width=40, anchor="e").pack(side="left")

            return {"count": v, "confirm_pct": v_confirm_pct}

        def _sub_account(parent):
            f, v = _spin_row(parent, "Anzahl Rechnungen", 10, 1, 200)
            f.pack(anchor="w", pady=2)
            # Static default (no live cross-widget binding in this codebase); the
            # field's real default is None (derive from Anzahl Rechnungen) — this
            # is only what the spinner shows before the user touches it.
            _default_bills = 5
            f2, v2 = _spin_row(parent, "Anzahl Eingangsrechnungen", _default_bills, 0, 200)
            f2.pack(anchor="w", pady=2)
            bank_var = ctk.BooleanVar(value=True)
            ctk.CTkCheckBox(parent, text="Banktransaktionen erstellen",
                            variable=bank_var).pack(anchor="w", pady=2)
            return {"count": v, "bills_count": v2, "bank_transactions": bank_var}

        def _sub_hr(parent):
            f, v_count = _spin_row(parent, "Anzahl Mitarbeiter", 10, 1, 200)
            f.pack(anchor="w", pady=2)

            to_enabled = ctk.BooleanVar(value=True)
            ctk.CTkCheckBox(parent, text="Urlaubsdaten erstellen",
                            variable=to_enabled).pack(anchor="w", pady=(6, 2))

            to_frame = ctk.CTkFrame(parent, fg_color="transparent")

            f2, v_entries = _spin_row(to_frame, "Urlaubsantraege pro Mitarbeiter", 2, 1, 20)
            f2.pack(anchor="w", pady=2)

            ctk.CTkLabel(to_frame, text="Ø Urlaubsdauer (Tage)", anchor="w").pack(anchor="w")
            v_avg_len = ctk.IntVar(value=5)
            _row_avg = ctk.CTkFrame(to_frame, fg_color="transparent")
            _row_avg.pack(anchor="w", fill="x")
            ctk.CTkSlider(_row_avg, from_=1, to=30, variable=v_avg_len,
                          number_of_steps=29).pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(_row_avg, textvariable=v_avg_len, width=35, anchor="e").pack(side="left")

            ctk.CTkLabel(to_frame, text="Vergangenheit \u2190 Zukunft (%)", anchor="w").pack(anchor="w")
            v_past_future = ctk.IntVar(value=30)
            v_past_future_str = ctk.StringVar(value="30%")
            v_past_future.trace_add("write", lambda *_: v_past_future_str.set(f"{v_past_future.get()}%"))
            _row_pf = ctk.CTkFrame(to_frame, fg_color="transparent")
            _row_pf.pack(anchor="w", fill="x")
            ctk.CTkSlider(_row_pf, from_=0, to=100, variable=v_past_future,
                          number_of_steps=100).pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(_row_pf, textvariable=v_past_future_str, width=40, anchor="e").pack(side="left")

            f3, v_timescale = _spin_row(to_frame, "Zeitraum (Tage)", 180, 30, 730)
            f3.pack(anchor="w", pady=2)

            ctk.CTkLabel(to_frame, text="Genehmigt (%)", anchor="w").pack(anchor="w")
            v_validate_pct = ctk.IntVar(value=100)
            v_validate_pct_str = ctk.StringVar(value="100%")
            v_validate_pct.trace_add("write", lambda *_: v_validate_pct_str.set(f"{v_validate_pct.get()}%"))
            _row_vp = ctk.CTkFrame(to_frame, fg_color="transparent")
            _row_vp.pack(anchor="w", fill="x")
            ctk.CTkSlider(_row_vp, from_=0, to=100, variable=v_validate_pct,
                          number_of_steps=100).pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(_row_vp, textvariable=v_validate_pct_str, width=40, anchor="e").pack(side="left")

            def _toggle_to(*_):
                if to_enabled.get():
                    to_frame.pack(anchor="w", fill="x", padx=10, pady=(0, 4))
                else:
                    to_frame.pack_forget()

            to_enabled.trace_add("write", _toggle_to)
            _toggle_to()

            return {
                "count": v_count,
                "to_enabled": to_enabled,
                "to_entries": v_entries,
                "to_avg_len": v_avg_len,
                "to_past_future": v_past_future,
                "to_timescale": v_timescale,
                "to_validate_pct": v_validate_pct,
            }

        def _sub_project(parent):
            f, v = _spin_row(parent, "Anzahl Projekte", 5, 1, 50)
            f.pack(anchor="w", pady=2)
            f2, v2 = _spin_row(parent, "Aufgaben pro Projekt", 10, 1, 50)
            f2.pack(anchor="w", pady=2)
            return {"count": v, "tasks_per_project": v2}

        def _sub_timesheet(parent):
            f, v = _spin_row(parent, "Anzahl Zeiteinträge", 30, 1, 500)
            f.pack(anchor="w", pady=2)
            return {"count": v}

        def _sub_mrp(parent):
            f, v = _spin_row(parent, "Anzahl Fertigungsprodukte", 3, 1, 50)
            f.pack(anchor="w", pady=2)
            f2, v2 = _spin_row(parent, "Komponenten pro Stückliste", 4, 1, 20)
            f2.pack(anchor="w", pady=2)
            f3, v3 = _spin_row(parent, "Komponenten mit Sub-Stückliste", 2, 0, 20)
            f3.pack(anchor="w", pady=2)

            routings_on = getattr(self, 'feature_flags', {}).get('mrp_routings', False)
            v4 = ctk.IntVar(value=0)
            if routings_on:
                f4, v4 = _spin_row(parent, "Arbeitszentren", 3, 1, 10)
                f4.pack(anchor="w", pady=2)

            f5, v5 = _spin_row(parent, "Fertigungsauftraege", 5, 0, 20)
            f5.pack(anchor="w", pady=2)

            v6 = ctk.BooleanVar(value=False)
            if getattr(self, 'feature_flags', {}).get('quality', False):
                cb = ctk.CTkCheckBox(parent, text="Qualitaetspruefpunkte", variable=v6)
                cb.pack(anchor="w", pady=2)

            return {
                "num_products": v, "components_per_bom": v2, "sub_boms_per_product": v3,
                "num_workcenters": v4, "num_manufacturing_orders": v5, "create_quality_points": v6,
            }

        def _sub_recruiting(parent):
            f, v = _spin_row(parent, "Anzahl Stellen", 5, 1, 50)
            f.pack(anchor="w", pady=2)
            f2, v2 = _spin_row(parent, "Anzahl Bewerbungen", 15, 1, 200)
            f2.pack(anchor="w", pady=2)
            skills_var = ctk.BooleanVar(value=True)
            ctk.CTkCheckBox(parent, text="Kompetenzen erstellen",
                            variable=skills_var).pack(anchor="w", pady=2)
            f3, v3 = _spin_row(parent, "Anzahl Kompetenzarten", 3, 1, 20)
            f3.pack(anchor="w", pady=2)
            f4, v4 = _spin_row(parent, "Kompetenzen pro Kompetenzart", 4, 1, 20)
            f4.pack(anchor="w", pady=2)
            return {
                "num_jobs": v,
                "num_candidates": v2,
                "create_skills": skills_var,
                "num_skill_types": v3,
                "skills_per_type": v4,
            }

        def _sub_documents(parent):
            bill_pdfs_var = ctk.BooleanVar(value=True)
            ctk.CTkCheckBox(parent, text="PDF-Rechnungen für Eingangsrechnungen",
                            variable=bill_pdfs_var).pack(anchor="w", pady=2)
            cv_pdfs_var = ctk.BooleanVar(value=True)
            ctk.CTkCheckBox(parent, text="Lebenslauf-PDFs für Bewerber",
                            variable=cv_pdfs_var).pack(anchor="w", pady=2)
            return {
                "bill_pdfs": bill_pdfs_var,
                "cv_pdfs": cv_pdfs_var,
            }

        module_defs = [
            ("crm", "CRM", _sub_crm),
            ("sale", "Verkauf", _sub_sale),
            ("account", "Buchhaltung", _sub_account),
            ("hr", "Personal", _sub_hr),
            ("project", "Projekte", _sub_project),
            ("hr_timesheet", "Zeiterfassung", _sub_timesheet),
            ("mrp", "Fertigung", _sub_mrp),
            ("hr_recruitment", "Recruiting", _sub_recruiting),
        ]

        modules_container = ctk.CTkFrame(scroll, fg_color="transparent")
        modules_container.pack(fill="x")

        # Hide/show modules container based on mode
        def _update_modules_visibility(*_):
            if mode_var.get() == "both":
                modules_container.pack(fill="x")
            else:
                modules_container.pack_forget()

        mode_var.trace_add("write", _update_modules_visibility)
        _update_modules_visibility()

        for key, lbl, sub_fn in module_defs:
            if key in installed:
                _module_block(modules_container, key, lbl, sub_fn)

        # "documents" is a pseudo-module (PDF generation, ir.attachment) — not
        # a real Odoo app, so it's rendered unconditionally instead of being
        # gated by `installed` like everything in module_defs above.
        _module_block(modules_container, "documents", "Dokumente (PDFs)", _sub_documents)

        if not any(k in installed for k, _, _ in module_defs):
            ctk.CTkLabel(modules_container,
                         text="(Keine der unterstützten Module sind installiert.)",
                         text_color=("gray50", "gray60")).pack(anchor="w")

        # ---- Bottom bar ----
        bottom_bar = ctk.CTkFrame(outer, fg_color="transparent")
        bottom_bar.pack(fill="x", padx=30, pady=(0, 20))

        error_label = ctk.CTkLabel(bottom_bar, text="", text_color="red")
        error_label.pack(side="left")

        def _on_generate():
            mode_val = mode_var.get()
            industry_val = industry_var.get().strip() or "IT-Dienstleistung"

            criteria = DemoCriteria(
                mode=mode_val,
                industry=industry_val,
                num_companies=n_companies.get(),
                num_delivery_contacts=n_delivery.get(),
                num_invoice_contacts=n_invoice.get(),
                num_other_contacts=n_other.get(),
                num_services=n_services.get(),
                num_consumables=n_consumables.get(),
                num_storables=n_storables.get(),
            )

            sel = ModuleSelections()
            selected_modules: set = set()

            if mode_val == "both":
                for key, _, _ in module_defs:
                    if key not in module_widgets:
                        continue
                    w = module_widgets[key]
                    if not w["_enabled"].get():
                        continue
                    selected_modules.add(key)

                    if key == "crm":
                        sel.crm = w["count"].get()
                        if w.get("leads_count") is not None:
                            sel.leads = w["leads_count"].get()
                        if w["chatter_enabled"].get():
                            sel.crm_chatter = {
                                "enabled": True,
                                "style": w["chatter_style"].get(),
                                "messages_per_opp": w["chatter_msg_count"].get(),
                            }
                        if w["act_enabled"].get():
                            past = w["act_past"].get()
                            today = min(w["act_today"].get(), 100 - past)
                            sel.crm_activities = {"enabled": True, "past_pct": past, "today_pct": today}
                    elif key == "sale":
                        sel.sale = w["count"].get()
                        sel.sale_confirm_pct = w["confirm_pct"].get()
                    elif key == "account":
                        sel.account = w["count"].get()
                        sel.account_bills = w["bills_count"].get()
                        sel.create_bank_transactions = w["bank_transactions"].get()
                    elif key == "hr":
                        sel.hr = w["count"].get()
                        if w["to_enabled"].get():
                            sel.hr_timeoff = {
                                "enabled": True,
                                "entries_per_employee": w["to_entries"].get(),
                                "avg_length_days": w["to_avg_len"].get(),
                                "past_future_pct": w["to_past_future"].get(),
                                "timescale_days": w["to_timescale"].get(),
                                "validate_pct": w["to_validate_pct"].get(),
                            }
                    elif key == "project":
                        sel.project = w["count"].get()
                        sel.tasks_per_project = w["tasks_per_project"].get()
                    elif key == "hr_timesheet":
                        sel.hr_timesheet = w["count"].get()
                    elif key == "mrp":
                        n_prod = w["num_products"].get()
                        comp = w["components_per_bom"].get()
                        sub = min(w["sub_boms_per_product"].get(), comp)
                        sel.mrp = {
                            "num_products": n_prod,
                            "components_per_bom": comp,
                            "sub_boms_per_product": sub,
                            "num_workcenters": w["num_workcenters"].get(),
                            "num_manufacturing_orders": w["num_manufacturing_orders"].get(),
                            "create_quality_points": bool(w["create_quality_points"].get()),
                        }
                    elif key == "hr_recruitment":
                        sel.hr_recruitment = {
                            "num_jobs": w["num_jobs"].get(),
                            "num_candidates": w["num_candidates"].get(),
                            "create_skills": w["create_skills"].get(),
                            "num_skill_types": w["num_skill_types"].get(),
                            "skills_per_type": w["skills_per_type"].get(),
                        }

                # "documents" is a pseudo-module (see module render loop above)
                # not covered by module_defs, so it's handled as a standalone
                # block rather than inside the module_defs loop.
                if "documents" in module_widgets:
                    w = module_widgets["documents"]
                    if w["_enabled"].get():
                        selected_modules.add("documents")
                        sel.documents = {
                            "bill_pdfs_enabled": w["bill_pdfs"].get(),
                            "cv_pdfs_enabled": w["cv_pdfs"].get(),
                        }

            ctx = RunContext(
                criteria=criteria,
                module_selections=sel,
                industry=industry_val,
                language_name=self.language_name,
                language_code=self.language_code,
                gemini_model_name=self.llm.model_name,
                installed_modules=self.installed_modules,
                feature_flags=self.feature_flags,
            )

            if use_existing_var.get():
                ctx.company_ids.extend(self.existing_company_ids)
                ctx.product_ids.extend(self.existing_product_ids)

            ctx.skip_master_data = skip_master_var.get()

            self._show_screen4(ctx, sel, selected_modules)

        ctk.CTkButton(
            bottom_bar, text="Daten generieren →", width=240, command=_on_generate,
        ).pack(side="right")

    # -----------------------------------------------------------------------
    # Screen 4: Fortschritt
    # -----------------------------------------------------------------------

    def _show_screen4(self, ctx: RunContext, sel: ModuleSelections, selected_modules: set):
        self._clear()
        # Drain old log queue
        while not self._log_queue.empty():
            try:
                self._log_queue.get_nowait()
            except queue.Empty:
                break

        outer = ctk.CTkFrame(self)
        outer.pack(fill="both", expand=True, padx=30, pady=30)

        ctk.CTkLabel(
            outer,
            text="Daten werden generiert…",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(pady=(10, 16))

        # ---- Progress bars per active module ----
        progress_frame = ctk.CTkFrame(outer, fg_color="transparent")
        progress_frame.pack(fill="x", padx=10, pady=(0, 10))

        module_bars: dict = {}
        active_keys = [] if ctx.skip_master_data else ["stammdaten"]

        module_order_keys = ["mrp", "crm", "sale", "hr", "project",
                             "hr_timesheet", "account", "hr_recruitment", "documents"]
        for key in module_order_keys:
            # B10: gate on installed AND selected — ctx.installed_modules is now
            # the true Odoo-probed set (may be a superset of what the user picked).
            # "documents" is a pseudo-module with no installed_modules entry
            # (see orchestrator.py module_order) — gate on selection only.
            if key == "documents":
                if key in selected_modules:
                    active_keys.append(key)
            elif key in ctx.installed_modules and key in selected_modules:
                active_keys.append(key)

        for key in active_keys:
            label = MODULE_LABELS.get(key, key.upper()) if key != "stammdaten" else "Stammdaten"
            row = ctk.CTkFrame(progress_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=label, width=160, anchor="w").pack(side="left")
            bar = ctk.CTkProgressBar(row, width=400, mode="indeterminate")
            bar.pack(side="left", padx=8)
            bar.set(0)
            status_lbl = ctk.CTkLabel(row, text="Ausstehend", width=100,
                                      text_color=("gray50", "gray60"))
            status_lbl.pack(side="left")
            module_bars[key] = (bar, status_lbl)

        # ---- Log textbox ----
        ctk.CTkLabel(outer, text="Protokoll", anchor="w",
                     font=ctk.CTkFont(weight="bold")).pack(fill="x", pady=(6, 2))
        log_box = ctk.CTkTextbox(outer, height=260, wrap="word",
                                 font=ctk.CTkFont(family="Courier", size=11))
        log_box.pack(fill="both", expand=True)
        log_box.configure(state="disabled")

        # ---- Summary + error area (shown after completion) ----
        summary_frame = ctk.CTkFrame(outer, fg_color="transparent")
        # Not packed yet – shown after run completes

        # ---- Bottom buttons ----
        btn_frame = ctk.CTkFrame(outer, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(10, 0))

        restart_btn = ctk.CTkButton(btn_frame, text="Neu starten", width=180,
                                    state="disabled", command=self._show_screen1)
        restart_btn.pack(side="right")

        # ---------------------------------------------------------------
        # Helpers
        # ---------------------------------------------------------------

        def _log(text: str):
            """Append text to log box (must be called from main thread)."""
            log_box.configure(state="normal")
            log_box.insert("end", text + "\n")
            log_box.see("end")
            log_box.configure(state="disabled")

        def _set_bar(key: str, done: bool, error: bool = False):
            if key not in module_bars:
                return
            bar, lbl = module_bars[key]
            bar.stop()
            bar.configure(mode="determinate")
            if error:
                bar.set(1)
                bar.configure(progress_color="red")
                lbl.configure(text="Fehler", text_color="red")
            elif done:
                bar.set(1)
                lbl.configure(text="Fertig", text_color=("green", "#55cc55"))

        def _start_bar(key: str):
            if key not in module_bars:
                return
            bar, lbl = module_bars[key]
            bar.configure(mode="indeterminate")
            bar.start()
            lbl.configure(text="Läuft…", text_color=("blue", "#6699ff"))

        # Poll queue and update log box
        def _poll():
            try:
                while True:
                    msg = self._log_queue.get_nowait()
                    _log(msg)
            except queue.Empty:
                pass
            self.after(100, _poll)

        self.after(100, _poll)

        # ---------------------------------------------------------------
        # Background worker
        # ---------------------------------------------------------------

        _module_key_map = {
            "Stammdaten": "stammdaten",
            "mrp": "mrp", "crm": "crm", "sale": "sale",
            "account": "account", "hr": "hr",
            "project": "project", "hr_timesheet": "hr_timesheet",
            "hr_recruitment": "hr_recruitment",
        }

        def _run():
            queue_handler = QueueLogHandler(self._log_queue)
            queue_handler.setFormatter(logging.Formatter("%(message)s"))
            root_logger = logging.getLogger()
            root_logger.addHandler(queue_handler)

            def _ui_start(key):
                self.after(0, lambda k=key: _start_bar(k))

            def _ui_done(key, error=False):
                self.after(0, lambda k=key, e=error: _set_bar(k, done=True, error=e))

            def _on_module_start(name):
                _ui_start(_module_key_map.get(name, name))

            def _on_module_done(name, ok=True):
                _ui_done(_module_key_map.get(name, name), error=not ok)

            _ui_start("stammdaten")  # visual feedback during upfront LLM calls, before master_data itself starts

            try:
                orchestrator.run(self.client, self.llm, ctx,
                                  on_module_start=_on_module_start,
                                  on_module_done=_on_module_done)

            except Exception as exc:
                logger.error(f"Kritischer Fehler: {exc}")
                _ui_done("stammdaten", error=True)
            finally:
                root_logger.removeHandler(queue_handler)

            # Gather results
            api_errors = self.client.get_errors() if self.client else []
            total_calls = self.llm.total_calls if self.llm else 0
            total_tokens = self.llm.total_tokens if self.llm else 0

            def _show_results():
                summary_frame.pack(fill="x", pady=(10, 0))

                ctk.CTkLabel(
                    summary_frame,
                    text=f"Abgeschlossen — LLM-Anfragen: {total_calls},"
                         f" Token: {total_tokens},"
                         f" API-Fehler: {len(api_errors)}",
                    font=ctk.CTkFont(weight="bold"),
                    text_color=("red" if api_errors else "green",
                                "#cc4444" if api_errors else "#55cc55"),
                ).pack(anchor="w")

                if api_errors:
                    err_box = ctk.CTkTextbox(summary_frame, height=120,
                                             font=ctk.CTkFont(family="Courier", size=10))
                    err_box.pack(fill="x", pady=(6, 0))
                    for idx, err in enumerate(api_errors, 1):
                        line = (
                            f"[{idx}] {err.get('status_code', '?')} "
                            f"{err.get('url', '')} — "
                            f"{err.get('error_message', '')[:120]}\n"
                        )
                        err_box.insert("end", line)
                    err_box.configure(state="disabled")

                restart_btn.configure(state="normal")

            self.after(0, _show_results)

        threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = App()
    app.mainloop()
