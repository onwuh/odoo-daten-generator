"""Unit tests for modules/master_data.py — D3 batch-creation call-count guard."""
import os
import sys
from unittest.mock import MagicMock, patch

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

    # ------------------------------------------------------------------
    # R8: _create_products tags every service product with service_tracking/
    # invoice_policy/service_type when project+hr_timesheet are installed.
    # ------------------------------------------------------------------
    try:
        client = _mock_client_for_batches()
        ctx = _make_ctx()
        ctx.installed_modules = {"project", "hr_timesheet"}
        atoms = {"product_names": {"services": ["Beratung"], "consumables": [], "storables": []}}
        master_data._create_products(client, atoms, ctx)
        assert client.create_batch.call_count == 1, client.create_batch.call_count
        vals_list = client.create_batch.call_args_list[0].args[1]
        assert len(vals_list) == 1, vals_list
        vals = vals_list[0]
        assert vals.get("service_tracking") == "task_in_project", vals
        assert vals.get("invoice_policy") == "delivery", vals
        assert vals.get("service_type") == "timesheet", vals
        results.append((
            "R8: _create_products tags service product when project+hr_timesheet installed",
            True, f"vals={vals}",
        ))
    except AssertionError as e:
        results.append(("R8: _create_products tags service product when project+hr_timesheet installed", False, str(e)))

    # ------------------------------------------------------------------
    # R8 Pattern 3: gate is off when either app is missing from
    # installed_modules — existing (pre-R8) vals shape unchanged.
    # ------------------------------------------------------------------
    try:
        for missing in ({"project"}, {"hr_timesheet"}, set()):
            client = _mock_client_for_batches()
            ctx = _make_ctx()
            ctx.installed_modules = missing
            atoms = {"product_names": {"services": ["Beratung"], "consumables": [], "storables": []}}
            master_data._create_products(client, atoms, ctx)
            vals = client.create_batch.call_args_list[0].args[1][0]
            assert "service_tracking" not in vals, f"installed_modules={missing}: {vals}"
            assert "invoice_policy" not in vals, f"installed_modules={missing}: {vals}"
            assert "service_type" not in vals, f"installed_modules={missing}: {vals}"
        results.append((
            "R8 Pattern 3: no tagging when project or hr_timesheet not installed",
            True, "",
        ))
    except AssertionError as e:
        results.append(("R8 Pattern 3: no tagging when project or hr_timesheet not installed", False, str(e)))

    # ------------------------------------------------------------------
    # S13/R13: assign_tracking is called (avg_qty>0 AND 'stock' installed),
    # with the configured percentages, and its result reaches create_batch.
    # ------------------------------------------------------------------
    try:
        client = _mock_client_for_batches()
        ctx = _make_ctx()
        ctx.installed_modules = {"stock"}
        ctx.module_selections.stock = {
            "avg_qty": 10, "tracking_lot_pct": 20, "tracking_serial_pct": 5,
        }
        atoms = {"product_names": {"services": [], "consumables": [], "storables": ["Regal"]}}
        with patch.object(master_data.data_factory, "assign_tracking") as mocked:
            master_data._create_products(client, atoms, ctx)
            assert mocked.call_count == 1, mocked.call_count
            call_vals_list, lot_pct, serial_pct = mocked.call_args.args
            assert lot_pct == 20 and serial_pct == 5, (lot_pct, serial_pct)
            assert len(call_vals_list) == 1, call_vals_list
        results.append((
            "S13/R13: assign_tracking called once with configured percentages "
            "when avg_qty>0 and 'stock' installed", True, "",
        ))
    except AssertionError as e:
        results.append((
            "S13/R13: assign_tracking called once with configured percentages "
            "when avg_qty>0 and 'stock' installed", False, str(e),
        ))

    # ------------------------------------------------------------------
    # S13/B1: gate reads avg_qty specifically, not just "stock dict truthy" —
    # avg_qty=0 must skip assign_tracking even with 'stock' installed and
    # non-zero percentages configured (an avg_qty=0 run never creates a
    # stock.lot for it either, see inventory.py — a product left marked
    # tracking='lot' with no lot ever created is exactly what this avoids).
    # ------------------------------------------------------------------
    try:
        client = _mock_client_for_batches()
        ctx = _make_ctx()
        ctx.installed_modules = {"stock"}
        ctx.module_selections.stock = {"avg_qty": 0, "tracking_lot_pct": 50, "tracking_serial_pct": 0}
        atoms = {"product_names": {"services": [], "consumables": [], "storables": ["Regal"]}}
        with patch.object(master_data.data_factory, "assign_tracking") as mocked:
            master_data._create_products(client, atoms, ctx)
            mocked.assert_not_called()
        results.append(("S13/B1: avg_qty=0 -> assign_tracking not called, even with pct set", True, ""))
    except AssertionError as e:
        results.append(("S13/B1: avg_qty=0 -> assign_tracking not called, even with pct set", False, str(e)))

    # ------------------------------------------------------------------
    # S13/S7: 'stock' not in installed_modules -> gate stays off too.
    # ------------------------------------------------------------------
    try:
        client = _mock_client_for_batches()
        ctx = _make_ctx()
        ctx.installed_modules = set()
        ctx.module_selections.stock = {"avg_qty": 10, "tracking_lot_pct": 50, "tracking_serial_pct": 0}
        atoms = {"product_names": {"services": [], "consumables": [], "storables": ["Regal"]}}
        with patch.object(master_data.data_factory, "assign_tracking") as mocked:
            master_data._create_products(client, atoms, ctx)
            mocked.assert_not_called()
        results.append(("S13/S7: 'stock' not installed -> assign_tracking not called", True, ""))
    except AssertionError as e:
        results.append(("S13/S7: 'stock' not installed -> assign_tracking not called", False, str(e)))

    # ------------------------------------------------------------------
    # S13/Befund 4: ctx.new_product_ids gets exactly the ids _create_products
    # itself created this run — nothing more, nothing less.
    # ------------------------------------------------------------------
    try:
        client = _mock_client_for_batches()
        ctx = _make_ctx()
        ctx.product_ids = [1, 2]  # simulates use_existing ids already merged in
        atoms = {"product_names": {"services": ["Beratung"], "consumables": [], "storables": ["Regal"]}}
        master_data._create_products(client, atoms, ctx)
        assert len(ctx.new_product_ids) == 2, ctx.new_product_ids
        assert set(ctx.new_product_ids) <= set(ctx.product_ids), (ctx.new_product_ids, ctx.product_ids)
        assert 1 not in ctx.new_product_ids and 2 not in ctx.new_product_ids, ctx.new_product_ids
        results.append(("S13/Befund 4: new_product_ids holds only this run's own creates", True, ""))
    except AssertionError as e:
        results.append(("S13/Befund 4: new_product_ids holds only this run's own creates", False, str(e)))

    # ------------------------------------------------------------------
    # S13/WP5-review: a create_batch failure while 'tracking' is present in
    # the vals (e.g. an ACL/group restriction on the field, untested beyond
    # demo-test5) must retry once with 'tracking' stripped rather than take
    # down the whole product batch — services/consumables have no
    # 'tracking' key either way and must not be affected.
    # ------------------------------------------------------------------
    try:
        client = MagicMock()
        counter = {"n": 2000}
        calls = []

        def _create_batch(model, values_list, context=None):
            calls.append([dict(v) for v in values_list])
            if len(calls) == 1 and any('tracking' in v for v in values_list):
                raise Exception("tracking: insufficient access rights")
            ids = []
            for _ in values_list:
                counter["n"] += 1
                ids.append(counter["n"])
            return ids

        client.create_batch.side_effect = _create_batch
        ctx = _make_ctx()
        ctx.installed_modules = {"stock"}
        ctx.module_selections.stock = {"avg_qty": 10, "tracking_lot_pct": 100, "tracking_serial_pct": 0}
        atoms = {"product_names": {"services": [], "consumables": [], "storables": ["Regal"]}}
        master_data._create_products(client, atoms, ctx)
        assert len(calls) == 2, f"expected exactly one retry, got {len(calls)} calls"
        assert any('tracking' in v for v in calls[0]), "first attempt should still carry tracking"
        assert all('tracking' not in v for v in calls[1]), "retry must strip tracking"
        assert len(ctx.product_ids) == 1, ctx.product_ids
        results.append((
            "S13/WP5-review: create_batch failure with tracking set -> retries once without tracking",
            True, "",
        ))
    except AssertionError as e:
        results.append((
            "S13/WP5-review: create_batch failure with tracking set -> retries once without tracking",
            False, str(e),
        ))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results


if __name__ == "__main__":
    ok, steps = run()
    for label, passed, detail in steps:
        print(f"{'OK  ' if passed else 'FAIL'}  {label}  {detail}")
    sys.exit(0 if ok else 1)
