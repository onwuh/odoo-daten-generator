"""Unit tests for modules/crm.py — D3 batch-creation call-count guard."""
import os
import random
import sys
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import ChatterConfig, DemoCriteria, LostConfig, ModuleSelections, RunContext
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
        industry="IT", language_name="German", language_code="de",
    )
    ctx.partner_company_ids = company_ids if company_ids is not None else [1, 2, 3]
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
        crm.create_crm_data(client, llm=MagicMock(), ctx=ctx)
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
        crm.create_crm_data(client, llm=MagicMock(), ctx=ctx)
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
        crm.create_crm_data(client, llm=MagicMock(), ctx=ctx)
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
            ctx.module_selections.crm_chatter = ChatterConfig(
                style="mixed", messages_per_opp=2, use_db_names=use_db_names,
            )
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

    # ------------------------------------------------------------------
    # R11: mark_lost_opportunities — Pattern 3 (no crm_lost selection -> no calls)
    # ------------------------------------------------------------------
    try:
        client = MagicMock()
        ctx = _make_ctx()
        ctx.opportunity_ids = [1, 2, 3]
        crm.mark_lost_opportunities(client, llm=None, ctx=ctx)
        client.search_read.assert_not_called()
        client.write.assert_not_called()
        results.append(("mark_lost_opportunities: crm_lost=None -> no calls (Pattern 3)", True, ""))
    except AssertionError as e:
        results.append(("mark_lost_opportunities: crm_lost=None -> no calls (Pattern 3)", False, str(e)))

    # ------------------------------------------------------------------
    # R11: pct=0 -> no calls at all.
    # ------------------------------------------------------------------
    try:
        client = MagicMock()
        ctx = _make_ctx()
        ctx.opportunity_ids = [1, 2, 3]
        ctx.module_selections.crm_lost = LostConfig(pct=0)
        crm.mark_lost_opportunities(client, llm=None, ctx=ctx)
        client.search_read.assert_not_called()
        results.append(("mark_lost_opportunities: pct=0 -> no calls", True, ""))
    except AssertionError as e:
        results.append(("mark_lost_opportunities: pct=0 -> no calls", False, str(e)))

    # ------------------------------------------------------------------
    # R11: Pattern 5 — empty opportunity_ids -> no calls, graceful skip.
    # Covers "CRM installed but not selected" too (opportunity_ids stays
    # empty in that case, since create_crm_data never ran).
    # ------------------------------------------------------------------
    try:
        client = MagicMock()
        ctx = _make_ctx()
        ctx.opportunity_ids = []
        ctx.module_selections.crm_lost = LostConfig(pct=50)
        crm.mark_lost_opportunities(client, llm=None, ctx=ctx)
        client.search_read.assert_not_called()
        results.append(("mark_lost_opportunities: empty opportunity_ids -> no calls (Pattern 5)", True, ""))
    except AssertionError as e:
        results.append(("mark_lost_opportunities: empty opportunity_ids -> no calls (Pattern 5)", False, str(e)))

    # ------------------------------------------------------------------
    # R11: only unlinked opportunities are candidates — a linked one must
    # never be written, even with pct=100.
    # ------------------------------------------------------------------
    try:
        client = MagicMock()
        client.search_read.side_effect = lambda model, *a, **kw: (
            [{"id": 900}] if model == "crm.lost.reason" else []
        )
        ctx = _make_ctx()
        ctx.opportunity_ids = [1, 2, 3, 4]
        ctx.linked_opportunity_ids = [1, 2]
        ctx.module_selections.crm_lost = LostConfig(pct=100)
        crm.mark_lost_opportunities(client, llm=None, ctx=ctx)
        written_ids = set()
        for call in client.write.call_args_list:
            written_ids.update(call.args[1])
        assert written_ids == {3, 4}, written_ids
        results.append(("mark_lost_opportunities: only unlinked opportunities are ever written", True, f"{written_ids}"))
    except AssertionError as e:
        results.append(("mark_lost_opportunities: only unlinked opportunities are ever written", False, str(e)))

    # ------------------------------------------------------------------
    # R11: Pattern 1 — empty crm.lost.reason pool -> no writes, graceful skip.
    # ------------------------------------------------------------------
    try:
        client = MagicMock()
        client.search_read.side_effect = lambda model, *a, **kw: []
        ctx = _make_ctx()
        ctx.opportunity_ids = [1, 2, 3]
        ctx.module_selections.crm_lost = LostConfig(pct=100)
        crm.mark_lost_opportunities(client, llm=None, ctx=ctx)
        client.write.assert_not_called()
        results.append(("mark_lost_opportunities: empty crm.lost.reason pool -> no writes (Pattern 1)", True, ""))
    except AssertionError as e:
        results.append(("mark_lost_opportunities: empty crm.lost.reason pool -> no writes (Pattern 1)", False, str(e)))

    # ------------------------------------------------------------------
    # R11: the write payload never sets won_status directly (it's a compute
    # field) — only active/probability/lost_reason_id, matching S12/WP3's
    # live-verified finding that plain write() is sufficient.
    # ------------------------------------------------------------------
    try:
        client = MagicMock()
        client.search_read.side_effect = lambda model, *a, **kw: (
            [{"id": 900}] if model == "crm.lost.reason" else []
        )
        ctx = _make_ctx()
        ctx.opportunity_ids = [1, 2]
        ctx.module_selections.crm_lost = LostConfig(pct=100)
        crm.mark_lost_opportunities(client, llm=None, ctx=ctx)
        assert client.write.call_count >= 1
        for call in client.write.call_args_list:
            vals = call.args[2]
            assert set(vals.keys()) == {"active", "probability", "lost_reason_id"}, vals
            assert vals["active"] is False, vals
        results.append(("mark_lost_opportunities: write payload is active/probability/lost_reason_id only", True, ""))
    except AssertionError as e:
        results.append(("mark_lost_opportunities: write payload is active/probability/lost_reason_id only", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 7: pct distribution over n=200 unlinked opportunities, seeded.
    # ------------------------------------------------------------------
    try:
        random.seed(7)
        client = MagicMock()
        client.search_read.side_effect = lambda model, *a, **kw: (
            [{"id": 900}, {"id": 901}] if model == "crm.lost.reason" else []
        )
        ctx = _make_ctx()
        ctx.opportunity_ids = list(range(200))
        ctx.module_selections.crm_lost = LostConfig(pct=30)
        crm.mark_lost_opportunities(client, llm=None, ctx=ctx)
        written_ids = set()
        for call in client.write.call_args_list:
            written_ids.update(call.args[1])
        assert 45 <= len(written_ids) <= 90, f"len={len(written_ids)} far from expected ~30% of 200"
        results.append(("Pattern 7: crm_lost pct=30 over n=200 lands near 30%", True, f"{len(written_ids)}/200"))
    except AssertionError as e:
        results.append(("Pattern 7: crm_lost pct=30 over n=200 lands near 30%", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
