"""Live integration test for the D7 run journal and its cleanup path (S9).

Pattern 4 applies: every created record is read back and asserted on a
non-trivial field, and the cleanup is verified by reading the record back and
finding it gone — not by trusting the unlink call's return value.
"""
import tempfile
from pathlib import Path

import run_journal


def run(client, ctx):
    """
    Consumes: nothing from ctx (creates and removes its own throwaway records).
    Returns: (all_passed, [(label, ok, detail), ...])
    """
    results = []
    created_ids = []

    with tempfile.TemporaryDirectory() as tmp:
        journal = run_journal.RunJournal("demo-itest-journal", Path(tmp))

        # Step 1 — JournalingClient records what it creates, and the record is real
        try:
            jclient = run_journal.JournalingClient(
                client.base_url.replace("/json/2", ""), client.database, client.api_key,
                journal=journal,
            )
            partner_id = jclient.create('res.partner', {"name": "S9 Journal Testkontakt"})
            created_ids.append(partner_id)
            assert isinstance(partner_id, int) and partner_id > 0, partner_id

            read_back = client.search_read('res.partner', [["id", "=", partner_id]],
                                           fields=["name", "active"], limit=1)
            assert read_back, "Kontakt nach dem Anlegen nicht lesbar"
            assert read_back[0]["name"] == "S9 Journal Testkontakt", read_back[0]
            assert ("res.partner", partner_id) in journal.entries, journal.entries
            assert journal.path.exists(), "Journal-Datei fehlt"
            results.append(("run_journal: create wird erfasst und ist live lesbar",
                            True, f"partner {partner_id}"))
        except Exception as e:
            results.append(("run_journal: create wird erfasst und ist live lesbar", False, str(e)))
            return False, results

        # Step 2 — create_batch is recorded once per id, not twice
        try:
            batch_ids = jclient.create_batch('res.partner', [
                {"name": "S9 Journal Batch A"},
                {"name": "S9 Journal Batch B"},
            ])
            created_ids.extend(batch_ids)
            assert len(batch_ids) == 2, batch_ids
            recorded = [rid for model, rid in journal.entries if model == "res.partner"]
            assert sorted(recorded) == sorted(created_ids), (recorded, created_ids)
            assert len(recorded) == len(set(recorded)), f"Doppelte Einträge: {recorded}"
            rows = client.search_read('res.partner', [["id", "in", batch_ids]],
                                      fields=["name"], limit=0)
            assert len(rows) == 2, rows
            results.append(("run_journal: create_batch erfasst jede ID genau einmal",
                            True, f"{len(recorded)} Einträge"))
        except Exception as e:
            results.append(("run_journal: create_batch erfasst jede ID genau einmal", False, str(e)))

        # Step 3 — the journal survives a restart (reload from disk)
        try:
            reloaded = run_journal.RunJournal.load("demo-itest-journal", Path(tmp))
            assert reloaded.entries == journal.entries, (reloaded.entries, journal.entries)
            results.append(("run_journal: neu geladenes Journal ist identisch",
                            True, f"{len(reloaded.entries)} Einträge"))
        except Exception as e:
            results.append(("run_journal: neu geladenes Journal ist identisch", False, str(e)))

        # Step 4 — cleanup removes the records; read-back proves it
        try:
            summary = run_journal.delete_run(client, journal)
            assert summary["deleted"] == len(created_ids), summary
            assert not summary["failed"], summary["failed"]
            remaining = client.search_read('res.partner', [["id", "in", created_ids]],
                                           fields=["id"], limit=0)
            assert remaining == [], f"nach dem Löschen noch vorhanden: {remaining}"
            created_ids = []
            results.append(("run_journal: delete_run entfernt alle Datensätze",
                            True, f"{summary['deleted']} gelöscht"))
        except Exception as e:
            results.append(("run_journal: delete_run entfernt alle Datensätze", False, str(e)))

        # Step 5 — Pattern 5: an empty journal is a graceful no-op
        try:
            empty = run_journal.RunJournal("demo-itest-empty", Path(tmp))
            summary = run_journal.delete_run(client, empty)
            assert summary == {"deleted": 0, "archived": 0, "failed": [], "skipped": 0, "total": 0}, summary
            results.append(("run_journal: leeres Journal löscht nichts (Pattern 5)", True, ""))
        except Exception as e:
            results.append(("run_journal: leeres Journal löscht nichts (Pattern 5)", False, str(e)))

        # Step 6 — S12/WP5 cold-review fund: an APPROVED hr.expense used to
        # make delete_run fail outright (Odoo refuses unlink on
        # approved/posted expenses, and delete_run unlinks a whole model
        # group in one call — so this single record would have failed
        # deletion for every hr.expense in the same run). CANCEL_BEFORE_UNLINK
        # now runs action_reset first; this proves the real fix end-to-end,
        # not just the mocked unit tests.
        try:
            employees = client.search_read('hr.employee', [], fields=["id"], limit=1)
            products = client.search_read(
                'product.product', [["can_be_expensed", "=", True]], fields=["id"], limit=1,
            )
            if not employees or not products:
                results.append(("run_journal: SKIP — kein Mitarbeiter/keine Spesenkategorie",
                                True, "skipped"))
            else:
                expense_journal = run_journal.RunJournal("demo-itest-expense", Path(tmp))
                expense_id = client.create('hr.expense', {
                    "employee_id": employees[0]["id"], "product_id": products[0]["id"],
                    "name": "S12/WP5 Journal Reset Test", "total_amount": 10.0,
                    "payment_mode": "own_account",
                })
                expense_journal.record('hr.expense', [expense_id])
                client.write('hr.expense', [expense_id], {"approval_state": "submitted"})
                client.write('hr.expense', [expense_id], {"approval_state": "approved"})

                summary = run_journal.delete_run(client, expense_journal)
                assert summary["deleted"] == 1 and not summary["failed"], summary
                remaining = client.search_read(
                    'hr.expense', [["id", "=", expense_id]], fields=["id"], limit=0,
                )
                assert remaining == [], f"approved expense still present after delete_run: {remaining}"
                results.append((
                    "run_journal: delete_run löscht auch genehmigte hr.expense (action_reset)",
                    True, f"expense {expense_id}",
                ))
        except Exception as e:
            results.append(("run_journal: delete_run löscht auch genehmigte hr.expense (action_reset)", False, str(e)))

    # Safety net: never leave test partners behind if an assertion aborted early.
    if created_ids:
        try:
            client.call_method('res.partner', 'unlink', ids=created_ids)
        except Exception:
            pass

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results
