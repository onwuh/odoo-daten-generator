from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class DemoCriteria:
    mode: str
    industry: str
    num_companies: int
    num_delivery_contacts: int
    num_invoice_contacts: int
    num_other_contacts: int
    num_services: int
    num_consumables: int
    num_storables: int


@dataclass
class ModuleSelections:
    crm: int = 0
    leads: int = 0
    sale: int = 0
    sale_confirm_pct: int = 65  # % of created orders to confirm (B8); GUI slider default
    account: int = 0
    account_bills: Optional[int] = None  # vendor bill count (B7); None -> derive max(1, account // 2)
    create_bank_transactions: bool = False
    hr: int = 0
    project: int = 0
    tasks_per_project: int = 10
    hr_timesheet: int = 0
    mrp: dict = field(default_factory=dict)
    hr_recruitment: dict = field(default_factory=dict)
    hr_timeoff: dict = field(default_factory=dict)
    crm_chatter: dict = field(default_factory=dict)
    # crm_chatter shape: {"enabled": bool, "style": "notes_only"|"mixed"|"full_email", "messages_per_opp": int}
    # empty dict → disabled
    crm_activities: dict = field(default_factory=dict)
    # crm_activities shape: {"enabled": bool, "past_pct": int, "today_pct": int}
    # future_pct is implied: 100 - past_pct - today_pct
    # empty dict → activities disabled
    documents: dict = field(default_factory=dict)
    # documents shape: {"bill_pdfs_enabled": bool, "cv_pdfs_enabled": bool}
    # empty dict → both stages disabled
    purchase: int = 0                    # number of purchase orders to create; 0 = skip
    purchase_confirm_pct: int = 70       # % of created POs to confirm, mirrors sale_confirm_pct
    stock: dict = field(default_factory=dict)
    # stock shape: {"avg_qty": int} — dict-gated like mrp/documents, NOT a bare int:
    # orchestrator.py's module_order key must equal this field's name ("stock") exactly,
    # since ModuleSelections.get(module_code) is getattr(self, module_code) below and the
    # gate is `elif not sel: continue` — a scalar int field named e.g. stock_avg_qty paired
    # with module_code "stock" would look up a nonexistent attribute and always skip silently.

    def get(self, key: str, default=None):
        return getattr(self, key, default)


@dataclass
class RunContext:
    """Carries all runtime state between modules."""
    criteria: DemoCriteria
    module_selections: ModuleSelections
    industry: str
    language_name: str
    language_code: str
    gemini_model_name: str
    installed_modules: Set[str] = field(default_factory=set)
    skip_master_data: bool = False
    # Name banks from Gemini (product_names, employee_names, etc.)
    name_banks: Dict[str, List[str]] = field(default_factory=dict)
    # IDs created during the run (modules write here, subsequent modules read)
    company_ids: List[int] = field(default_factory=list)
    product_ids: List[int] = field(default_factory=list)
    employee_ids: List[int] = field(default_factory=list)
    project_ids: List[int] = field(default_factory=list)
    order_ids: List[int] = field(default_factory=list)
    confirmed_order_ids: List[int] = field(default_factory=list)
    opportunity_ids: List[int] = field(default_factory=list)
    lead_ids: List[int] = field(default_factory=list)
    workcenter_ids: List[int] = field(default_factory=list)
    # Components/raw materials created by MRP (purchase_ok=True, sale_ok=False).
    # Kept separate from product_ids (sellable finished goods) so vendor bills
    # and sale orders draw from the correct pool.
    component_ids: List[int] = field(default_factory=list)
    feature_flags: Dict[str, bool] = field(default_factory=dict)
    # Raw per-model create-access probe results (odoo_actions.probe_model_access),
    # keyed by technical model name — e.g. {"ir.attachment": False}. Distinct
    # from feature_flags: feature_flags names a small set of pre-existing,
    # multi-model settings (mrp_routings, quality, crm_leads); this is the
    # per-model result a module reads directly when no such named flag exists
    # (documents.py's ir.attachment gate, hr.py's leave models). Always read
    # with .get(model, True) — a model missing from this dict was never probed
    # (e.g. its parent module wasn't installed) and must not be treated as
    # blocked, or a module that is actually fine gets silently skipped (B1
    # error class).
    model_access: Dict[str, bool] = field(default_factory=dict)
    # Module keys a handler returned from early because model_access blocked
    # it entirely (not merely a sub-behaviour within it — see documents.py's
    # create_documents). Populated by module code, read by web/jobs.py after
    # orchestrator.run() returns: on_done(ok=True) already fired by then, and
    # a module function has no other channel to say "I did nothing" versus
    # "I genuinely succeeded" back to the locked on_module_done signature.
    skipped_modules: Set[str] = field(default_factory=set)
    # Customer invoices / vendor bills created THIS run — bank transaction
    # generation scopes to these instead of scanning all posted moves in the
    # DB, so re-running the generator doesn't duplicate transactions (B4).
    invoice_ids: List[int] = field(default_factory=list)
    bill_ids: List[int] = field(default_factory=list)
    applicant_ids: List[int] = field(default_factory=list)
    # Vendor partners (supplier_rank>0) created this run — shared between accounting.py's
    # standalone vendor bills and purchase.py's POs so both draw from the same supplier set.
    supplier_ids: List[int] = field(default_factory=list)
