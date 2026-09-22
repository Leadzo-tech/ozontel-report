from sheets_client import (
    MAX_CELL_CHARS,
    compute_columns,
    flatten_document,
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
