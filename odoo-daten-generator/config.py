from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


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
    # mrp shape: {"num_products": int, "components_per_bom": int, "sub_boms_per_product": int,
    # "num_workcenters": int, "num_manufacturing_orders": int, "create_quality_points": bool,
    # "quality_fail_pct": int} — quality_fail_pct (S14/R18 additive) only has an effect when
    # create_quality_points is True; read in modules/mrp.py next to that flag.
    hr_recruitment: dict = field(default_factory=dict)
    hr_timeoff: dict = field(default_factory=dict)
    crm_chatter: dict = field(default_factory=dict)
    # crm_chatter shape: {"enabled": bool, "style": "notes_only"|"mixed"|"full_email", "messages_per_opp": int}
    # empty dict → disabled
    crm_activities: dict = field(default_factory=dict)
    # crm_activities shape: {"enabled": bool, "past_pct": int, "today_pct": int}
    # future_pct is implied: 100 - past_pct - today_pct
    # empty dict → activities disabled
    crm_lost: dict = field(default_factory=dict)
    # crm_lost shape: {"pct": int} (R11). A crm.py sub-feature like crm_chatter/
    # crm_activities, NOT its own orchestrated module — no WANTED_MODULES/
    # MODULE_RUN_ORDER entry, gated in build_selections inside the existing
    # `if _enabled(crm)` block. Still its own orchestrator.py module_order
    # step (must run after "sale") — see modules/crm.py's mark_lost_opportunities.
    documents: dict = field(default_factory=dict)
    # documents shape: {"bill_pdfs_enabled": bool, "cv_pdfs_enabled": bool}
    # empty dict → both stages disabled
    purchase: int = 0                    # number of purchase orders to create; 0 = skip
    purchase_confirm_pct: int = 70       # % of created POs to confirm, mirrors sale_confirm_pct
    stock: dict = field(default_factory=dict)
    # stock shape: {"avg_qty": int, "sub_locations": int, "second_warehouse": bool,
    # "tracking_lot_pct": int, "tracking_serial_pct": int, "tracking_serial_max": int,
    # "orderpoints_pct": int, "orderpoint_min_qty": int, "orderpoint_max_qty": int}
    # (S13/R13-R15, S14/R12 additive) — dict-gated like mrp/documents, NOT a bare int:
    # orchestrator.py's module_order key must equal this field's name ("stock") exactly,
    # since ModuleSelections.get(module_code) is getattr(self, module_code) below and the
    # gate is `elif not sel: continue` — a scalar int field named e.g. stock_avg_qty paired
    # with module_code "stock" would look up a nonexistent attribute and always skip silently.
    # avg_qty==0 with another key set (e.g. sub_locations>0, orderpoints_pct>0) still runs
    # the step — inventory.py's own early-return checks all four trigger keys, not avg_qty
    # alone. orderpoint_min_qty/orderpoint_max_qty are independent config values, not
    # derived from avg_qty (would degenerate to 0.0 on the avg_qty=0 path otherwise, S14/
    # Befund 7 — same reasoning as tracking_serial_max's own decoupling from avg_qty).
    hr_expense: dict = field(default_factory=dict)
    # hr_expense shape: {"count_per_employee": int, "approved_pct": int} (R19).
    # Field name must be "hr_expense", not "expenses" — matches WANTED_MODULES'
    # Odoo technical module name and orchestrator.py's module_order key, same
    # convention as hr_recruitment/hr_timesheet (see run_config.py's own note
    # on this).
    analytic: dict = field(default_factory=dict)
    # analytic shape: {"enabled": bool, "sale_pct": int, "purchase_pct": int,
    # "expense_pct": int} (S15/R20). NOT its own orchestrated module — no
    # WANTED_MODULES/MODULE_RUN_ORDER/orchestrator.py entry, no progress row.
    # Read independently by sale.py/purchase.py/expenses.py, each gated on
    # its OWN parent module being selected too (sale_pct is meaningless if
    # "sale" itself is off) — closer to documents' top-level shape than
    # crm_lost's (nested under one single parent's `if _enabled(crm)`), since
    # this has three unrelated parents, not one. sale_pct/purchase_pct/
    # expense_pct each default to 0 (that module's own off-switch, same
    # precedent as S14's orderpoints_pct/quality_fail_pct) once "enabled" is
    # true; "enabled" itself is the feature's own on/off (matching
    # documents/crm_chatter's top-level dict convention), not implied by the
    # three percentages being zero — an explicit gate is clearer than "are
    # all three sub-values coincidentally zero".

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
    # Besitzer (4): master_data.py:196, orchestrator.py:149 (Fallback-Partner),
    # run_config.py:533 (use_existing), web/jobs.py:490 (D8b, per-company fetch)
    partner_company_ids: List[int] = field(default_factory=list)
    # Besitzer (5): master_data.py:119, mrp.py:194 (Fertigprodukte),
    # orchestrator.py:169 (Fallback), run_config.py:534 (use_existing),
    # web/jobs.py:491 (D8b)
    product_ids: List[int] = field(default_factory=list)
    employee_ids: List[int] = field(default_factory=list)
    project_ids: List[int] = field(default_factory=list)
    order_ids: List[int] = field(default_factory=list)
    confirmed_order_ids: List[int] = field(default_factory=list)
    opportunity_ids: List[int] = field(default_factory=list)
    lead_ids: List[int] = field(default_factory=list)
    # Opportunity ids sale.py successfully linked to an order (R11) — lets
    # mark_lost_opportunities operate only on the unlinked remainder without
    # an extra search_read against the rate-limited live instance.
    linked_opportunity_ids: List[int] = field(default_factory=list)
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
    # Besitzer (2): accounting.py:403, purchase.py:247
    bill_ids: List[int] = field(default_factory=list)
    applicant_ids: List[int] = field(default_factory=list)
    # Vendor partners (supplier_rank>0) created this run — shared between accounting.py's
    # standalone vendor bills and purchase.py's POs so both draw from the same supplier set.
    # Besitzer (2): accounting.py:391, purchase.py:168
    supplier_ids: List[int] = field(default_factory=list)
    # Product ids master_data.py's _create_products created THIS run (S13/R13) —
    # distinct from product_ids, which also holds use_existing's pre-existing
    # customer product ids (merged in before master_data runs, run_config.py's
    # build_context) and mrp.py's finished goods/components. inventory.py's
    # lot/serial-tracking branch reads this to never write tracking-derived
    # stock.lot records against a real customer's pre-existing product.
    new_product_ids: List[int] = field(default_factory=list)
    # S15/R20: cost-center account.analytic.account ids, lazy+memoized via
    # odoo_actions.get_or_create_analytic_accounts — three independent
    # modules (sale/purchase/hr_expense) may each need these, and no single
    # one is guaranteed to run first, so whichever calls the helper first
    # populates this, the others reuse it. Optional, not a bare [] default:
    # None means "never attempted yet", [] means "attempted, genuinely
    # nothing came back" (e.g. account.analytic.plan create failed) — a
    # plain truthiness check on a bare list default can't tell those apart
    # and would retry (and duplicate the plan) on every subsequent call,
    # same reasoning as mrp.py's _get_company_id() wrapper-list memoization.
    # Besitzer (2): odoo_actions.py:430/:435/:438, web/jobs.py:497
    analytic_account_ids: Optional[List[int]] = None
    # S16/D2: the real res.company id this ctx is scoped to — distinct from
    # partner_company_ids above, which holds res.partner ids
    # (customer/company contacts from master_data.py), never a real
    # res.company id. Under D10 each RunContext is scoped to exactly one
    # company, populated by exactly one caller (web/jobs.py's per-company
    # loop, once, right after resolving that iteration's target company) —
    # a flat list, not a bare int, only because a future N-companies-per-ctx
    # scope wouldn't need another schema change; NOT a signal that more than
    # one entry is expected today. A second caller populating this MUST
    # switch to an Optional[List[int]]/None-sentinel first (same reasoning
    # as analytic_account_ids above) — a plain list default can't
    # distinguish "not yet resolved" from "resolved to nothing".
    res_company_ids: List[int] = field(default_factory=list)
