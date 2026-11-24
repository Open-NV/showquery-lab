"""Thin, thread-safe client for running SQL on osqueryd over its extensions socket."""

from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass

from osquery import ExtensionClient
from thrift.Thrift import TException


class OsqueryUnavailable(RuntimeError):
    """osqueryd (or the socket) can't be reached."""


class OsqueryQueryError(ValueError):
    """osquery rejected or failed the query (syntax error, unknown table, scan limit...)."""


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[dict[str, str]]


class OsqueryClient:
    def __init__(self, socket_path: str, timeout_s: float = 5.0) -> None:
        self._socket_path = socket_path
        self._timeout_s = timeout_s
        self._lock = threading.Lock()  # a thrift client is not safe for concurrent use
        self._client: ExtensionClient | None = None

    def query(self, sql: str) -> QueryResult:
        with self._lock:
            try:
                return self._query_once(sql)
            except (TException, OSError):
                # Socket went stale (osqueryd restarted); reconnect once.
                self._reset()
                try:
                    return self._query_once(sql)
                except (TException, OSError) as exc:
                    self._reset()
                    raise OsqueryUnavailable(f"osquery socket {self._socket_path}: {exc}") from exc

    def ping(self) -> bool:
        with self._lock:
            try:
                return self._manager().ping().code == 0
            except (TException, OSError, OsqueryUnavailable):
                self._reset()
                return False

    def _query_once(self, sql: str) -> QueryResult:
        manager = self._manager()
        result = manager.query(sql)
        if result.status.code != 0:
            raise OsqueryQueryError(result.status.message)
        # query() returns rows as unordered maps; getQueryColumns() gives SELECT order.
        columns_result = manager.getQueryColumns(sql)
        columns = [name for col in columns_result.response for name in col]
        return QueryResult(columns=columns, rows=list(result.response))

    def _manager(self):  # noqa: ANN202 - generated thrift type
        if self._client is None:
            client = ExtensionClient(path=self._socket_path)
            if not client.open(timeout=self._timeout_s):
                raise OsqueryUnavailable(f"cannot open osquery socket {self._socket_path}")
            self._client = client
        return self._client.extension_manager_client()

    def _reset(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
        self._client = None
