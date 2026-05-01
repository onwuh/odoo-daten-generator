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
        from config import ModuleSelections, RunContext, DemoCriteria
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
        # Empty dict = disabled
        sel = ModuleSelections(crm_chatter={})
        mock_ctx = RunContext(
            criteria=criteria, module_selections=sel,
            industry="Test", language_name="German", language_code="de_DE",
            gemini_model_name="test",
        )

        opp_data = [{"id": 1, "name": "Test Opp", "partner_id": 10,
                     "partner_name": "Test GmbH", "salesperson": None}]
        _post_chatter_messages(mock_client, mock_gemini, mock_ctx, opp_data)

        mock_client.call_method.assert_not_called()
        results.append(("crm: chatter disabled (empty dict) → no message_post", True, "call_method not called"))
    except Exception as e:
        results.append(("crm: chatter disabled (empty dict) → no message_post", False, str(e)))

    all_passed = all(ok for _, ok, _ in results)
    return all_passed, results
