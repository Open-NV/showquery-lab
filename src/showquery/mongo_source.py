"""Executes table queries against MongoDB and shapes documents into osquery rows."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from pymongo import MongoClient
from pymongo.collection import Collection

from .catalog import Table
from .pushdown import build_pipeline, row_id

log = logging.getLogger(__name__)


class ScanLimitExceeded(RuntimeError):
    pass


class MongoSource:
    def __init__(
        self,
        collection: Collection,
        *,
        max_scan_rows: int,
        max_time_ms: int,
    ) -> None:
        self._collection = collection
        self._max_scan_rows = max_scan_rows
        self._max_time_ms = max_time_ms

    @classmethod
    def from_uri(cls, uri: str, db: str, collection: str, **kwargs: Any) -> MongoSource:
        client: MongoClient = MongoClient(uri, tz_aware=True, appname="showquery-osquery")
        return cls(client[db][collection], **kwargs)

    def rows(self, table: Table, context: dict[str, Any]) -> list[dict[str, str]]:
        log.debug("table=%s context=%s", table.name, json.dumps(context))
        pipeline = build_pipeline(table, context, limit=self._max_scan_rows + 1)
        started = time.perf_counter()
        docs = list(self._collection.aggregate(pipeline, maxTimeMS=self._max_time_ms))
        log.info(
            "table=%s rows=%d ms=%.1f match=%s",
            table.name, len(docs), (time.perf_counter() - started) * 1000,
            json.dumps(pipeline[0]["$match"], default=str),
        )
        if len(docs) > self._max_scan_rows:
            raise ScanLimitExceeded(
                f"{table.name}: query would scan more than {self._max_scan_rows:,} rows; "
                "filter on device, site, an exact collected_date, or a collected_at range"
            )
        return [self._to_row(table, doc) for doc in docs]

    @staticmethod
    def _to_row(table: Table, doc: dict[str, Any]) -> dict[str, str]:
        # "rowid" is not a declared column; osquery uses it as the SQLite rowid.
        row: dict[str, str] = {"rowid": str(row_id(doc))}
        for name, value in doc.items():
            column = table.column(name)
            if column is None or value is None:
                continue  # missing values become SQL NULL
            if isinstance(value, (dict, list)):
                row[name] = json.dumps(value, default=str, sort_keys=True)
            else:
                row[name] = column.type.from_mongo(value)
        return row

