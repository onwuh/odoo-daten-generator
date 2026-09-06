"""S17 safety net (S17-D9) — frozen equivalence snapshots for D5 + D16.

WHY THIS FILE EXISTS
--------------------
S17 rewrites 10 untyped `dict` fields of `ModuleSelections` into typed
dataclasses (D5) and renames RunContext's `company_ids` field to
`partner_company_ids` (D16). That refactoring
also rewrites ~38 `ModuleSelections(...)` constructions and ~40 field
assignments spread across 30 existing test files — so the existing suite is
edited by the very diff it is supposed to validate and cannot vouch for it.

This file is written BEFORE the first refactor line, against the dict-based
code, and is then FROZEN: neither it nor its golden JSONs under `fixtures/`
may be edited for the rest of the sprint. If a golden goes red during WP2 or
WP3, the production change is wrong — not the golden. Adjusting a golden to
match new output makes the whole sprint worthless.

WHAT IT COVERS
--------------
Netz A — `run_config.build_selections(payload)` -> (selections, selected).
    The complete write side of D5: every one of the 10 dict fields, every
    clamp, every payload default. Four payloads: everything on with
    non-default values, everything off, mode="master", and the clamp edges.

Netz B — module entry point -> the exact sequence of Odoo calls it issues.
    The read side of D5 for the seven value-richest consumers (mrp, stock,
    documents, hr_recruitment and the three `analytic` readers sale/purchase/
    expenses). A recording fake client captures every create/create_batch/
    write/call_method/search_read as (method, model, args, kwargs).

Netz C — the D16 rename needs no net of its own: `RunContext` is a dataclass
    without `__getattr__`, so a missed READ raises AttributeError. The §3 grep
    plus a green suite covers it. (Assignments do not raise — that gap is
    handled by the plan's grep, not here.)

NORMALIZATION, AND WHAT IT DELIBERATELY DOES NOT CHECK
------------------------------------------------------
Netz B normalizes every `YYYY-MM-DD[ HH:MM:SS]` string to `<DATE>`/`<DATETIME>`
before comparing. 17 production sites across 8 modules write
`datetime.now()`/`date.today()` straight into the recorded vals; a raw golden
would be second- or day-accurate and would go red on its own the next day —
and a net that reddens for no reason gets switched off instead of believed.

    Price, stated openly: Netz B does NOT check date logic. That is
    acceptable because D5/D16 touch no date logic at all, but the next
    reader must not assume a coverage that isn't there.

Generated PDF attachments (documents.py's base64 `raw`) are reduced to a
hash of the document with fpdf2's clock-derived `/ID` and `/CreationDate`
removed — the PDF's content must still match byte for byte, its timestamp
must not. Same caveat as above: PDF *dates* are not covered.

Netz A drops the `enabled` key from the four blocks that carry one
(`crm_chatter`, `crm_activities`, `hr_timeoff`, `analytic`). S17-D2 removes
those keys ("object present == feature active"), so keeping them would make
the net fail for an expected reason. This is the ONLY tolerated difference
between the pre- and post-refactor snapshot; it is applied by name, to those
four fields only, at the top level only.
"""
import base64
import copy
import dataclasses
import hashlib
import json
import os
import random
import re
import sys
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import run_config
from config import (
    AnalyticConfig,
    DemoCriteria,
    DocumentsConfig,
    ExpenseConfig,
    ModuleSelections,
    MrpConfig,
    RecruitmentConfig,
    RunContext,
    StockConfig,
)
from modules import documents, expenses, inventory, mrp, purchase, recruiting, sale

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
_GOLDEN_A = os.path.join(_FIXTURES, "s17_netz_a_selections.json")
_GOLDEN_B = os.path.join(_FIXTURES, "s17_netz_b_calls.json")

# S17-D2: these four blocks carry an "enabled" key today that the typed
# dataclasses drop (presence == active). Stripped by name, top level only.
_ENABLED_KEY_FIELDS = ("crm_chatter", "crm_activities", "hr_timeoff", "analytic")

_SEED = 1337


# ===========================================================================
# Netz A — payload -> ModuleSelections
# ===========================================================================

