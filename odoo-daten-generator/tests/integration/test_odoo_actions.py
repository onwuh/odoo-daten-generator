"""Live integration test for odoo_actions.py's S10/R10 write-access probing.

Two things only a live instance can prove, which is why these aren't unit
tests: (1) POST /{model}/has_access actually answers true/false the way
CLAUDE.md documents (odoo_client.has_create_access's whole design rests on
that live-verified shape), and (2) check_field_compatibility's module gating
(A2b) really does cut requests, not just in a mocked call-count assertion.
"""
import odoo_actions


def run(client, ctx):
    """
    Consumes: ctx.installed_modules (read-only).
    Returns: (all_passed, [(label, ok, detail), ...])
    """
    results = []

    # ------------------------------------------------------------------
    # has_create_access against a model the pipeline definitely creates
    # into — the live instance's API key must be able to write res.partner,
    # or nothing in this tool works at all.
    # ------------------------------------------------------------------
    try:
        result = client.has_create_access('res.partner')
        assert result is True, f"expected True for a model this tool must be able to create, got {result!r}"
        results.append(("has_create_access: res.partner -> True (live)", True, ""))
    except Exception as e:
        results.append(("has_create_access: res.partner -> True (live)", False, str(e)))

    # ------------------------------------------------------------------
    # A model that does not exist on this instance: the live 404 that
    # odoo_client.py's docstring/CLAUDE.md documents, and exactly ONE POST —
    # no fallback-chain fan-out (unlike routing this through model_method,
    # which would cost ~6 extra attempts per nonexistent model).
    # ------------------------------------------------------------------
    try:
        real_post = client.session.post
        calls = []

        def _counting_post(url, *args, **kwargs):
            calls.append(url)
            return real_post(url, *args, **kwargs)

        client.session.post = _counting_post
        try:
            result = client.has_create_access('this.model.does.not.exist.on.odoo')
        finally:
            client.session.post = real_post
        assert result is False, f"expected False for a nonexistent model, got {result!r}"
        assert len(calls) == 1, f"expected exactly one POST, got {len(calls)}: {calls}"
        results.append(("has_create_access: nichtexistentes Modell -> False, genau ein POST (live)",
                        True, f"{calls[0] if calls else '–'}"))
    except Exception as e:
        results.append(("has_create_access: nichtexistentes Modell -> False, genau ein POST (live)", False, str(e)))

    # ------------------------------------------------------------------
    # probe_model_access: only models of installed modules are probed —
    # live-count the POSTs, not just the returned dict's keys, since a stray
    # extra probe would still produce a plausible-looking dict.
    # ------------------------------------------------------------------
    try:
        real_post = client.session.post
        calls = []

        def _counting_post(url, *args, **kwargs):
            calls.append(url)
            return real_post(url, *args, **kwargs)

        client.session.post = _counting_post
        try:
            access = odoo_actions.probe_model_access(client, installed_modules=set())
        finally:
            client.session.post = real_post

        always_on = set(odoo_actions.MODEL_ACCESS_PROBES["stammdaten"]) | \
                   set(odoo_actions.MODEL_ACCESS_PROBES["documents"])
        assert set(access.keys()) == always_on, (
            f"expected only the always-on models with no installed modules, got {sorted(access.keys())}"
        )
        assert len(calls) == len(always_on), (
            f"expected exactly {len(always_on)} POSTs (one has_access per always-on model), "
            f"got {len(calls)}: {calls}"
        )
        results.append(("probe_model_access: keine installierten Module -> nur Stammdaten/Dokumente sondiert (live)",
                        True, f"{len(calls)} POSTs, {list(access.values())}"))
    except Exception as e:
        results.append(("probe_model_access: keine installierten Module -> nur Stammdaten/Dokumente sondiert (live)",
                        False, str(e)))

    # ------------------------------------------------------------------
    # check_field_compatibility (A2b): gating on installed_modules must
    # actually cut the request count. Counting raw POSTs rather than error
    # entries deliberately: whether a gated model's fields_get would have
    # FAILED depends on whether that app happens to be installed on THIS
    # live instance, which this test must not depend on — the request
    # count is the one signal that proves the gate fired regardless.
    # ------------------------------------------------------------------
    try:
        all_module_keys = {mk for mk, _ in odoo_actions.FIELD_COMPAT_WHITELIST.values() if mk is not None}

        def _count_requests(installed):
            real_post = client.session.post
            counts = {"n": 0}

            def _counting_post(url, *args, **kwargs):
                counts["n"] += 1
                return real_post(url, *args, **kwargs)

            client.session.post = _counting_post
            try:
                odoo_actions.check_field_compatibility(client, installed_modules=installed)
            finally:
                client.session.post = real_post
            return counts["n"]

        gated_requests = _count_requests(set())
        all_requests = _count_requests(all_module_keys)
        assert gated_requests < all_requests, (
            f"gating had no effect: {gated_requests} requests with nothing installed, "
            f"{all_requests} with every module key installed"
        )
        results.append(("check_field_compatibility: Gating senkt die Anfragezahl messbar (live)",
                        True, f"{gated_requests} (nichts installiert) < {all_requests} (alles installiert)"))
    except Exception as e:
        results.append(("check_field_compatibility: Gating senkt die Anfragezahl messbar (live)", False, str(e)))

    all_ok = all(ok for _, ok, _ in results)
    return all_ok, results
