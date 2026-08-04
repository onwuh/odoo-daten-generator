"""
Batch CRUD helpers for Odoo models.

These complement OdooJson2Client with functions that pre-fetch reference data
in bulk to avoid N+1 query patterns.
"""

from typing import Dict, List
from odoo_client import OdooJson2Client


def resolve_country_ids(client: OdooJson2Client, country_codes: List[str]) -> Dict[str, int]:
    """Batch-lookup country codes to Odoo IDs in a single API call.

    Returns a dict of {UPPER_CASE_CODE: id}.
    """
    if not country_codes:
        return {}
    codes = list({c.upper() for c in country_codes if c})
    records = client.search_read('res.country', [["code", "in", codes]], fields=["id", "code"])
    return {r["code"]: r["id"] for r in records}


def fetch_skill_levels_map(client: OdooJson2Client) -> Dict[int, List[Dict]]:
    """Pre-fetch all skill levels grouped by skill_type_id.

    Returns {skill_type_id: [{"id": x, "level_progress": y}, ...]}
    so callers can pick a level without querying per skill.
    """
    levels = client.search_read(
        'hr.skill.level', [], fields=["id", "skill_type_id", "level_progress"]
    )
    result: Dict[int, List[Dict]] = {}
    for lv in levels:
        st_id = lv.get("skill_type_id")
        if isinstance(st_id, (list, tuple)) and st_id:
            st_id = st_id[0]
        if st_id:
            result.setdefault(st_id, []).append({
                "id": lv["id"],
                "level_progress": lv.get("level_progress", 0),
            })
    return result
