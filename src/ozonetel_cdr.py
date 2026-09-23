"""Ozonetel CDR -> Google Sheet sync.

Pulls call detail records from Ozonetel's fetchCDRDetails API and writes them
to the target worksheet (overwrite, or merge by CallID to keep history). Everything configurable (endpoint, pull window, sheet
target, column order) comes from the spec in scheduled_reports/.

Two things about Ozonetel's API drive the shape of this code, both verified
against production:

1. fetchCDRDetails requires a *literal GET carrying a JSON body*. requests
   sends the method exactly as given; Google Apps Script's UrlFetchApp does
   not — it silently rewrites any GET-with-payload into a POST on the wire
   (proved with an echo server), and the route 405s on POST. That is the
   whole reason this runs as a Lambda instead of in Apps Script.

2. Auth is the `apiKey` header, NOT a Bearer token. The account can mint a
   token from /ca_reports/CAToken/generateToken, but fetchCDRDetails rejects
   it with a misleading 401 {"status":"false","message":"Missing userName or
   apiKey"} — the same message it returns for genuinely missing fields.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from src.logging_utils import log_event
from src.secrets import get_json_parameter, parameter_name
from src.sheets_client import SheetWriter

IST = timezone(timedelta(hours=5, minutes=30))


def _days_ago_str(i: int) -> str:
    return (datetime.now(IST) - timedelta(days=i)).strftime("%Y-%m-%d")


def _fetch_day(endpoint: str, date_str: str, api_key: str, username: str) -> list[dict]:
    # fromDate and toDate must land on the same calendar day — Ozonetel serves
    # one day per call, so a range spanning midnight returns nothing useful.
    payload = {
        "fromDate": f"{date_str} 00:00:00",
        "toDate": f"{date_str} 23:59:59",
        "userName": username,
    }
    resp = requests.request(
        "GET",
        endpoint,
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


def _project(records: list[dict], projection: dict[str, str]) -> list[dict]:
    """Shape each record: keep only the projected fields, under their output
    names, in the order declared.

    This is the analog of the `$project` stage every query-scheduler spec uses —
    one construct that selects, renames and orders, rather than three separate
    settings. A source field Ozonetel didn't return becomes "" instead of a
    missing key, so the sheet keeps a fixed shape whatever a given day's data
    happens to contain.
    """
    return [
        {output: record.get(source, "") for output, source in projection.items()}
        for record in records
    ]


def run(
    spec_raw: dict[str, Any],
    api_key: str,
    username: str,
    full_backfill: bool = False,
    replace: bool = False,
) -> dict:
    ozonetel = spec_raw["ozonetel"]
    endpoint = ozonetel["endpoint"]
    days_back = ozonetel["days_back_full"] if full_backfill else ozonetel["days_back_routine"]
    sleep_seconds = ozonetel["rate_limit_sleep_seconds"]

    all_records: list[dict] = []
    per_day_counts: dict[str, int] = {}

    # i == 0 is today, so the sheet includes calls made so far today.
    for i in range(days_back, -1, -1):
        date_str = _days_ago_str(i)
        records = _fetch_day(endpoint, date_str, api_key, username)
        per_day_counts[date_str] = len(records)
        all_records.extend(records)
        log_event("ozonetel_fetch", date=date_str, records=len(records))
        # Ozonetel rate-limits fetchCDRDetails to 2 requests/minute.
        if i > 0:
            time.sleep(sleep_seconds)

    sheet_spec = spec_raw["sheet"]
    projection = ozonetel.get("projection") or {}
    if projection:
        all_records = _project(all_records, projection)
        # The projection's key order is the column order — no second list to
        # keep in sync.
        sheet_spec = {
            **sheet_spec,
            "columns": {**(sheet_spec.get("columns") or {}), "preferred_order": list(projection)},
        }

    if replace:
        # Rebuild the tab from scratch with just what was fetched. Only reached
        # once every day's fetch has succeeded, so a failed pull can't leave
        # the sheet empty. Anything older than the pull window is dropped.
        sheet_spec = {**sheet_spec, "write_mode": "overwrite", "clear_before_write": True}

    google_sa_json = get_json_parameter(parameter_name("GOOGLE_SA_JSON_PARAM", "google/sa-json"))
    sheet_result = SheetWriter(google_sa_json).write(sheet_spec, all_records)

    return {
        "status": "success",
        "replaced_sheet": replace,
        "days_pulled": len(per_day_counts),
        "per_day_counts": per_day_counts,
        "rows_read": len(all_records),
        "rows_written": sheet_result["rows_written"],
        "sheet": {
            "spreadsheet_id": spec_raw["sheet"]["spreadsheet_id"],
            "worksheet_name": spec_raw["sheet"]["worksheet_name"],
            "range": sheet_result["range"],
        },
    }
