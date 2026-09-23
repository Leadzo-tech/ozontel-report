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


def merge_rows(
    existing_values: list[list[Any]], new_rows: list[dict[str, Any]], key: str
) -> list[dict[str, Any]]:
    """Upsert new_rows into the rows already on the sheet (header row first).

    Existing rows keep their position; a new row whose key matches replaces it
    in place, anything else is appended. Rows with no key are always appended.
    """
    merged: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    if existing_values:
        header = existing_values[0]
        for raw in existing_values[1:]:
            row = {name: value for name, value in zip(header, raw) if name}
            row_key = str(row.get(key, ""))
            if row_key:
                index[row_key] = len(merged)
            merged.append(row)
    for row in (flatten_document(r) for r in new_rows):
        row_key = str(row.get(key, ""))
        if row_key and row_key in index:
            merged[index[row_key]] = row
        else:
            if row_key:
                index[row_key] = len(merged)
            merged.append(row)
    return merged


class SheetWriter:
    def __init__(self, service_account_info: dict[str, Any]):
        self.client = gspread.service_account_from_dict(service_account_info)

    def _worksheet(self, sheet_spec: dict[str, Any]) -> gspread.Worksheet:
        spreadsheet = self.client.open_by_key(sheet_spec["spreadsheet_id"])
        worksheet_name = sheet_spec["worksheet_name"]
        try:
            return spreadsheet.worksheet(worksheet_name)
        except gspread.WorksheetNotFound:
            return spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=26)

    def write(self, sheet_spec: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
        if sheet_spec.get("write_mode", "overwrite") == "merge":
            return self.merge(sheet_spec, rows)
        return self.overwrite(sheet_spec, rows)

    def merge(self, sheet_spec: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Keep every row already on the sheet and upsert `rows` by merge_key,
        so history accumulates beyond Ozonetel's 15-day retention.

        Never clears the sheet: if a write fails partway, the old rows are still
        there (possibly with some already updated) rather than wiped."""
        worksheet = self._worksheet(sheet_spec)
        # Unformatted, so a long numeric CallID comes back as 1234567890123456
        # rather than "1.23457E+15" and still matches on upsert.
        existing = worksheet.get_values(value_render_option=gspread.utils.ValueRenderOption.unformatted)
        merged = merge_rows(existing, rows, sheet_spec["merge_key"])

        preferred = sheet_spec.get("columns", {}).get("preferred_order", [])
        if preferred:
            # Old rows carry whatever columns the sheet had; with a fixed column
            # list, a column removed from it must disappear from history too.
            merged = [{column: row.get(column, "") for column in preferred} for row in merged]
        columns, values = rows_to_values(merged, preferred)
        payload: list[list[Any]] = [columns, *values]
        # Grow first so the write fits, then shrink to trim columns that were
        # dropped. Rows only ever grow.
        worksheet.resize(
            rows=max(worksheet.row_count, len(payload)), cols=max(worksheet.col_count, len(columns))
        )
        for i in range(0, len(payload), _WRITE_BATCH_SIZE):
            worksheet.update(payload[i:i + _WRITE_BATCH_SIZE], f"A{1 + i}", value_input_option="RAW")
        worksheet.resize(rows=len(payload), cols=len(columns))

        return {
            "rows_written": len(values),
            "columns_written": columns,
            "range": f"A1:{len(payload)}x{len(columns)}",
        }

    def overwrite(self, sheet_spec: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
        worksheet = self._worksheet(sheet_spec)

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
