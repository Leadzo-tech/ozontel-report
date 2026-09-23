from src.sheets_client import (
    MAX_CELL_CHARS,
    compute_columns,
    flatten_document,
    merge_rows,
    rows_to_values,
    truncate_cell,
)


def test_truncate_cell_leaves_short_strings_alone():
    assert truncate_cell("hello") == "hello"


def test_truncate_cell_leaves_non_strings_alone():
    assert truncate_cell(123) == 123
    assert truncate_cell(None) is None


def test_truncate_cell_clamps_oversized_strings():
    value = "x" * (MAX_CELL_CHARS + 500)
    result = truncate_cell(value)
    assert len(result) <= MAX_CELL_CHARS
    assert result.endswith("[truncated]")


def test_flatten_document_flattens_nested_dicts():
    doc = {"a": 1, "b": {"c": 2, "d": {"e": 3}}}
    assert flatten_document(doc) == {"a": 1, "b.c": 2, "b.d.e": 3}


def test_flatten_document_json_encodes_lists():
    doc = {"tags": ["x", "y"]}
    assert flatten_document(doc) == {"tags": '["x","y"]'}


def test_compute_columns_preferred_order_first_then_alphabetical_rest():
    rows = [{"b": 1, "a": 2}, {"c": 3}]
    columns = compute_columns(rows, preferred_order=["a", "b"])
    assert columns == ["a", "b", "c"]


def test_compute_columns_drops_preferred_keys_not_present():
    rows = [{"a": 1}]
    columns = compute_columns(rows, preferred_order=["z", "a"])
    assert columns == ["a"]


def test_rows_to_values_fills_missing_fields_with_empty_string():
    rows = [{"CallID": 1, "Status": "Answered"}, {"CallID": 2}]
    columns, values = rows_to_values(rows, preferred_order=["CallID", "Status"])
    assert columns == ["CallID", "Status"]
    assert values == [[1, "Answered"], [2, ""]]


def test_merge_rows_keeps_history_updates_in_place_and_appends_new():
    existing = [["CallID", "Status"], ["1", "Unanswered"], ["2", "Answered"]]
    new = [{"CallID": 2, "Status": "Transferred"}, {"CallID": 3, "Status": "Answered"}]
    merged = merge_rows(existing, new, "CallID")
    assert merged == [
        {"CallID": "1", "Status": "Unanswered"},
        {"CallID": 2, "Status": "Transferred"},
        {"CallID": 3, "Status": "Answered"},
    ]


def test_merge_rows_on_empty_sheet_returns_new_rows():
    assert merge_rows([], [{"CallID": 1}], "CallID") == [{"CallID": 1}]


def test_truncate_cell_writes_long_integers_as_text():
    assert truncate_cell(90576912345678901) == "90576912345678901"
    assert truncate_cell(123) == 123


def test_merge_rows_replaces_a_callid_sheets_rounded():
    # Sheets stored 90576912345678901 as a double and hands it back rounded.
    existing = [["CallID", "Status"], [9.05769123456789e16, "Unanswered"], ["1", "Answered"]]
    new = [{"CallID": "90576912345678901", "Status": "Answered"}]
    merged = merge_rows(existing, new, "CallID")
    assert merged == [
        {"CallID": "90576912345678901", "Status": "Answered"},
        {"CallID": "1", "Status": "Answered"},
    ]