# (a) Everything on, every value deliberately different from its payload
#     default, plus existing_data_consent="granted" — without it
#     crm_chatter.use_db_names can never be True (run_config.py:285) and half
#     of that block stays unobserved.
_PAYLOAD_ALL_ON = {
    "mode": "both",
    "existing_data_consent": "granted",
    "modules": {
        "crm": {"enabled": True, "count": 11, "leads": 7,
                "chatter": {"enabled": True, "style": "full_email", "messages_per_opp": 9},
                "activities": {"enabled": True, "past_pct": 41, "today_pct": 23},
                "lost": {"enabled": True, "pct": 37}},
        "sale": {"enabled": True, "count": 13, "confirm_pct": 81},
        "account": {"enabled": True, "count": 17, "bills": 6, "bank_transactions": True},
        "hr": {"enabled": True, "count": 19,
               "timeoff": {"enabled": True, "entries_per_employee": 4, "avg_length_days": 7,
                           "past_future_pct": 44, "timescale_days": 210, "validate_pct": 88}},
        "project": {"enabled": True, "count": 5, "tasks_per_project": 12},
        "hr_timesheet": {"enabled": True, "count": 31},
        "mrp": {"enabled": True, "num_products": 6, "components_per_bom": 5,
                "sub_boms_per_product": 3, "num_workcenters": 4,
                "num_manufacturing_orders": 8, "create_quality_points": True,
                "quality_fail_pct": 15},
        "hr_recruitment": {"enabled": True, "num_jobs": 7, "num_candidates": 21,
                           "create_skills": True, "num_skill_types": 4, "skills_per_type": 6},
        "purchase": {"enabled": True, "count": 9, "confirm_pct": 58},
        "stock": {"enabled": True, "avg_qty": 77, "sub_locations": 4, "second_warehouse": True,
                  "tracking_lot_pct": 25, "tracking_serial_pct": 15, "tracking_serial_max": 9,
                  "orderpoints_pct": 33, "orderpoint_min_qty": 11, "orderpoint_max_qty": 42},
        "hr_expense": {"enabled": True, "count_per_employee": 6, "approved_pct": 64},
        "documents": {"enabled": True, "bill_pdfs": True, "cv_pdfs": False},
        "analytic": {"enabled": True, "sale_pct": 35, "purchase_pct": 22, "expense_pct": 14},
    },
}

# (b) Smoke case: every module explicitly off. After normalization this is
#     almost entirely {}-vs-None cells — it exercises the normalizer, not the
#     mapping. Not load-bearing on its own (see the plan's WP1 note).
_PAYLOAD_ALL_OFF = {
    "mode": "both",
    "existing_data_consent": "denied",
    "modules": {name: {"enabled": False} for name in (
        "crm", "sale", "account", "hr", "project", "hr_timesheet", "mrp",
        "hr_recruitment", "purchase", "stock", "hr_expense", "documents", "analytic",
    )},
}

# (c) Smoke case: master mode returns before reading `modules` at all.
_PAYLOAD_MASTER = {
    "mode": "master",
    "existing_data_consent": "granted",
    "modules": copy.deepcopy(_PAYLOAD_ALL_ON["modules"]),
}

