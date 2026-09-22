"""Ozonetel CDR -> Google Sheet sync.

Standalone version for the dedicated ozonetel-cdr-proxy-poc Lambda (not the
query-scheduler worker). Same proven logic: pulls call records from
Ozonetel's HTTP API and overwrites the "Ozonetel" tab of the KPI sheet.

Why this runs as a real Lambda instead of Google Apps Script: Ozonetel's
fetchCDRDetails endpoint requires a literal GET request carrying a JSON body.
Google Apps Script's UrlFetchApp silently rewrites any GET+payload request to
POST on the wire (confirmed directly with an echo-server test), and
Ozonetel's route 405s on POST. Python's requests library sends the method
exactly as given (confirmed working against Ozonetel's production endpoint
via curl: GET + apiKey header + JSON body -> 200 with real data).
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import requests

from secrets import get_json_parameter
from sheets_client import SheetWriter

OZONETEL_DOMAIN = "https://in1-ccaas-api.ozonetel.com"
OZONETEL_ENDPOINT = f"{OZONETEL_DOMAIN}/ca_reports/fetchCDRDetails"
IST = timezone(timedelta(hours=5, minutes=30))

SHEET_SPEC = {
    "spreadsheet_id": "1WsTggCbZbSBV4DeAzznebcH51o8pJUFLOdVJMFMrhE0",
    "worksheet_name": "Ozonetel",
    "start_cell": "A1",
    "include_headers": True,
    "clear_before_write": True,
    "columns": {
        "preferred_order": [
            "CallID", "CallDate", "StartTime", "EndTime", "Duration", "TalkTime",
            "HandlingTime", "CallerID", "DialedNumber", "E164", "DID", "Location",
            "AgentID", "AgentName", "Skill", "CampaignName", "CallFlow", "Type",
            "Status", "DialStatus", "AgentDialStatus", "CustomerDialStatus",
            "Disposition", "Comments", "HangupBy", "QueueTime", "HoldDuration",
            "WrapupDuration", "TimeToAnswer", "DialCount", "UCID", "UUI", "CallAudio",
        ]
    },
}

# Ozonetel only serves the last 15 days; a full backfill pulls all of them
# (rate-limited to 2 req/min, so this takes ~7.5 min — under the Lambda
# timeout but worth knowing). The routine hourly run only needs the last
# couple of days, to pick up late-arriving/updated records without redoing
# the whole 15-day pull every time.
DAYS_BACK_FULL = 15
DAYS_BACK_ROUTINE = 2
RATE_LIMIT_SLEEP_SECONDS = 31


def _days_ago_str(i: int) -> str:
    return (datetime.now(IST) - timedelta(days=i)).strftime("%Y-%m-%d")


def _fetch_day(date_str: str, api_key: str, username: str) -> list[dict]:
    payload = {
        "fromDate": f"{date_str} 00:00:00",
        "toDate": f"{date_str} 23:59:59",
        "userName": username,
    }
    resp = requests.request(
        "GET",
        OZONETEL_ENDPOINT,
        headers={"apiKey": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=25,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Ozonetel HTTP {resp.status_code} for {date_str}: {resp.text}")
    data = resp.json()
    if data.get("status") not in ("success", "true", True):
        raise RuntimeError(f"Ozonetel API error for {date_str}: {data.get('message', data)}")
    return data.get("details", [])


def run(full_backfill: bool = False) -> dict:
    api_key = os.environ.get("OZONETEL_API_KEY")
    username = os.environ.get("OZONETEL_USERNAME")
    if not api_key or not username:
        raise RuntimeError("Missing OZONETEL_API_KEY / OZONETEL_USERNAME environment variables")

    days_back = DAYS_BACK_FULL if full_backfill else DAYS_BACK_ROUTINE
    all_records: list[dict] = []
    per_day_counts: dict[str, int] = {}

    for i in range(days_back, 0, -1):
        date_str = _days_ago_str(i)
        records = _fetch_day(date_str, api_key, username)
        per_day_counts[date_str] = len(records)
        all_records.extend(records)
        if i > 1:
            time.sleep(RATE_LIMIT_SLEEP_SECONDS)

    google_sa_json_param = os.environ.get("GOOGLE_SA_JSON_PARAM")
    if not google_sa_json_param:
        raise RuntimeError("Missing GOOGLE_SA_JSON_PARAM environment variable")
    google_sa_json = get_json_parameter(google_sa_json_param)
    sheet_result = SheetWriter(google_sa_json).overwrite(SHEET_SPEC, all_records)

    return {
        "status": "success",
        "days_pulled": days_back,
        "per_day_counts": per_day_counts,
        "rows_written": sheet_result["rows_written"],
    }
