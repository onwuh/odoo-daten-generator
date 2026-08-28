"""Unit tests for modules/crm.py — D3 batch-creation call-count guard."""
import os
import sys
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DemoCriteria, ModuleSelections, RunContext
from modules import crm


def _make_ctx(num_opps=0, num_leads=0, company_ids=None):
    criteria = DemoCriteria(
        mode="both", industry="IT", num_companies=1,
        num_delivery_contacts=0, num_invoice_contacts=0, num_other_contacts=0,
        num_services=1, num_consumables=0, num_storables=0,
    )
    ctx = RunContext(
        criteria=criteria,
        module_selections=ModuleSelections(crm=num_opps, leads=num_leads),
        industry="IT", language_name="German", language_code="de", gemini_model_name="test",
    )
    ctx.company_ids = company_ids if company_ids is not None else [1, 2, 3]
    return ctx


def _mock_client(stages=None):
    client = MagicMock()
    client.search_read.side_effect = lambda model, *a, **kw: (
        stages or [] if model == "crm.stage" else []
    )
    counter = {"n": 500}

    def _create_batch(model, values_list, context=None):
        ids = []
        for _ in values_list:
            counter["n"] += 1
            ids.append(counter["n"])
        return ids

    client.create_batch.side_effect = _create_batch
    return client


def run():
    results = []

    # ------------------------------------------------------------------
    # D3: opportunities created via exactly 1 create_batch call, not N create()s
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_opps=7)
        crm.create_crm_data(client, gemini=MagicMock(), ctx=ctx)
        assert client.create_batch.call_count == 1, client.create_batch.call_count
        assert client.create.call_count == 0, "fell back to per-record create()"
        assert len(ctx.opportunity_ids) == 7, ctx.opportunity_ids
        results.append((
            "create_crm_data: opportunities via exactly 1 create_batch call",
            True, f"create_batch calls={client.create_batch.call_count}, ids={len(ctx.opportunity_ids)}",
        ))
    except AssertionError as e:
        results.append(("create_crm_data: opportunities via exactly 1 create_batch call", False, str(e)))

    # ------------------------------------------------------------------
    # D3: leads created via exactly 1 create_batch call
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_opps=0, num_leads=4)
        crm.create_crm_data(client, gemini=MagicMock(), ctx=ctx)
        assert client.create_batch.call_count == 1, client.create_batch.call_count
        assert len(ctx.lead_ids) == 4, ctx.lead_ids
        results.append((
            "create_crm_data: leads via exactly 1 create_batch call",
            True, f"create_batch calls={client.create_batch.call_count}, ids={len(ctx.lead_ids)}",
        ))
    except AssertionError as e:
        results.append(("create_crm_data: leads via exactly 1 create_batch call", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 3 / Pattern 5: no company_ids -> skip gracefully, no create_batch call
    # ------------------------------------------------------------------
    try:
        client = _mock_client()
        ctx = _make_ctx(num_opps=5, company_ids=[])
        crm.create_crm_data(client, gemini=MagicMock(), ctx=ctx)
        client.create_batch.assert_not_called()
        assert ctx.opportunity_ids == []
        results.append(("create_crm_data: empty company_ids -> no create_batch call (Pattern 5)", True, ""))
    except AssertionError as e:
        results.append(("create_crm_data: empty company_ids -> no create_batch call (Pattern 5)", False, str(e)))

    # ------------------------------------------------------------------
    # Consent gate: the chatter prompt is the ONE place a value read out of the
    # target database reaches an LLM. Without consent it must carry placeholders.
    # ------------------------------------------------------------------
    for label, use_db_names, expect_real in [("ohne Zustimmung", False, False),
                                             ("mit Zustimmung", True, True)]:
        try:
            captured = {}

            class _LLM:
                def fetch_crm_chatter_messages(self, opportunities, industry, language,
                                               style=None, messages_per_opp=None):
                    captured["opps"] = opportunities
                    return {}

            ctx = _make_ctx(num_opps=1, num_leads=0)
            ctx.module_selections.crm_chatter = {
                "enabled": True, "style": "mixed", "messages_per_opp": 2,
                "use_db_names": use_db_names,
            }
            opp_data = [{
                "id": 1, "name": "Angebot A",
                "partner_name": "Echte Kunden GmbH",
                "salesperson": {"name": "Echter Mitarbeiter"},
            }]
            crm._post_chatter_messages(MagicMock(), _LLM(), ctx, opp_data)

            sent = captured.get("opps", [{}])[0]
            if expect_real:
                assert sent.get("customer") == "Echte Kunden GmbH", sent
                assert sent.get("salesperson") == "Echter Mitarbeiter", sent
            else:
                assert sent.get("customer") == "Kunde", sent
                assert sent.get("salesperson") == "Verkäufer", sent
                blob = str(sent)
                assert "Echte Kunden GmbH" not in blob, blob
                assert "Echter Mitarbeiter" not in blob, blob
            # The opportunity title itself is LLM-generated, never DB-read, so it
            # is unaffected either way.
            assert sent.get("title") == "Angebot A", sent
            results.append((f"chatter-Prompt {label}: Namen korrekt gefiltert", True, str(sent.get("customer"))))
        except Exception as e:
            results.append((f"chatter-Prompt {label}: Namen korrekt gefiltert", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
