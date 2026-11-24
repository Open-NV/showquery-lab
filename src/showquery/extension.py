"""osquery extension process: registers one osquery table per catalog Table.

Run it against a running osqueryd::

    showquery-extension --socket /path/to/osquery.sock

(The --socket/--timeout/--interval/--verbose flags are parsed by osquery-python.)
"""

from __future__ import annotations

import json
import logging
from typing import Any

import osquery
from osquery.extensions.ttypes import ExtensionResponse, ExtensionStatus

from . import __version__
from .catalog import TABLES, Table
from .config import get_settings
from .mongo_source import MongoSource, ScanLimitExceeded

log = logging.getLogger("showquery.extension")

# osquery-python only exports STRING/INTEGER constants, but the wire format is just the
# type name, so the catalog's SQL type is passed through directly.



class CatalogTablePlugin(osquery.TablePlugin):
    """Base plugin; concrete subclasses are generated per table by make_plugin()."""

    table: Table
    source: MongoSource

    def name(self) -> str:
        return self.table.name

    def columns(self) -> list[Any]:
        return [osquery.TableColumn(name=c.name, type=c.type.sql_type)
                for c in self.table.columns]

    def generate(self, context: dict[str, Any]) -> list[dict[str, str]]:
        return self.source.rows(self.table, context)

    def call(self, context: dict[str, Any]) -> ExtensionResponse:
        # osquery-python lets exceptions escape generate(), which surfaces to the user as an
        # opaque thrift error. Return a proper error status so the message reaches SQL callers.
        if context.get("action") != "generate":
            return super().call(context)
        try:
            ctx = json.loads(context.get("context", "{}"))
            rows = self.generate(ctx)
        except ScanLimitExceeded as exc:
            return _error(str(exc))
        except Exception as exc:  # noqa: BLE001 - report every failure to the caller
            log.exception("generate failed for %s", self.table.name)
            return _error(f"{self.table.name}: {type(exc).__name__}: {exc}")
        return ExtensionResponse(status=ExtensionStatus(code=0, message="OK"), response=rows)


def _error(message: str) -> ExtensionResponse:
    return ExtensionResponse(status=ExtensionStatus(code=1, message=message), response=[])


def make_plugin(table: Table, source: MongoSource) -> type[CatalogTablePlugin]:
    # osquery-python instantiates plugin classes itself (no constructor args), so each table
    # gets its own subclass carrying its configuration as class attributes.
    class_name = "".join(part.title() for part in table.name.split("_")) + "Table"
    return type(class_name, (CatalogTablePlugin,), {"table": table, "source": source})


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    source = MongoSource.from_uri(
        settings.mongo_uri,
        settings.mongo_db,
        settings.mongo_collection,
        max_scan_rows=settings.max_scan_rows,
        max_time_ms=settings.mongo_max_time_ms,
    )
    for table in TABLES:
        osquery.register_plugin(make_plugin(table, source))
    log.info("registering tables: %s", ", ".join(t.name for t in TABLES))
    osquery.start_extension(name="showquery", version=__version__)


if __name__ == "__main__":
    main()
