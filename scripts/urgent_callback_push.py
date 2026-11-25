"""Pushes the highest-priority callback leads to Ozonetel every 5 minutes.

Zoho's own Schedule feature can't run more often than hourly, which is fine
for most of the pipeline but too slow for leads already in a live
conversation (Connected - Follow-up, Negotiation, Demo Scheduled) -- a
callback due at 2:05pm shouldn't wait until a 3:00pm hourly sweep. This
script covers only that one priority tier; `dial_scheduler` in Zoho still
runs hourly and handles everything else (New, No Response, Payment Link
Shared, Free Lead Live). Deliberately narrow, not a full re-implementation.

Mirrors dial_scheduler's campaign-routing logic exactly: no agent assigned
yet -> LeadzoStarterPack, agent already assigned -> LeadzoStarterPackFollowup.
Priority is always 90 here since this script only ever touches that tier.
"""
import os
import sys

import requests

CALLBACK_STATUSES = {"Connected - Follow-up", "Negotiation", "Demo Scheduled"}
OZONETEL_USERNAME = "leadzo"
OZONETEL_DOMAIN = "https://in1-ccaas-api.ozonetel.com"


def zoho_access_token(client_id, client_secret, refresh_token, accounts_domain):
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


def main():
    client_id = os.environ["ZOHO_CLIENT_ID"]
    client_secret = os.environ["ZOHO_CLIENT_SECRET"]
    refresh_token = os.environ["ZOHO_REFRESH_TOKEN"]
    accounts_domain = os.environ.get("ZOHO_ACCOUNTS_DOMAIN", "https://accounts.zoho.in")
    api_domain = os.environ.get("ZOHO_API_DOMAIN", "https://www.zohoapis.in")
    ozonetel_api_key = os.environ["OZONETEL_API_KEY"]

    access_token = zoho_access_token(client_id, client_secret, refresh_token, accounts_domain)
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}", "Content-Type": "application/json"}

    criteria = "(Ozonetel_Queued:equals:false)"
    resp = requests.get(
        f"{api_domain}/crm/v2/Leads/search",
        headers=headers,
        params={"criteria": criteria},
        timeout=20,
    )
    if resp.status_code == 204:
        print("No candidate leads.")
        return
    resp.raise_for_status()
    candidates = resp.json().get("data", [])

    pushed = 0
    for lead in candidates:
        status = lead.get("Lead_Status", "")
        if status not in CALLBACK_STATUSES:
            continue

        phone = lead.get("Phone") or ""
        if not phone:
            continue

        next_call_at = lead.get("Next_Call_At")
        if not next_call_at:
            continue
        # Next_Call_At is ISO8601 with +05:30 offset, same as "now" in that zone --
        # plain string comparison works since both share the same format/offset.
        from datetime import datetime, timedelta, timezone
        ist = timezone(timedelta(hours=5, minutes=30))
        now_iso = datetime.now(ist).strftime("%Y-%m-%dT%H:%M:%S+05:30")
        if next_call_at > now_iso:
            continue

        dials_today = lead.get("Dials_Today") or 0
        if dials_today >= 2:
            continue

        owner = lead.get("Owner") or {}
        owner_id = owner.get("id")
        ozonetel_agent_id = ""
        if owner_id:
            agent_resp = requests.get(
                f"{api_domain}/crm/v2/Agents/search",
                headers=headers,
                params={"criteria": f"(Agent_User:equals:{owner_id})"},
                timeout=15,
            )
            if agent_resp.status_code == 200:
                rows = agent_resp.json().get("data", [])
                if rows:
                    ozonetel_agent_id = rows[0].get("Ozonetel_Agent_ID") or ""

        campaign_name = "LeadzoStarterPackFollowup" if ozonetel_agent_id else "LeadzoStarterPack"

        params = {
            "userName": OZONETEL_USERNAME,
            "apiKey": ozonetel_api_key,
            "campaignName": campaign_name,
            "phoneNumber": phone,
            "name": lead.get("Company") or "",
            "priority": 90,
            "checkDuplicate": "true",
        }
        if ozonetel_agent_id:
            params["agentId"] = ozonetel_agent_id

        oz_resp = requests.post(f"{OZONETEL_DOMAIN}/ca_apis/AddCampaignData", data=params, timeout=20)
        if oz_resp.status_code != 200:
            print(f"Ozonetel push failed for lead {lead.get('id')}: {oz_resp.status_code} {oz_resp.text[:300]}")
            continue

        update_resp = requests.put(
            f"{api_domain}/crm/v2/Leads/{lead['id']}",
            headers=headers,
            json={"data": [{"Ozonetel_Queued": True}], "trigger": ["workflow"]},
            timeout=20,
        )
        if update_resp.status_code == 200:
            pushed += 1
        else:
            print(f"Zoho update failed for lead {lead.get('id')}: {update_resp.status_code} {update_resp.text[:300]}")

    print(f"Pushed {pushed} urgent callback lead(s).")


if __name__ == "__main__":
    sys.exit(main())
