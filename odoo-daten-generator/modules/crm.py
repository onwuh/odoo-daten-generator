"""CRM module: creates opportunities/leads, distributes across stages,
posts chatter messages, and creates follow-up activities."""

import logging
import random
import datetime
from collections import defaultdict

from config import RunContext
from fallback_data import FALLBACK_OPPORTUNITY_TITLES

logger = logging.getLogger(__name__)

_EARLY_STAGE_KEYWORDS = ('neu', 'new', 'eingang', 'incoming', 'qualifizier', 'qualify', 'kontakt', 'contact')


# ---------------------------------------------------------------------------
# Low-level CRM helpers
# ---------------------------------------------------------------------------

def create_opportunity(client, partner_id, name, extra_vals=None):
    logger.info(f"-> Creating Opportunity for partner {partner_id}: {name}")
    values = {"type": "opportunity", "partner_id": partner_id, "name": name}
    if extra_vals:
        values.update(extra_vals)
    return client.create('crm.lead', values)


def create_lead(client, partner_id, name, extra_vals=None):
    logger.info(f"-> Creating Lead for partner {partner_id}: {name}")
    values = {"type": "lead", "partner_id": partner_id, "name": name}
    if extra_vals:
        values.update(extra_vals)
    return client.create('crm.lead', values)


def get_crm_stages(client, exclude_won=True):
    """Get CRM stages, optionally excluding 'won' stage."""
    stages = client.search_read('crm.stage', [], fields=["id", "name"], limit=0)
    if exclude_won:
        stages = [s for s in stages if s.get("name", "").lower() != "won"]
    return stages


def _early_stages(stages):
    """Return stage IDs whose names suggest early pipeline position."""
    early = [s["id"] for s in stages
             if any(kw in s.get("name", "").lower() for kw in _EARLY_STAGE_KEYWORDS)]
    return (early or [stages[0]["id"]]) if stages else []


def _build_partner_pool(company_ids, num_records):
    """Return a list of partner_ids of length num_records with at most 2 per company."""
    pool = company_ids * 2
    random.shuffle(pool)
    if num_records <= len(pool):
        return pool[:num_records]
    # Need more than 2x companies — allow repeats for the overflow
    extras = random.choices(company_ids, k=num_records - len(pool))
    return pool + extras


def _unique_titles(bank, n):
    """Return n opportunity/lead titles, unique within the batch.

    messages_by_title (chatter) is keyed by title, so duplicates silently
    collide (B9). random.sample already guarantees uniqueness when n fits the
    bank; on overflow, disambiguate with a counter suffix — guaranteed unique,
    unlike appending the partner name (a partner can appear twice in the pool).
    """
    if not bank:
        bank = ["Opportunity"]
    if n <= len(bank):
        return random.sample(bank, n)
    base = [random.choice(bank) for _ in range(n)]
    counts = {}
    result = []
    for t in base:
        counts[t] = counts.get(t, 0) + 1
        result.append(t if counts[t] == 1 else f"{t} #{counts[t]}")
    return result


def _extra_vals():
    """Random date_deadline + expected_revenue for a crm.lead record."""
    deadline = (datetime.date.today() + datetime.timedelta(days=random.randint(30, 180))).isoformat()
    revenue = round(random.uniform(5000, 150000), 2)
    return {"date_deadline": deadline, "expected_revenue": revenue}


