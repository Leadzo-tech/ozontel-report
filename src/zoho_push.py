"""Pushes Ozonetel call dispositions into Zoho CRM Leads.

Runs after the Ozonetel CDR fetch, reusing the same raw records the sheet
sync already pulled -- no extra Ozonetel API calls. For each call on one of
our two automated campaigns (LeadzoStarterPack / LeadzoStarterPackFollowup)
with a disposition set, it:

  - matches the call to a Lead by phone number (last 10 digits, to tolerate
    formatting differences between Ozonetel's E164 and Zoho's Phone field)
  - maps the Ozonetel disposition to a Lead_Status stage and a
    Last_Disposition value (Zoho's own vocabulary, which predates and
    doesn't match Ozonetel's agent-console labels 1:1 -- this map is the one
    place that reconciles them)
  - pins Owner to whichever agent took the call, but only the first time --
    an existing Owner is never overwritten, since that's what keeps the same
    rep on a seller across repeat calls
  - sets Next_Call_At for anything still active, or clears it if the
    disposition closed the lead out

Idempotent on CallID via each Lead's Last_Call_ID field, so it's safe to
call on every run even though the routine window re-pulls the last 2 days
each time (re-processing an already-handled call is a no-op, not a repeat
Dials_Today increment or a pushed-out Next_Call_At).

Credentials are plain Lambda environment variables (ZOHO_CLIENT_ID,
ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN) -- same pattern as OZONETEL_API_KEY
for this function, not SSM, since these aren't shared with any other system.
Missing creds means Zoho push is simply skipped (treated as "not configured",
same convention as the optional Slack webhook).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from src.logging_utils import log_event

IST = timezone(timedelta(hours=5, minutes=30))

OUR_CAMPAIGNS = {"LeadzoStarterPack", "LeadzoStarterPackFollowup"}

# Ozonetel agent-console disposition -> (Last_Disposition value in Zoho's
# own vocabulary, Lead_Status stage, terminal/closed).
DISPOSITION_MAP: dict[str, tuple[str, str, bool]] = {
    "DNP": ("No answer", "No Response", False),
    "Decision Maker NA": ("Callback requested", "Connected - Follow-up", False),
    "Interested": ("Callback requested", "Connected - Follow-up", False),
    "General Enquiry": ("Callback requested", "Connected - Follow-up", False),
    "Meeting Scheduled": ("Demo booked", "Demo Scheduled", False),
    "Demo Completed": ("Interested - negotiating", "Negotiation", False),
    "Offer Given": ("Interested - offer given", "Payment Link Shared", False),
    "Sale made": ("Interested - offer given", "Won - SP Purchased", True),
    "Not Interested": ("Not interested", "Nurture", True),
    "Free lead given": ("Free lead given", "Free Lead Live", False),
    "Not a Service Business": ("Not a service business", "Disqualified", True),
    "Do Not Call": ("Do not call", "Do Not Contact", True),
    "Wrong Number": ("Wrong number", "Disqualified", True),
}


def _digits(value: str) -> str:
    d = "".join(c for c in (value or "") if c.isdigit())
    return d[-10:] if len(d) >= 10 else d


def _assigned_agent_ids(records: list[dict]) -> dict[str, str]:
    """CallID -> AgentID, same cross-leg resolution as the sheet sync's
    AssignedAgent (the leg that answered wins, else the one it was offered
    to), but keyed on the stable AgentID rather than the display name."""
    by_call: dict[str, str] = {}
    for r in records:
        call_id, agent_id = str(r.get("CallID", "")), str(r.get("AgentID", "") or "")
        if not call_id or not agent_id:
            continue
        if r.get("Status") == "Answered" or call_id not in by_call:
            by_call[call_id] = agent_id
    return by_call


def _next_call_at(now: datetime, disposition: str) -> str | None:
    hour = now.hour
    is_calling_hours = 10 <= hour < 19 and now.strftime("%A") != "Sunday"
    if disposition == "DNP" and is_calling_hours:
        nxt = now + timedelta(minutes=120)
    else:
        roll_days = 0 if (hour < 10 and now.strftime("%A") != "Sunday") else 1
        nxt = (now + timedelta(days=roll_days)).replace(hour=10, minute=0, second=0, microsecond=0)
    return nxt.strftime("%Y-%m-%dT%H:%M:%S+05:30")


def _access_token(client_id: str, client_secret: str, refresh_token: str, accounts_domain: str) -> str:
    resp = requests.post(
        f"{accounts_domain}/oauth/v2/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def push_dispositions(
    records: list[dict],
    client_id: str,
    client_secret: str,
    refresh_token: str,
    accounts_domain: str,
    api_domain: str,
) -> dict:
    access_token = _access_token(client_id, client_secret, refresh_token, accounts_domain)
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}", "Content-Type": "application/json"}

    # Candidate set: only leads currently out for a call. Narrows the search
    # and means phone-matching never has to scan the whole Leads module.
    search_resp = requests.get(
        f"{api_domain}/crm/v2/Leads/search",
        headers=headers,
        params={"criteria": "(Ozonetel_Queued:equals:true)"},
        timeout=20,
    )
    if search_resp.status_code == 204:
        candidates = []
    else:
        search_resp.raise_for_status()
        candidates = search_resp.json().get("data", [])
    by_digits = {_digits(c.get("Phone", "")): c for c in candidates if _digits(c.get("Phone", ""))}

    assigned_agent_ids = _assigned_agent_ids(records)
    now = datetime.now(IST)

    pushed, skipped, errors = 0, 0, 0

    for r in records:
        if r.get("CampaignName") not in OUR_CAMPAIGNS:
            continue
        disposition = r.get("Disposition", "")
        if disposition not in DISPOSITION_MAP:
            continue
        call_id = str(r.get("CallID", ""))
        if not call_id:
            continue
        phone_digits = _digits(r.get("E164", ""))
        lead = by_digits.get(phone_digits) if phone_digits else None
        if not lead:
            skipped += 1
            continue
        if str(lead.get("Last_Call_ID", "") or "") == call_id:
            skipped += 1
            continue

        last_disposition, lead_status, terminal = DISPOSITION_MAP[disposition]

        update: dict[str, Any] = {
            "Lead_Status": lead_status,
            "Last_Disposition": last_disposition,
            "Last_Call_At": now.strftime("%Y-%m-%dT%H:%M:%S+05:30"),
            "Last_Call_ID": call_id,
            "Dials_Today": int(lead.get("Dials_Today") or 0) + 1,
            "Ozonetel_Queued": False,
            "Next_Call_At": None if terminal else _next_call_at(now, disposition),
        }

        owner = lead.get("Owner") or {}
        if not owner.get("id"):
            agent_id = assigned_agent_ids.get(call_id, "")
            # Agents module keys an Ozonetel agent ID back to its Zoho user --
            # resolved per call rather than pre-fetched since most calls hit
            # the same handful of agents and the lookup is cheap by comparison
            # to the main search above.
            if agent_id:
                agent_resp = requests.get(
                    f"{api_domain}/crm/v2/Agents/search",
                    headers=headers,
                    params={"criteria": f"(Ozonetel_Agent_ID:equals:{agent_id})"},
                    timeout=15,
                )
                if agent_resp.status_code == 200:
                    agent_rows = agent_resp.json().get("data", [])
                    if agent_rows:
                        agent_user = agent_rows[0].get("Agent_User") or {}
                        if agent_user.get("id"):
                            update["Owner"] = agent_user["id"]

        update_resp = requests.put(
            f"{api_domain}/crm/v2/Leads/{lead['id']}",
            headers=headers,
            json={"data": [update], "trigger": ["workflow"]},
            timeout=20,
        )
        if update_resp.status_code == 200 and update_resp.json().get("data", [{}])[0].get("status") == "success":
            pushed += 1
        else:
            errors += 1
            log_event(
                "zoho_push_error",
                call_id=call_id,
                lead_id=lead.get("id"),
                response=update_resp.text[:500],
            )

    log_event("zoho_push_summary", pushed=pushed, skipped=skipped, errors=errors)
    return {"pushed": pushed, "skipped": skipped, "errors": errors}
