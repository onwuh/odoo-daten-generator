import sys
import os
import datetime
import random

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from modules.crm import (
    create_opportunity, create_lead,
    _get_crm_lead_model_id, _get_activity_types, _extra_vals,
    _activity_deadline,
)


def run(client, ctx):
    """
    Consumes: ctx.partner_ids, ctx.feature_flags
    Produces: ctx.opportunity_ids, ctx.lead_ids
    Returns: (all_passed, [(label, ok, detail), ...])
    """
    results = []

    if not ctx.partner_ids:
        results.append(("crm: SKIP — no partner_ids in ctx", False, "master_data must run first"))
        return False, results

    partner_id = ctx.partner_ids[0]
    opp_id = None

    # Step 1 — Create opportunity with extra fields
    try:
        ev = _extra_vals()
        opp_id = create_opportunity(client, partner_id, "Integration Test Opportunity", ev)
        assert isinstance(opp_id, int) and opp_id > 0
        ctx.opportunity_ids.append(opp_id)
        results.append(("crm: create opportunity", True, opp_id))
    except Exception as e:
        results.append(("crm: create opportunity", False, str(e)))

    # Step 2 — Read back partner_id, date_deadline, expected_revenue
    try:
        assert opp_id, "No opportunity created in step 1"
        rec = client.search_read(
            'crm.lead',
            [["id", "=", opp_id]],
            fields=["partner_id", "expected_revenue", "date_deadline", "type"],
            limit=1,
        )
        assert rec, "Record not found"
        pid = rec[0]["partner_id"]
        pid = pid[0] if isinstance(pid, (list, tuple)) else pid
        assert pid == partner_id, f"partner_id mismatch: {pid} != {partner_id}"
        assert rec[0].get("expected_revenue") is not None, "expected_revenue missing"
        assert rec[0].get("date_deadline"), "date_deadline missing"
        assert rec[0].get("type") == "opportunity", "type should be opportunity"
        results.append(("crm: read-back fields (partner, revenue, deadline, type)", True, opp_id))
    except Exception as e:
        results.append(("crm: read-back fields (partner, revenue, deadline, type)", False, str(e)))

    # Step 3a — Post internal note (classic chatter)
    try:
        assert opp_id, "No opportunity created in step 1"
        client.call_method(
            'crm.lead', 'message_post',
            ids=[opp_id],
            kwargs={'body': 'Integration test internal note.', 'message_type': 'comment',
                    'subtype_xmlid': 'mail.mt_note'},
        )
        results.append(("crm: message_post (internal note)", True, opp_id))
    except Exception as e:
        results.append(("crm: message_post (internal note)", False, str(e)))

    # Step 3b — Post email-type chatter message
    try:
        assert opp_id, "No opportunity created in step 1"
        client.call_method(
            'crm.lead', 'message_post',
            ids=[opp_id],
            kwargs={'body': 'Sehr geehrte Damen und Herren,\n\nIntegration test email message.\n\nMit freundlichen Grüßen',
                    'message_type': 'email'},
        )
        results.append(("crm: message_post (email type)", True, opp_id))
    except Exception as e:
        results.append(("crm: message_post (email type)", False, str(e)))

    # Step 4 — Create mail.activity
    try:
        assert opp_id, "No opportunity created in step 1"
        model_id = _get_crm_lead_model_id(client)
        assert model_id, "crm.lead model ID not found"
        act_types = _get_activity_types(client)
        assert act_types, "No mail.activity.type found"
        deadline = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
        act_id = client.create('mail.activity', {
            'res_id': opp_id,
            'res_model_id': model_id,
            'activity_type_id': act_types[0]['id'],
            'date_deadline': deadline,
            'summary': 'Integration test activity',
        })
        assert isinstance(act_id, int) and act_id > 0
        results.append(("crm: create mail.activity", True, act_id))
    except Exception as e:
        results.append(("crm: create mail.activity", False, str(e)))

    # Step 5 — Lead creation (only if crm_leads feature flag active)
    if ctx.feature_flags.get('crm_leads'):
        try:
            lead_id = create_lead(client, partner_id, "Integration Test Lead", _extra_vals())
            assert isinstance(lead_id, int) and lead_id > 0
            ctx.lead_ids.append(lead_id)
            # Verify type=lead
            rec = client.search_read('crm.lead', [["id", "=", lead_id]], fields=["type"], limit=1)
            assert rec and rec[0].get("type") == "lead", f"Expected type=lead, got {rec}"
            results.append(("crm: create lead + verify type=lead", True, lead_id))
        except Exception as e:
            results.append(("crm: create lead + verify type=lead", False, str(e)))
    else:
        results.append(("crm: leads SKIP — crm_leads feature not active", True, "skipped"))

    # Step 6 — _activity_deadline distribution (unit, no network)
    try:
        random.seed(42)
        today = datetime.date.today().isoformat()
        dates = [_activity_deadline(50, 20) for _ in range(200)]
        past_dates   = [d for d in dates if d < today]
        today_dates  = [d for d in dates if d == today]
        future_dates = [d for d in dates if d > today]
        assert past_dates,   "Expected some past dates (past_pct=50)"
        assert today_dates,  "Expected some today dates (today_pct=20)"
        assert future_dates, "Expected some future dates (future_pct=30)"
        results.append((
            "crm: _activity_deadline distribution (past/today/future)",
            True,
            f"past={len(past_dates)}, today={len(today_dates)}, future={len(future_dates)}",
        ))
    except Exception as e:
        results.append(("crm: _activity_deadline distribution (past/today/future)", False, str(e)))

    # Step 7 — chatter disabled (empty dict): message_post must not be called
    try:
        from unittest.mock import MagicMock
        from config import (
            DemoCriteria,
            LostConfig,
            ModuleSelections,
            RunContext,
        )
        from modules.crm import _post_chatter_messages

        mock_client = MagicMock()
        mock_gemini = MagicMock()
        mock_gemini.fetch_crm_chatter_messages.return_value = {"Test Opp": [
            {"type": "note", "speaker": "salesperson", "body": "msg1"}
        ]}

        criteria = DemoCriteria(
            mode="both", industry="Test", num_companies=1,
            num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
            num_services=0, num_consumables=0, num_storables=0,
        )
        # crm_chatter=None = disabled
        sel = ModuleSelections(crm_chatter=None)
        mock_ctx = RunContext(
            criteria=criteria, module_selections=sel,
            industry="Test", language_name="German", language_code="de_DE",
            gemini_model_name="test",
        )

        opp_data = [{"id": 1, "name": "Test Opp", "partner_id": 10,
                     "partner_name": "Test GmbH", "salesperson": None}]
        _post_chatter_messages(mock_client, mock_gemini, mock_ctx, opp_data)

        mock_client.call_method.assert_not_called()
        results.append(("crm: chatter disabled (crm_chatter=None) → no message_post", True, "call_method not called"))
    except Exception as e:
        results.append(("crm: chatter disabled (crm_chatter=None) → no message_post", False, str(e)))

    # Step 8 — B12: salesperson assignment must not depend on crm_chatter flag
    try:
        from config import (
            DemoCriteria,
            LostConfig,
            ModuleSelections,
            RunContext,
        )
        from modules.crm import create_crm_data

        criteria = DemoCriteria(
            mode="both", industry="Test", num_companies=1,
            num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
            num_services=0, num_consumables=0, num_storables=0,
        )
        sel = ModuleSelections(crm=1, leads=0, crm_chatter=None)  # chatter disabled
        b12_ctx = RunContext(
            criteria=criteria, module_selections=sel,
            industry="Test", language_name="German", language_code="de_DE",
            gemini_model_name="test",
        )
        b12_ctx.partner_company_ids = [partner_id]
        b12_ctx.name_banks = {"opportunity_titles": ["B12 Test Opportunity"]}

        create_crm_data(client, None, b12_ctx)
        assert b12_ctx.opportunity_ids, "no opportunity created"
        rec = client.search_read(
            'crm.lead', [["id", "=", b12_ctx.opportunity_ids[0]]],
            fields=["user_id"], limit=1,
        )
        uid = rec[0]["user_id"] if rec else None
        uid = uid[0] if isinstance(uid, (list, tuple)) else uid
        assert uid, f"user_id not set on opportunity created with crm_chatter disabled: {rec}"
        results.append(("crm: user_id set with chatter disabled (B12)", True, f"user_id={uid}"))
    except Exception as e:
        results.append(("crm: user_id set with chatter disabled (B12)", False, str(e)))

    # Step 9 — R11: mark_lost_opportunities only touches unlinked opportunities.
    # opp_a simulates one sale.py already linked to an order (must stay
    # active); opp_b is unlinked (with pct=100, must end up lost).
    try:
        from config import (
            DemoCriteria,
            LostConfig,
            ModuleSelections,
            RunContext,
        )
        from modules.crm import mark_lost_opportunities

        opp_a = create_opportunity(client, partner_id, "R11 Test Opportunity Linked")
        opp_b = create_opportunity(client, partner_id, "R11 Test Opportunity Unlinked")
        assert isinstance(opp_a, int) and isinstance(opp_b, int)

        criteria = DemoCriteria(
            mode="both", industry="Test", num_companies=1,
            num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
            num_services=0, num_consumables=0, num_storables=0,
        )
        sel = ModuleSelections(crm_lost=LostConfig(pct=100))
        r11_ctx = RunContext(
            criteria=criteria, module_selections=sel,
            industry="Test", language_name="German", language_code="de_DE",
            gemini_model_name="test",
        )
        r11_ctx.opportunity_ids = [opp_a, opp_b]
        r11_ctx.linked_opportunity_ids = [opp_a]

        mark_lost_opportunities(client, None, r11_ctx)

        rec = client.search_read(
            'crm.lead', [["id", "in", [opp_a, opp_b]]],
            fields=["active", "won_status"], limit=0,
            context={"active_test": False},
        )
        by_id = {r["id"]: r for r in rec}
        assert by_id[opp_a]["active"] is True, f"linked opportunity was marked lost: {by_id[opp_a]}"
        assert by_id[opp_b]["active"] is False, f"unlinked opportunity was NOT marked lost: {by_id[opp_b]}"
        assert by_id[opp_b]["won_status"] == "lost", by_id[opp_b]
        results.append((
            "crm: R11 — mark_lost_opportunities only touches unlinked opportunities (Pattern 4)",
            True, f"linked={by_id[opp_a]}, unlinked={by_id[opp_b]}",
        ))
    except Exception as e:
        results.append(("crm: R11 — mark_lost_opportunities only touches unlinked opportunities (Pattern 4)", False, str(e)))

    # Step 10 — R11 Pattern 5: empty opportunity_ids -> graceful skip, no writes.
    try:
        from config import (
            DemoCriteria,
            LostConfig,
            ModuleSelections,
            RunContext,
        )
        from modules.crm import mark_lost_opportunities

        criteria = DemoCriteria(
            mode="both", industry="Test", num_companies=1,
            num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
            num_services=0, num_consumables=0, num_storables=0,
        )
        sel = ModuleSelections(crm_lost=LostConfig(pct=100))
        skip_ctx = RunContext(
            criteria=criteria, module_selections=sel,
            industry="Test", language_name="German", language_code="de_DE",
            gemini_model_name="test",
        )
        skip_ctx.opportunity_ids = []
        mark_lost_opportunities(client, None, skip_ctx)  # must not raise
        results.append(("crm: R11 — empty opportunity_ids -> graceful skip (Pattern 5)", True, ""))
    except Exception as e:
        results.append(("crm: R11 — empty opportunity_ids -> graceful skip (Pattern 5)", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