def _activity_deadline(past_pct: int, today_pct: int) -> str:
    """Return an ISO date string distributed across past/today/future buckets."""
    r = random.randint(0, 99)
    if r < past_pct:
        days = random.randint(1, 30)
        return (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    elif r < past_pct + today_pct:
        return datetime.date.today().isoformat()
    else:
        days = random.randint(3, 30)
        return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


def _get_crm_lead_model_id(client):
    """Return the ir.model ID for crm.lead (needed for mail.activity)."""
    try:
        recs = client.search_read('ir.model', [['model', '=', 'crm.lead']], fields=['id'], limit=1)
        return recs[0]['id'] if recs else None
    except Exception:
        return None


def _get_activity_types(client):
    """Return list of mail.activity.type records with id + name."""
    try:
        return client.search_read('mail.activity.type', [], fields=['id', 'name'], limit=0)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Module entry point
# ---------------------------------------------------------------------------

def create_crm_data(client, llm, ctx: RunContext) -> None:
    """Creates CRM opportunities (and optionally leads), distributes across stages,
    posts chatter messages, and creates follow-up activities."""
    num_opps = ctx.module_selections.crm
    num_leads = ctx.module_selections.leads
    if num_opps <= 0 and num_leads <= 0:
        return
    if not ctx.partner_company_ids:
        return

    opp_titles_bank = ctx.name_banks.get('opportunity_titles', []) or FALLBACK_OPPORTUNITY_TITLES
    all_stages = get_crm_stages(client, exclude_won=True)
    stage_ids = [s["id"] for s in all_stages]

    # --- Salespeople pool (always fetched — user_id assignment is independent of chatter) ---
    sales_users = _fetch_sales_users(client)
    if sales_users:
        logger.info(f"   -> {len(sales_users)} interne Benutzer als Verkäufer verfügbar.")

    # --- Opportunities ---
    if num_opps > 0:
        logger.info(f"\n--- CRM: Erstelle {num_opps} Opportunities ---")
        partner_pool = _build_partner_pool(ctx.partner_company_ids, num_opps)
        opp_titles = _unique_titles(opp_titles_bank, num_opps)
        opp_vals_list = []
        opp_meta = []  # (partner_id, name, salesperson), same order as opp_vals_list
        for partner_id, name in zip(partner_pool, opp_titles):
            extra = _extra_vals()
            salesperson = random.choice(sales_users) if sales_users else None
            if salesperson:
                extra['user_id'] = salesperson['user_id']
            values = {"type": "opportunity", "partner_id": partner_id, "name": name}
            values.update(extra)
            opp_vals_list.append(values)
            opp_meta.append((partner_id, name, salesperson))

        opp_ids = client.create_batch('crm.lead', opp_vals_list)
        ctx.opportunity_ids.extend(opp_ids)
        opp_data = []
        for opp_id, (partner_id, name, salesperson) in zip(opp_ids, opp_meta):
            opp_data.append({
                'id': opp_id,
                'name': name,
                'partner_id': partner_id,
                'partner_name': '',
                'salesperson': salesperson,
            })

        if stage_ids:
            logger.info("--- CRM: Verteile Opportunities auf Phasen ---")
            stage_to_ids = defaultdict(list)
            for opp_id in ctx.opportunity_ids:
                stage_to_ids[random.choice(stage_ids)].append(opp_id)
            for stage_id, ids in stage_to_ids.items():
                client.write('crm.lead', ids, {"stage_id": stage_id})

        logger.info(f"✅ {len(ctx.opportunity_ids)} Opportunities erstellt.")

        # --- Chatter messages ---
        if ctx.module_selections.crm_chatter:
            # Enrich with partner names (one batch call)
            partner_ids = list({o['partner_id'] for o in opp_data})
            partner_info = _fetch_partner_names(client, partner_ids)
            for o in opp_data:
                info = partner_info.get(o['partner_id'], {})
                o['partner_name'] = info.get('name', '')
            _post_chatter_messages(client, llm, ctx, opp_data)

        # --- Activities ---
        if ctx.module_selections.crm_activities is not None:
            _create_activities(client, ctx.opportunity_ids, ctx)

    # --- Leads ---
    if num_leads > 0:
        logger.info(f"\n--- CRM: Erstelle {num_leads} Leads ---")
        early_ids = _early_stages(all_stages)
        partner_pool = _build_partner_pool(ctx.partner_company_ids, num_leads)
        lead_titles = _unique_titles(opp_titles_bank, num_leads)
        lead_vals_list = []
        for partner_id, name in zip(partner_pool, lead_titles):
            values = {"type": "lead", "partner_id": partner_id, "name": name}
            values.update(_extra_vals())
            lead_vals_list.append(values)
        ctx.lead_ids.extend(client.create_batch('crm.lead', lead_vals_list))

        if early_ids:
            client.write('crm.lead', ctx.lead_ids, {"stage_id": random.choice(early_ids)})

        logger.info(f"✅ {len(ctx.lead_ids)} Leads erstellt.")


def mark_lost_opportunities(client, llm, ctx: RunContext) -> None:
    """R11: marks a configurable share of opportunities as lost.

    Must run after sale.py — operates only on opportunities sale.py did NOT
    link to an order (ctx.linked_opportunity_ids), so a lead never ends up
    both lost and Won-staged. Enforced by orchestrator.py's module_order
    position ("crm_lost" right after "sale"), not by this function.

    crm.lost.reason is searched, never created (reference-data convention,
    see modules/mrp.py's quality.alert.team/quality.point.test_type search —
    an empty pool skips the feature entirely rather than inventing a reason).
    """
    sel = ctx.module_selections.crm_lost
    if not sel:
        return
    pct = sel.pct
    if pct <= 0:
        return
    if not ctx.opportunity_ids:
        return

    unlinked = [oid for oid in ctx.opportunity_ids if oid not in ctx.linked_opportunity_ids]
    if not unlinked:
        return

    reasons = client.search_read('crm.lost.reason', [], fields=['id'], limit=0)
    if not reasons:
        logger.warning("⚠️  Keine crm.lost.reason gefunden — Lost-Opportunities übersprungen.")
        return

    num_lost = round(len(unlinked) * pct / 100)
    to_lost = random.sample(unlinked, k=min(num_lost, len(unlinked)))
    if not to_lost:
        return

    logger.info(f"\n--- CRM: Markiere {len(to_lost)} Opportunities als verloren ---")
    reason_ids = [r['id'] for r in reasons]
    groups = defaultdict(list)
    for oid in to_lost:
        groups[random.choice(reason_ids)].append(oid)
    for reason_id, ids in groups.items():
        try:
            # active=False + lost_reason_id is sufficient — won_status is a
            # compute field and correctly resolves to 'lost' (live-verified
            # S12/WP3), no action_set_lost call needed.
            client.write('crm.lead', ids, {"active": False, "probability": 0, "lost_reason_id": reason_id})
        except Exception as e:
            logger.warning(f"⚠️  Konnte {len(ids)} Opportunities nicht als verloren markieren: {e}")

    logger.info(f"✅ {len(to_lost)} Opportunities als verloren markiert.")


# ---------------------------------------------------------------------------
# Chatter
# ---------------------------------------------------------------------------

def _fetch_sales_users(client):
    """Return internal Odoo users as potential salesperson/author pool.

    Returns list of dicts: [{"user_id": int, "partner_id": int, "name": str, "email": str}]
    Falls back to [] on any error.
    """
    try:
        users = client.search_read(
            'res.users',
            [['active', '=', True], ['share', '=', False]],
            fields=['id', 'name', 'partner_id', 'email'],
            limit=0,
        )
        result = []
        for u in users:
            pid = u.get('partner_id')
            pid = pid[0] if isinstance(pid, (list, tuple)) else pid
            if not pid:
                continue
            result.append({
                'user_id': u['id'],
                'partner_id': pid,
                'name': u.get('name', ''),
                'email': u.get('email', ''),
            })
        return result
    except Exception as e:
        logger.warning(f"⚠️  Konnte Sales-User nicht laden: {e}")
        return []


def _fetch_partner_names(client, partner_ids):
    """Batch-fetch partner names for a list of IDs.

    Returns dict {partner_id: {"name": str, "email": str}}.
    """
    if not partner_ids:
        return {}
    try:
        recs = client.search_read(
            'res.partner',
            [['id', 'in', list(set(partner_ids))]],
            fields=['id', 'name', 'email'],
            limit=0,
        )
        return {r['id']: {'name': r.get('name', ''), 'email': r.get('email', '')} for r in recs}
    except Exception as e:
        logger.warning(f"⚠️  Konnte Partner-Namen nicht laden: {e}")
        return {}


def _normalize_message(raw) -> dict:
    """Normalize a raw LLM message item to a consistent dict.

    Handles legacy string format (old cache) and new dict format.
    """
    if isinstance(raw, str):
        return {'type': 'note', 'speaker': 'salesperson', 'body': raw}
    if isinstance(raw, dict):
        return {
            'type': raw.get('type', 'note'),
            'speaker': raw.get('speaker', 'salesperson'),
            'body': raw.get('body', ''),
        }
    return {'type': 'note', 'speaker': 'salesperson', 'body': str(raw)}


def _post_chatter_messages(client, llm, ctx: RunContext, opp_data):
    """Post LLM-generated chatter messages per opportunity.

    Args:
        opp_data: list of dicts with keys:
            id, name, partner_id, partner_name, salesperson (dict or None)
    """
    if not opp_data:
        return

    chatter_cfg = ctx.module_selections.crm_chatter
    if not chatter_cfg:
        return

    style = chatter_cfg.style
    messages_per_opp = chatter_cfg.messages_per_opp

    # This prompt is the ONE place where values read out of the target database
    # reach an LLM: 'customer' is a res.partner name and 'salesperson' a res.users
    # name. When the run creates its own master data both are LLM-invented, but
    # with "vorhandene Daten einbeziehen" the customer is a real existing contact,
    # and the salesperson is a real user of the instance either way.
    #
    # use_db_names is the user's answer to the consent prompt in the UI. Declined
    # (or absent) means generic placeholders go out instead of the real names —
    # the chatter still reads naturally, it just addresses "Kunde"/"Verkäufer".
    use_db_names = bool(chatter_cfg.use_db_names)

    # One participant pair per opportunity — not a single sample reused for the
    # whole batch (B9), so each opp's messages address its actual customer/rep.
    opportunities = [
        {
            'title': o['name'],
            'customer': (o.get('partner_name') if use_db_names else None) or 'Kunde',
            'salesperson': ((o.get('salesperson') or {}).get('name')
                            if use_db_names else None) or 'Verkäufer',
        }
        for o in opp_data
    ]

    try:
        messages_by_title = llm.fetch_crm_chatter_messages(
            opportunities, ctx.industry, ctx.language_name,
            style=style, messages_per_opp=messages_per_opp,
        )
    except Exception as e:
        logger.warning(f"⚠️  Chatter-Generierung fehlgeschlagen: {e}")
        return

    if not messages_by_title:
        return

    logger.info("--- CRM: Poste Chatter-Nachrichten ---")
    for opp in opp_data:
        opp_id = opp['id']
        raw_msgs = messages_by_title.get(opp['name'], [])
        if not raw_msgs:
            continue

        customer_partner_id = opp.get('partner_id')
        salesperson = opp.get('salesperson')
        salesperson_partner_id = (salesperson or {}).get('partner_id')
        salesperson_email = (salesperson or {}).get('email', '')

        for raw in raw_msgs[:messages_per_opp]:
            msg = _normalize_message(raw)
            body = msg['body']
            if not body:
                continue

            speaker = msg['speaker']
            msg_type = msg['type']

            kwargs = {'body': body}

            if msg_type == 'email':
                kwargs['message_type'] = 'email'
                if speaker == 'customer' and customer_partner_id:
                    kwargs['author_id'] = customer_partner_id
                elif speaker == 'salesperson' and salesperson_partner_id:
                    kwargs['author_id'] = salesperson_partner_id
                    if salesperson_email:
                        kwargs['email_from'] = salesperson_email
            else:
                kwargs['message_type'] = 'comment'
                kwargs['subtype_xmlid'] = 'mail.mt_note'
                if salesperson_partner_id:
                    kwargs['author_id'] = salesperson_partner_id

            try:
                client.call_method('crm.lead', 'message_post', ids=[opp_id], kwargs=kwargs)
            except Exception as e:
                logger.warning(f"⚠️  Chatter für Opp {opp_id} fehlgeschlagen: {e}")


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------

def _create_activities(client, opp_ids, ctx: RunContext):
    """Create one follow-up mail.activity per opportunity."""
    if not opp_ids:
        return

    act_cfg = ctx.module_selections.crm_activities
    if act_cfg is None:
        return

    model_id = _get_crm_lead_model_id(client)
    if not model_id:
        logger.warning("⚠️  Aktivitäten übersprungen: ir.model ID für crm.lead nicht gefunden.")
        return

    activity_types = _get_activity_types(client)
    if not activity_types:
        logger.warning("⚠️  Aktivitäten übersprungen: keine mail.activity.type gefunden.")
        return

    # Prefer call/email/meeting types; fall back to all types
    preferred_keywords = ('call', 'email', 'meeting', 'anruf', 'e-mail', 'besprechung', 'termin', 'aufgabe', 'todo')
    preferred = [t for t in activity_types
                 if any(kw in t.get('name', '').lower() for kw in preferred_keywords)]
    type_pool = preferred or activity_types

    past_pct = act_cfg.past_pct
    today_pct = act_cfg.today_pct

    logger.info(f"--- CRM: Erstelle Aktivitäten für {len(opp_ids)} Opportunities ---")
    activity_vals_list = []
    for opp_id in opp_ids:
        deadline = _activity_deadline(past_pct, today_pct)
        activity_type = random.choice(type_pool)
        activity_vals_list.append({
            'res_id': opp_id,
            'res_model_id': model_id,
            'activity_type_id': activity_type['id'],
            'date_deadline': deadline,
            'summary': activity_type.get('name', 'Follow-up'),
        })

    try:
        client.create_batch('mail.activity', activity_vals_list)
    except Exception as e:
        logger.warning(f"⚠️  Aktivitäten fehlgeschlagen: {e}")

    logger.info("✅ Aktivitäten erstellt.")
