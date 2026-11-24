"""Pushdown must never change query results.

Oracle test: load every Mongo document into a plain in-memory SQLite database (no
pushdown at all), run the same SQL there and through osquery, and require identical
results. Needs the stack running (scripts/stack.sh up) and seeded data:

    pytest -m integration
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter

import pytest
from pymongo import MongoClient

from showquery.catalog import TABLES, Column, ColumnType, Table
from showquery.config import get_settings
from showquery.osquery_client import OsqueryClient

pytestmark = pytest.mark.integration


def _resolve(doc: dict, column: Column, table: Table, element: dict | None):
    path = column.path
    if element is not None and table.is_unwound(column):
        value: object = element
        path = path[len(table.unwind) + 1:]
    else:
        value = doc
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str, sort_keys=True)
    if column.type is ColumnType.EPOCH:
        return int(ColumnType.EPOCH.from_mongo(value))
    return value


@pytest.fixture(scope="module")
def oracle() -> sqlite3.Connection:
    settings = get_settings()
    docs = list(MongoClient(settings.mongo_uri, tz_aware=True)[settings.mongo_db][
        settings.mongo_collection].find({}, {"_id": 0}))
    assert docs, "no data: run showquery-seed first"
    db = sqlite3.connect(":memory:")
    for table in TABLES:
        cols = ", ".join(f'"{c.name}" {c.type.sql_type}' for c in table.columns)
        db.execute(f'CREATE TABLE "{table.name}" ({cols})')
        rows = []
        for doc in docs:
            if any(doc.get(k) != v for k, v in table.base_filter.items()):
                continue
            elements = [None]
            if table.unwind:
                elements = doc
                for part in table.unwind.split("."):
                    elements = elements.get(part, {})
            for element in elements or []:
                rows.append([_resolve(doc, c, table, element) for c in table.columns])
        marks = ", ".join("?" * len(table.columns))
        db.executemany(f'INSERT INTO "{table.name}" VALUES ({marks})', rows)
    return db


@pytest.fixture(scope="module")
def osq() -> OsqueryClient:
    client = OsqueryClient(get_settings().osquery_socket)
    if not client.ping():
        pytest.skip("osquery not running")
    return client


QUERIES = [
    "SELECT device, collected_date FROM device_versions WHERE site = 'dfw1'",
    "SELECT device FROM device_versions WHERE site IN ('dfw1', 'aus1') AND role = 'core'",
    "SELECT device, interface FROM interfaces WHERE status = 'err-disabled' OR vlan = '30'",
    "SELECT device, interface FROM interfaces"
    " WHERE device = 'dfw1-acc-01' OR interface = 'Gi1/0/7'",
    "SELECT device FROM device_versions WHERE device LIKE 'DFW%' AND platform LIKE '%ios'",
    "SELECT device, interface FROM interfaces WHERE description LIKE 'ap-%'",
    "SELECT device FROM device_versions WHERE uptime_seconds < 86400",
    "SELECT device, neighbor FROM bgp_neighbors WHERE prefixes_received BETWEEN 1 AND 500",
    "SELECT device, neighbor FROM bgp_neighbors WHERE state != 'Established'",
    "SELECT device FROM bgp_neighbors WHERE remote_as != 65000",
    "SELECT device FROM bgp_neighbors WHERE remote_as = '65000'",  # text vs integer affinity
    "SELECT device FROM show_commands WHERE collected_at >= strftime('%s', '2026-11-20')"
    " AND collected_at < strftime('%s', '2026-11-22')",
    "SELECT device FROM show_commands WHERE command = 'show version' AND output LIKE '%17.6%'",
    "SELECT device FROM device_versions WHERE device GLOB 'iah*'",
    "SELECT count(*) AS n FROM interfaces",
    "SELECT d.device, b.neighbor FROM device_versions d JOIN bgp_neighbors b"
    " ON b.device = d.device AND b.collected_date = d.collected_date"
    " WHERE d.role = 'edge' AND b.state = 'Established'",
    "SELECT device, interface FROM interfaces WHERE interface IN ('Gi1/0/1', 'Et1', 'Eth1/1')"
    " AND collected_date = (SELECT max(collected_date) FROM device_versions)",
]


def _normalise(rows) -> Counter:
    return Counter(tuple("" if v is None else str(v) for v in row) for row in rows)


@pytest.mark.parametrize("sql", QUERIES)
def test_osquery_matches_unpushed_sqlite(sql, oracle, osq):
    expected_cursor = oracle.execute(sql)
    names = [d[0] for d in expected_cursor.description]
    expected = _normalise(expected_cursor.fetchall())
    result = osq.query(sql)
    assert result.columns == names
    actual = _normalise([[r.get(n) for n in names] for r in result.rows])
    assert actual == expected
    assert sum(expected.values()) > 0, "query should return rows to be meaningful"
