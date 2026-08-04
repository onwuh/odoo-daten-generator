"""Unit tests for modules/master_data.py — D3 batch-creation call-count guard."""
import os
import sys
from unittest.mock import MagicMock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DemoCriteria, ModuleSelections, RunContext
from modules import master_data


def _make_ctx(num_companies=3):
    criteria = DemoCriteria(
        mode="both", industry="IT", num_companies=num_companies,
        num_delivery_contacts=1, num_invoice_contacts=1, num_other_contacts=1,
        num_services=1, num_consumables=0, num_storables=0,
    )
    return RunContext(
        criteria=criteria, module_selections=ModuleSelections(), industry="IT",
        language_name="German", language_code="de", gemini_model_name="test",
    )


def _mock_client_for_batches():
    """create_batch returns sequential fake ids matching len(values_list)."""
    client = MagicMock()
    counter = {"n": 1000}

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
    # D3: _create_partners issues exactly 2 create_batch calls (companies,
    # then contacts) regardless of num_companies — not N+1 individual creates.
    # ------------------------------------------------------------------
    try:
        client = _mock_client_for_batches()
        ctx = _make_ctx(num_companies=5)
        master_data._create_partners(client, ctx, country_map={})
        assert client.create_batch.call_count == 2, client.create_batch.call_count
        assert client.create.call_count == 0, "fell back to per-record create()"
        assert len(ctx.company_ids) == 5, ctx.company_ids
        results.append((
            "_create_partners: exactly 2 create_batch calls (companies, contacts)",
            True, f"create_batch calls={client.create_batch.call_count}",
        ))
    except AssertionError as e:
        results.append(("_create_partners: exactly 2 create_batch calls (companies, contacts)", False, str(e)))

    # ------------------------------------------------------------------
    # Pattern 1: num_companies=0 -> no batch calls made with non-empty payloads,
    # no crash, ctx.company_ids stays empty.
    # ------------------------------------------------------------------
    try:
        client = _mock_client_for_batches()
        ctx = _make_ctx(num_companies=0)
        master_data._create_partners(client, ctx, country_map={})
        assert ctx.company_ids == [], ctx.company_ids
        # create_batch may still be invoked with an empty list (client-level Pattern-1
        # guard already handles that); what matters is nothing crashes and no ids appear.
        for call in client.create_batch.call_args_list:
            values_list = call.args[1] if len(call.args) > 1 else call.kwargs.get("values_list")
            assert values_list == [], f"non-empty batch issued for 0 companies: {values_list}"
        results.append(("_create_partners: num_companies=0 -> no crash, empty ids", True, ""))
    except AssertionError as e:
        results.append(("_create_partners: num_companies=0 -> no crash, empty ids", False, str(e)))
    except Exception as e:
        results.append(("_create_partners: num_companies=0 -> no crash, empty ids", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
