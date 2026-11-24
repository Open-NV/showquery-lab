from datetime import datetime, timezone

import pytest

from showquery.catalog import BGP_NEIGHBORS, DEVICE_VERSIONS, INTERFACES, SHOW_COMMANDS, ColumnType
from showquery.pushdown import Op, build_pipeline, like_to_regex, row_id, translate


def ctx(*constraints, cols=None):
    return {
        "constraints": [
            {"name": name, "list": [{"op": int(op), "expr": expr}], "affinity": "TEXT"}
            for name, op, expr in constraints
        ],
        "colsUsed": cols or [],
    }


@pytest.mark.parametrize(
    ("pattern", "matches", "rejects"),
    [
        ("dfw%", ["dfw1-core-01", "DFW2"], ["xdfw"]),
        ("%core%", ["dfw1-core-01"], ["dfw1-acc-01"]),
        ("Gi1/0/_", ["Gi1/0/1"], ["Gi1/0/10"]),
        ("10.0.0.1", ["10.0.0.1"], ["10x0x0x1"]),  # dots are literal
        ("%16.12%", ["Version 16.12.4\nmore lines"], ["Version 17.6.5"]),
    ],
)
def test_like_to_regex_matches_sqlite_semantics(pattern, matches, rejects):
    import re

    regex = re.compile(like_to_regex(pattern), re.IGNORECASE | re.DOTALL)
    assert all(regex.match(s) for s in matches)
    assert not any(regex.match(s) for s in rejects)


def test_equality_and_ranges_translate_with_types():
    uptime = DEVICE_VERSIONS.column("uptime_seconds")
    assert translate(uptime, Op.LESS_THAN, "86400") == {"parsed.uptime_seconds": {"$lt": 86400}}
    assert translate(uptime, Op.GREATER_THAN_OR_EQUALS, "1.5") == {
        "parsed.uptime_seconds": {"$gte": 1.5}
    }
    device = SHOW_COMMANDS.column("device")
    assert translate(device, Op.EQUALS, "r1") == {"device": {"$eq": "r1"}}
    assert translate(device, Op.NOT_EQUALS, "r1") == {"device": {"$ne": "r1"}}


def test_epoch_columns_convert_to_datetimes():
    clause = translate(SHOW_COMMANDS.column("collected_at"), Op.GREATER_THAN, "1795132800")
    assert clause == {"collected_at": {"$gt": datetime(2026, 11, 20, tzinfo=timezone.utc)}}


def test_untranslatable_constraints_are_skipped():
    uptime = DEVICE_VERSIONS.column("uptime_seconds")
    assert translate(uptime, Op.EQUALS, "not-a-number") is None
    assert translate(uptime, Op.LIKE, "1%") is None  # LIKE on numbers compares text form
    assert translate(SHOW_COMMANDS.column("device"), Op.GLOB, "dfw*") is None
    assert translate(SHOW_COMMANDS.column("device"), Op.LIMIT, "5001") is None
    assert translate(SHOW_COMMANDS.column("device"), 999, "x") is None


def test_document_filters_run_before_unwind_and_element_filters_after():
    pipeline = build_pipeline(
        INTERFACES,
        ctx(("device", Op.EQUALS, "r1"), ("status", Op.EQUALS, "err-disabled"),
            cols=["device", "interface"]),
        limit=11,
    )
    assert pipeline[0] == {"$match": {"$and": [
        {"command": "show interfaces status"}, {"device": {"$eq": "r1"}}]}}
    assert pipeline[1] == {"$unwind": {"path": "$parsed.interfaces",
                                       "includeArrayIndex": "_element_index"}}
    assert pipeline[2] == {"$match": {"parsed.interfaces.status": {"$eq": "err-disabled"}}}
    assert pipeline[3] == {"$project": {
        "_id": 1, "_element_index": 1, "device": "$device", "interface": "$parsed.interfaces.name"}}
    assert pipeline[4] == {"$limit": 11}


def test_unused_columns_are_not_fetched():
    pipeline = build_pipeline(SHOW_COMMANDS, ctx(cols=["device"]), limit=5)
    project = next(stage["$project"] for stage in pipeline if "$project" in stage)
    assert "output" not in project and "parsed" not in project


def test_count_star_still_returns_rows():
    pipeline = build_pipeline(BGP_NEIGHBORS, {"constraints": [], "colsUsed": []}, limit=5)
    assert pipeline[0] == {"$match": {"command": "show ip bgp summary"}}
    assert {"$project": {"_id": 1, "_element_index": 1}} in pipeline


def test_unknown_constraint_columns_are_ignored():
    pipeline = build_pipeline(SHOW_COMMANDS, ctx(("nope", Op.EQUALS, "x")), limit=5)
    assert pipeline[0] == {"$match": {}}


def test_column_type_round_trip():
    when = datetime(2026, 11, 24, 2, 0, tzinfo=timezone.utc)
    assert ColumnType.EPOCH.from_mongo(when) == str(int(when.timestamp()))
    assert ColumnType.EPOCH.from_mongo(when.replace(tzinfo=None)) == str(int(when.timestamp()))
    assert ColumnType.INTEGER.to_mongo("42") == 42


def test_row_ids_are_stable_and_distinguish_array_elements():
    assert row_id({"_id": "a", "_element_index": 0}) == row_id({"_id": "a", "_element_index": 0})
    assert row_id({"_id": "a", "_element_index": 0}) != row_id({"_id": "a", "_element_index": 1})
    assert row_id({"_id": "a"}) != row_id({"_id": "b"})
    assert 0 <= row_id({"_id": "a"}) < 2**63
