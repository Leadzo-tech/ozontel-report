import json
from collections.abc import Mapping
from typing import Any

import gspread

# Google Sheets rejects any single cell over 50000 characters with
# APIError [400] "Your input contains more than the maximum of 50000 characters
# in a single cell", which fails the whole write. Cap every cell below that so
# one fat field can't kill the export.
MAX_CELL_CHARS = 50000
_WRITE_BATCH_SIZE = 10_000
_TRUNCATION_MARKER = "… [truncated]"


def truncate_cell(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
        keep = MAX_CELL_CHARS - len(_TRUNCATION_MARKER) - 1
        return value[:keep] + _TRUNCATION_MARKER
    return value


def flatten_document(document: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in document.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            flattened.update(flatten_document(value, name))
        elif isinstance(value, list):
            flattened[name] = json.dumps(value, default=str, separators=(",", ":"))
        else:
            flattened[name] = value
    return flattened


def compute_columns(rows: list[dict[str, Any]], preferred_order: list[str] | None = None) -> list[str]:
    preferred_order = preferred_order or []
    discovered = {key for row in rows for key in row.keys()}
    ordered = [key for key in preferred_order if key in discovered]
    ordered.extend(sorted(discovered - set(ordered)))
    return ordered


def rows_to_values(rows: list[dict[str, Any]], preferred_order: list[str] | None = None) -> tuple[list[str], list[list[Any]]]:
    flat_rows = [flatten_document(row) for row in rows]
    columns = compute_columns(flat_rows, preferred_order)
    values = [[truncate_cell(row.get(column, "")) for column in columns] for row in flat_rows]
    return columns, values


class SheetWriter:
    def __init__(self, service_account_info: dict[str, Any]):
        self.client = gspread.service_account_from_dict(service_account_info)

    def overwrite(self, sheet_spec: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
        spreadsheet = self.client.open_by_key(sheet_spec["spreadsheet_id"])
        worksheet_name = sheet_spec["worksheet_name"]
        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=26)

        preferred = sheet_spec.get("columns", {}).get("preferred_order", [])
        columns, values = rows_to_values(rows, preferred)
        payload: list[list[Any]] = []
        if sheet_spec.get("include_headers", True):
            payload.append(columns)
        payload.extend(values)

        if sheet_spec.get("clear_before_write", True):
            worksheet.clear()
        if payload:
            worksheet.resize(rows=2, cols=2)
            worksheet.resize(rows=len(payload), cols=len(columns))
            start_cell = sheet_spec.get("start_cell", "A1")
            col = "".join(c for c in start_cell if c.isalpha()).upper() or "A"
            start_row = int("".join(c for c in start_cell if c.isdigit()) or "1")
            for i in range(0, len(payload), _WRITE_BATCH_SIZE):
                batch = payload[i:i + _WRITE_BATCH_SIZE]
                worksheet.update(batch, f"{col}{start_row + i}", value_input_option="RAW")

        return {
            "rows_written": len(values),
            "columns_written": columns,
            "range": f"{sheet_spec.get('start_cell', 'A1')}:{len(payload)}x{len(columns)}",
        }