# (d) All four cross-field clamps, each driven past its edge:
#     1. tracking_lot_pct + tracking_serial_pct > 100  -> serial = 100 - lot   (70/60 -> 30)
#     2. orderpoint_max_qty <= orderpoint_min_qty      -> max = min + 1        (20/20 -> 21)
#     3. sub_boms_per_product > components_per_bom     -> sub_boms = components (9/2 -> 2)
#     4. today_pct > 100 - past_pct                    -> today = 100 - past   (80/50 -> 20)
#     create_skills=False here so that bool gets non-default coverage too.
_PAYLOAD_CLAMPS = {
    "mode": "both",
    "existing_data_consent": "denied",
    "modules": {
        "crm": {"enabled": True, "count": 3, "leads": 1,
                "chatter": {"enabled": True, "style": "notes_only", "messages_per_opp": 1},
                "activities": {"enabled": True, "past_pct": 80, "today_pct": 50},
                "lost": {"enabled": True, "pct": 100}},
        "mrp": {"enabled": True, "num_products": 1, "components_per_bom": 2,
                "sub_boms_per_product": 9, "num_workcenters": 1,
                "num_manufacturing_orders": 1, "create_quality_points": False,
                "quality_fail_pct": 100},
        "stock": {"enabled": True, "avg_qty": 0, "sub_locations": 0, "second_warehouse": False,
                  "tracking_lot_pct": 70, "tracking_serial_pct": 60, "tracking_serial_max": 1,
                  "orderpoints_pct": 100, "orderpoint_min_qty": 20, "orderpoint_max_qty": 20},
        "hr_recruitment": {"enabled": True, "num_jobs": 0, "num_candidates": 0,
                           "create_skills": False, "num_skill_types": 0, "skills_per_type": 0},
        "hr": {"enabled": True, "count": 1,
               "timeoff": {"enabled": True, "entries_per_employee": 1, "avg_length_days": 1,
                           "past_future_pct": 0, "timescale_days": 1, "validate_pct": 0}},
        "hr_expense": {"enabled": True, "count_per_employee": 0, "approved_pct": 0},
        "documents": {"enabled": True, "bill_pdfs": False, "cv_pdfs": True},
        "analytic": {"enabled": True, "sale_pct": 100, "purchase_pct": 0, "expense_pct": 100},
    },
}

_NETZ_A_CASES = (
    ("all_on", _PAYLOAD_ALL_ON),
    ("all_off", _PAYLOAD_ALL_OFF),
    ("master_mode", _PAYLOAD_MASTER),
    ("clamp_edges", _PAYLOAD_CLAMPS),
)


def _normalize_selections(sel: ModuleSelections) -> dict:
    """dataclasses.asdict + None -> {} + drop the four vanishing `enabled` keys.

    asdict works on ModuleSelections both before the refactor (plain dicts)
    and after (nested dataclasses), which is exactly why it is the only
    normalizer here — a second `vars()` branch would be written now, never
    executed, and then frozen.
    """
    raw = dataclasses.asdict(sel)
    out = {}
    for key, value in raw.items():
        if value is None:
            value = {}
        if key in _ENABLED_KEY_FIELDS and isinstance(value, dict):
            value = {k: v for k, v in value.items() if k != "enabled"}
        out[key] = value
    return out


def build_netz_a_snapshot() -> dict:
    snapshot = {}
    for name, payload in _NETZ_A_CASES:
        sel, selected = run_config.build_selections(copy.deepcopy(payload))
        snapshot[name] = {
            "selections": _normalize_selections(sel),
            # `selected` drives the progress rows and every gate in
            # estimate_record_counts — a golden over `sel` alone would leave
            # half of build_selections unobserved.
            "selected": sorted(selected),
        }
    return snapshot


# ===========================================================================
# Netz B — ModuleSelections -> recorded Odoo call sequence
# ===========================================================================

_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# fpdf2 stamps every document with a creation-time-derived /ID pair and a
# /CreationDate. Both land inside documents.py's base64 `raw` attachment
# payload, where the date regexes above cannot reach them — so two runs a
# second apart produce different bytes. Strip those two fields, then compare a
# hash of the remainder: the PDF's actual content still has to match exactly,
# but the clock no longer does.
_PDF_ID_RE = re.compile(rb"/ID \[<[0-9A-Fa-f]*><[0-9A-Fa-f]*>\]")
_PDF_CREATIONDATE_RE = re.compile(rb"/CreationDate \(D:[^)]*\)")
_PDF_B64_PREFIX = "JVBERi"  # base64 of "%PDF"


def _pdf_fingerprint(b64_value: str) -> str:
    blob = base64.b64decode(b64_value)
    blob = _PDF_ID_RE.sub(b"/ID <SCRUBBED>", blob)
    blob = _PDF_CREATIONDATE_RE.sub(b"/CreationDate <SCRUBBED>", blob)
    return f"<PDF len={len(blob)} sha256={hashlib.sha256(blob).hexdigest()[:32]}>"


def _scrub(value, key=None):
    """Recursively replace date/datetime literals (and PDF payloads) so the
    golden is stable across runs and days. Applied to the LIVE side at compare
    time as well as at generation time, so a golden is never rewritten by a
    re-run."""
    if isinstance(value, str):
        if key == "raw" and value.startswith(_PDF_B64_PREFIX):
            return _pdf_fingerprint(value)
        return _DATE_RE.sub("<DATE>", _DATETIME_RE.sub("<DATETIME>", value))
    if isinstance(value, dict):
        return {k: _scrub(v, key=k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


class _StubLLM:
    """Deterministic stand-in for LLMService.

    `fetch_recruiting_data` returns real content on purpose: with None,
    recruiting.py falls back to generated placeholders and `create_skills`,
    `num_skill_types` and `skills_per_type` would never influence a single
    recorded call — three config values of the very field this net is meant
    to protect.

    The four `-> Dict[...]`-typed methods return `{}`, which is their real
    empty-result contract (llm_service.py returns `{}`, never None, from all
    four) — the modules then take their deterministic fallback path, driven
    by the config counts this net exists to watch. Note that `{}` is not
    interchangeable with None here: `mrp.py:207` does
    `bom_components_map.get(...)` with no `or {}` guard and raises
    AttributeError on None. That is a pre-existing Pattern-2 gap in
    production code, out of scope for S17 (null behaviour change) and
    deliberately not worked around by pretending it doesn't exist.

    Anything else returns None — the modules' Pattern-2 fallback path.
    """

    total_calls = 0
    total_tokens = 0

    def fetch_all_bom_components(self, products, industry, language="German"):
        return {}

    def fetch_workcenter_data(self, industry, language, num_workcenters):
        return {}

    def fetch_job_summaries_batch(self, job_titles, industry, language="German"):
        return {}

    def fetch_cv_bullet_points_batch(self, applicants, industry, language="German"):
        return {}

    def fetch_recruiting_data(self, industry, num_jobs, num_candidates,
                              num_skill_types, skills_per_type, language="German"):
        return {
            "job_titles": [f"Rolle {i}" for i in range(1, 13)],
            "candidate_names": [f"Person {i}" for i in range(1, 31)],
            "skill_types": [
                {"name": f"Kompetenzart {i}",
                 "skills": [f"Kompetenz {i}.{j}" for j in range(1, 9)],
                 "levels": ["Grundlagen", "Fortgeschritten", "Experte"]}
                for i in range(1, 9)
            ],
        }

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _recording_client(search_read_fn=None):
    """MagicMock-backed fake that records the full mutating-call sequence.

    create/create_batch hand back ascending ints because downstream code does
    real arithmetic and zip()ing with them. search_read is domain-aware per
    module (a fixed return list is too weak — mrp parses the ["id","in",[...]]
    clause, inventory distinguishes two different stock.warehouse lookups by
    domain shape). Both are copied from the modules' existing unit tests
    rather than invented here.
    """
    client = MagicMock()
    log = []
    counter = {"n": 1000}

    def _next_id():
        counter["n"] += 1
        return counter["n"]

    def _create(model, values, context=None):
        log.append(["create", model, [values], {"context": context}])
        return _next_id()

    def _create_batch(model, values_list, context=None):
        log.append(["create_batch", model, [values_list], {"context": context}])
        return [_next_id() for _ in values_list]

    def _write(model, ids, values, context=None):
        log.append(["write", model, [ids, values], {"context": context}])
        return True

    def _call_method(model, method, ids=None, kwargs=None, context=None):
        log.append(["call_method", model, [method], {"ids": ids, "kwargs": kwargs}])
        return True

    def _search_read(model, domain=None, fields=None, limit=None, **kw):
        log.append(["search_read", model, [domain], {"fields": fields, "limit": limit}])
        return search_read_fn(model, domain, fields, limit) if search_read_fn else []

    client.create.side_effect = _create
    client.create_batch.side_effect = _create_batch
    client.write.side_effect = _write
    client.call_method.side_effect = _call_method
    client.search_read.side_effect = _search_read
    return client, log


def _domain_ids(domain, field="id", operator="in"):
    for clause in domain or []:
        if isinstance(clause, (list, tuple)) and len(clause) == 3:
            if clause[0] == field and clause[1] == operator:
                return clause[2]
    return []


def _has_field(domain, field):
    return any(isinstance(c, (list, tuple)) and c and c[0] == field for c in (domain or []))


def _ctx(selections, **overrides):
    criteria = DemoCriteria(
        mode="both", industry="IT", num_companies=2,
        num_delivery_contacts=1, num_invoice_contacts=1, num_other_contacts=1,
        num_services=2, num_consumables=2, num_storables=3,
    )
    ctx = RunContext(
        criteria=criteria, module_selections=selections, industry="IT",
        language_name="German", language_code="de",
    )
    for key, value in overrides.items():
        # RunContext is a dataclass without __slots__, so a stale fixture name
        # would silently create a dead attribute and leave the real field
        # empty — the module then skips and the golden reddens as if
        # production were broken. Fail on the actual cause instead.
        assert hasattr(ctx, key), f"RunContext hat kein Feld '{key}' — Fixture veraltet?"
        setattr(ctx, key, value)
    return ctx


# --- mrp ------------------------------------------------------------------

_MRP_SEL = {
    "num_products": 3, "components_per_bom": 4, "sub_boms_per_product": 2,
    "num_workcenters": 2, "num_manufacturing_orders": 4,
    "create_quality_points": True, "quality_fail_pct": 40,
}


def _search_mrp(model, domain, fields, limit):
    if model == 'product.product':
        # get_product_template_ids_bulk: one row per requested id.
        return [{"id": pid, "product_tmpl_id": [pid + 500, "tmpl"]}
                for pid in _domain_ids(domain)]
    if model == 'res.company':
        return [{"id": 1}]
    if model == 'stock.picking.type':
        return [{"id": 77}]
    if model == 'quality.alert.team':
        return [{"id": 9}]
    if model == 'quality.point.test_type':
        return [{"id": 8}]
    return []


def _case_mrp():
    client, log = _recording_client(_search_mrp)
    ctx = _ctx(ModuleSelections(mrp=MrpConfig(**_MRP_SEL)),
               feature_flags={"mrp_routings": True, "quality": True},
               name_banks={"product_names": [f"Produkt {i}" for i in range(1, 9)]},
               installed_modules={"mrp", "stock"})
    mrp.create_mrp_data(client, _StubLLM(), ctx)
    return log


# --- stock / inventory ----------------------------------------------------

_STOCK_SEL = {
    "avg_qty": 30, "sub_locations": 2, "second_warehouse": True,
    "tracking_lot_pct": 40, "tracking_serial_pct": 20, "tracking_serial_max": 3,
    "orderpoints_pct": 50, "orderpoint_min_qty": 7, "orderpoint_max_qty": 19,
}


def _search_inventory(model, domain, fields, limit):
    if model == 'res.company':
        return [{"id": 1}]
    if model == 'stock.warehouse':
        # get_default_warehouse filters on company_id, create_second_warehouse's
        # read-back filters on id — told apart by domain shape (as the module's
        # own unit test does).
        if _has_field(domain, "id"):
            return [{"lot_stock_id": [88, "WH2/Stock"]}]
        return [{"lot_stock_id": [1, "WH/Stock"], "in_type_id": [2, "WH/IN"]}]
    if model == 'product.product':
        tracking = ["lot", "serial", "none", "lot", "none"]
        return [{"id": pid, "tracking": tracking[i % len(tracking)]}
                for i, pid in enumerate(_domain_ids(domain))]
    if model == 'stock.location':
        return []
    return []


def _case_inventory():
    client, log = _recording_client(_search_inventory)
    ctx = _ctx(ModuleSelections(stock=StockConfig(**_STOCK_SEL)),
               partner_company_ids=[10, 11],
               product_ids=[1, 2, 3],
               component_ids=[4, 5],
               new_product_ids=[1, 2, 3],
               installed_modules={"stock"})
    inventory.create_inventory_data(client, _StubLLM(), ctx)
    return log


# --- documents ------------------------------------------------------------

def _search_documents(model, domain, fields, limit):
    if model == 'account.move':
        return [{"id": bid, "name": f"BILL/{bid}", "ref": f"REF-{bid}",
                 "invoice_date": "2026-01-15", "invoice_date_due": "2026-02-15",
                 "currency_id": [3, "EUR"], "amount_untaxed": 100.0 + bid,
                 "amount_tax": 19.0, "amount_total": 119.0 + bid,
                 "partner_id": [700 + bid, f"Lieferant {bid}"],
                 "invoice_line_ids": [900 + bid]}
                for bid in _domain_ids(domain)]
    if model == 'res.partner':
        return [{"id": pid, "name": f"Lieferant {pid}", "street": "Hauptstr. 1",
                 "zip": "10115", "city": "Berlin"} for pid in _domain_ids(domain)]
    if model == 'account.move.line':
        return [{"id": lid, "name": f"Position {lid}", "quantity": 2.0,
                 "price_unit": 50.0, "price_subtotal": 100.0, "price_total": 119.0,
                 "product_id": [11, "Bauteil"], "product_uom_id": [1, "Einheiten"],
                 "tax_ids": [5]} for lid in _domain_ids(domain)]
    if model == 'account.tax':
        return [{"id": tid, "amount": 19.0} for tid in _domain_ids(domain)]
    if model == 'res.company':
        return [{"id": 1, "name": "Demo GmbH", "street": "Werkstr. 5",
                 "zip": "80331", "city": "München", "partner_id": [2, "Demo GmbH"]}]
    if model == 'hr.applicant':
        return [{"id": aid, "partner_name": f"Bewerber {aid}",
                 "email_from": f"b{aid}@example.com", "partner_phone": "+49 30 123456",
                 "applicant_skill_ids": [aid * 10]} for aid in _domain_ids(domain)]
    if model == 'hr.applicant.skill':
        return [{"id": sid, "skill_id": [sid, f"Kompetenz {sid}"]}
                for sid in _domain_ids(domain)]
    return []


def _case_documents():
    client, log = _recording_client(_search_documents)
    ctx = _ctx(ModuleSelections(documents=DocumentsConfig(bill_pdfs_enabled=True,
                                                          cv_pdfs_enabled=True)),
               bill_ids=[201, 202],
               applicant_ids=[301, 302],
               installed_modules={"hr_recruitment", "hr_recruitment_skills"},
               model_access={"ir.attachment": True})
    documents.create_documents(client, _StubLLM(), ctx)
    return log


# --- hr_recruitment -------------------------------------------------------

_RECRUIT_SEL = {
    "num_jobs": 3, "num_candidates": 5, "create_skills": True,
    "num_skill_types": 2, "skills_per_type": 3,
}


def _search_recruiting(model, domain, fields, limit):
    if model == 'hr.skill.type':
        return []
    if model == 'hr.skill':
        if _has_field(domain, "id"):
            return [{"id": sid, "skill_type_id": [1001, "Kompetenzart 1"]}
                    for sid in _domain_ids(domain)]
        return []
    if model == 'hr.skill.level':
        return [{"id": 1, "level_progress": 33, "skill_type_id": [1001, "Kompetenzart 1"]},
                {"id": 2, "level_progress": 66, "skill_type_id": [1001, "Kompetenzart 1"]},
                {"id": 3, "level_progress": 100, "skill_type_id": [1001, "Kompetenzart 1"]}]
    if model == 'hr.department':
        return [{"id": 1, "name": "Allgemein"}]
    if model == 'hr.recruitment.stage':
        return [{"id": 1, "name": "Neu", "sequence": 1},
                {"id": 2, "name": "Interview", "sequence": 2}]
    if model == 'hr.job':
        return []
    return []


def _case_recruiting():
    client, log = _recording_client(_search_recruiting)
    ctx = _ctx(ModuleSelections(hr_recruitment=RecruitmentConfig(**_RECRUIT_SEL)),
               installed_modules={"hr_recruitment", "hr_recruitment_skills"})
    recruiting.create_recruiting_data(client, _StubLLM(), ctx)
    return log


# --- analytic reader 1: sale ---------------------------------------------

def _search_sale(model, domain, fields, limit):
    if model == 'product.product':
        return [{"id": pid} for pid in (10, 11, 12)]
    if model == 'crm.lead':
        return [{"id": 401, "partner_id": [1, "Kunde 1"]},
                {"id": 402, "partner_id": [2, "Kunde 2"]}]
    if model == 'sale.order':
        if _has_field(domain, "state"):
            return [{"id": oid, "name": f"SO{oid}"} for oid in _domain_ids(domain)]
        return [{"id": oid, "opportunity_id": [401, "Chance"]} for oid in _domain_ids(domain)]
    if model == 'crm.stage':
        return [{"id": 5, "name": "Won"}, {"id": 4, "name": "Proposal"}]
    if model == 'sale.order.line':
        return [{"id": 600 + i} for i in range(1, 9)]
    return []


def _case_sale():
    client, log = _recording_client(_search_sale)
    ctx = _ctx(ModuleSelections(sale=6, sale_confirm_pct=50,
                                analytic=AnalyticConfig(sale_pct=60,
                                                        purchase_pct=40, expense_pct=30)),
               partner_company_ids=[1, 2],
               product_ids=[10, 11, 12],
               opportunity_ids=[401, 402],
               installed_modules={"sale", "crm"})
    sale.create_sale_data(client, _StubLLM(), ctx)
    return log


# --- analytic reader 2: purchase -----------------------------------------

def _search_purchase(model, domain, fields, limit):
    if model == 'res.company':
        if _has_field(domain, "id"):
            return [{"id": 1, "currency_id": [3, "EUR"]}]
        return [{"id": 1, "currency_id": [3, "EUR"]}]
    if model == 'stock.warehouse':
        return [{"lot_stock_id": [1, "WH/Stock"], "in_type_id": [2, "WH/IN"]}]
    if model == 'product.product':
        return [{"id": pid, "name": f"Bauteil {pid}", "standard_price": 10.0 + pid,
                 "list_price": 30.0 + pid} for pid in _domain_ids(domain)]
    if model == 'purchase.order':
        # _create_bills_from_pos reads back one PO with ["id","=",oid]; the
        # manual fallback reads many with ["id","in",[...]]. Both shapes must
        # yield rows, or the preferred path silently degrades to the fallback.
        ids = _domain_ids(domain)
        if not ids:
            single = _domain_ids(domain, operator="=")
            ids = [single] if isinstance(single, int) else list(single or [])
        return [{"id": oid, "invoice_ids": [oid + 5000], "partner_id": [50, "Lieferant"],
                 "order_line": [oid + 100], "name": f"PO{oid}"} for oid in ids]
    if model == 'purchase.order.line':
        return [{"id": lid, "product_id": [11, "Bauteil"], "product_qty": 3.0,
                 "price_unit": 12.0, "name": "Zeile"} for lid in _domain_ids(domain)]
    return []


def _case_purchase():
    client, log = _recording_client(_search_purchase)
    ctx = _ctx(ModuleSelections(purchase=4, purchase_confirm_pct=75,
                                analytic=AnalyticConfig(sale_pct=60,
                                                        purchase_pct=45, expense_pct=30)),
               partner_company_ids=[10],
               component_ids=[21, 22, 23],
               supplier_ids=[51, 52],
               installed_modules={"purchase"})
    purchase.create_purchase_data(client, _StubLLM(), ctx)
    return log


# --- analytic reader 3: expenses -----------------------------------------

def _search_expenses(model, domain, fields, limit):
    if model == 'product.product':
        return [{"id": 500, "name": "Reisekosten"}, {"id": 501, "name": "Bewirtung"}]
    if model == 'res.company':
        return [{"id": 10, "currency_id": [3, "EUR"]}]
    return []


def _case_expenses():
    client, log = _recording_client(_search_expenses)
    ctx = _ctx(ModuleSelections(hr_expense=ExpenseConfig(count_per_employee=3, approved_pct=60),
                                analytic=AnalyticConfig(sale_pct=60,
                                                        purchase_pct=40, expense_pct=50)),
               employee_ids=[71, 72, 73],
               installed_modules={"hr_expense"})
    expenses.create_expense_data(client, _StubLLM(), ctx)
    return log


_NETZ_B_CASES = (
    ("mrp", _case_mrp),
    ("stock", _case_inventory),
    ("documents", _case_documents),
    ("hr_recruitment", _case_recruiting),
    ("sale_analytic", _case_sale),
    ("purchase_analytic", _case_purchase),
    ("expenses_analytic", _case_expenses),
)


def build_netz_b_snapshot() -> dict:
    snapshot = {}
    for name, case_fn in _NETZ_B_CASES:
        random.seed(_SEED)
        snapshot[name] = _scrub(case_fn())
    return snapshot


# ===========================================================================
# Comparison
# ===========================================================================

def _jsonable(value):
    """Round-trip through JSON so tuples/int-keys compare equal to a golden
    that has already been through json.dump."""
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def _first_difference(actual, expected, path="") -> str:
    if type(actual) is not type(expected):
        return f"{path or '<root>'}: type {type(actual).__name__} != {type(expected).__name__}"
    if isinstance(expected, dict):
        for key in sorted(set(actual) | set(expected)):
            if key not in actual:
                return f"{path}.{key}: missing in actual"
            if key not in expected:
                return f"{path}.{key}: unexpected in actual ({actual[key]!r:.80})"
            if actual[key] != expected[key]:
                return _first_difference(actual[key], expected[key], f"{path}.{key}")
        return f"{path}: dicts differ but no key-level diff found"
    if isinstance(expected, list):
        if len(actual) != len(expected):
            return f"{path}: length {len(actual)} != {len(expected)}"
        for index, (a, e) in enumerate(zip(actual, expected)):
            if a != e:
                return _first_difference(a, e, f"{path}[{index}]")
        return f"{path}: lists differ but no element-level diff found"
    return f"{path or '<root>'}: {actual!r:.100} != {expected!r:.100}"


def _load_golden(path):
    if not os.path.exists(path):
        raise AssertionError(
            f"Golden fehlt: {path}. Diese Datei wird in WP1 einmal erzeugt und danach "
            f"NIE wieder angepasst (S17-D9).")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _compare(label, actual, golden_path, results):
    try:
        expected = _load_golden(golden_path)
        actual_json = _jsonable(actual)
        if actual_json == expected:
            results.append((label, True, f"{len(expected)} Fälle"))
        else:
            results.append((label, False, _first_difference(actual_json, expected)))
    except AssertionError as e:
        results.append((label, False, str(e)))
    except Exception as e:
        results.append((label, False, f"{type(e).__name__}: {e}"))


def run():
    results = []

    _compare("Netz A: build_selections -> Golden (10 dict-Felder, 4 Clamps, `selected`)",
             build_netz_a_snapshot(), _GOLDEN_A, results)

    netz_b = None
    try:
        netz_b = build_netz_b_snapshot()
        _compare("Netz B: Modul-Aufrufsequenz -> Golden (7 Module, Datum normalisiert)",
                 netz_b, _GOLDEN_B, results)
    except Exception as e:
        results.append(("Netz B: Modul-Aufrufsequenz -> Golden (7 Module, Datum normalisiert)",
                        False, f"{type(e).__name__}: {e}"))

    # A golden of "[]" would compare green while proving nothing: guard every
    # Netz-B case against the module having silently returned early. This is
    # exactly the failure mode the isinstance(..., dict) guards would cause
    # after D5 (plan §3a) — there the module returns and reports ok=True.
    try:
        assert netz_b is not None, "Netz B konnte nicht aufgezeichnet werden"
        thin = []
        for name, calls in netz_b.items():
            writes = [c for c in calls if c[0] in ("create", "create_batch", "write")]
            if len(writes) < 2:
                thin.append(f"{name}={len(writes)}")
        assert not thin, f"Modul(e) ohne echte Schreibaufrufe: {', '.join(thin)}"
        results.append((
            "Netz B: jedes Modul erzeugt echte Schreibaufrufe (kein leeres Golden)",
            True, f"{len(netz_b)} Module"))
    except AssertionError as e:
        results.append((
            "Netz B: jedes Modul erzeugt echte Schreibaufrufe (kein leeres Golden)",
            False, str(e)))

    # The net is only worth anything if it is reproducible in-process too —
    # a case that depends on dict ordering or an un-reseeded RNG would drift.
    try:
        first = _jsonable(build_netz_a_snapshot())
        second = _jsonable(build_netz_a_snapshot())
        assert first == second, _first_difference(second, first)
        b_first = _jsonable(build_netz_b_snapshot())
        b_second = _jsonable(build_netz_b_snapshot())
        assert b_first == b_second, _first_difference(b_second, b_first)
        results.append(("Netz A+B: zwei Läufe im selben Prozess sind identisch", True, ""))
    except AssertionError as e:
        results.append(("Netz A+B: zwei Läufe im selben Prozess sind identisch", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
