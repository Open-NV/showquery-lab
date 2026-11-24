"""Table catalog: the single source of truth for what users can query.

Each Table maps rows of an osquery virtual table onto documents in the MongoDB
``show_commands`` collection. The osquery extension, the API's /schema endpoint and
the UI's query-builder fields are all generated from this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any


class ColumnType(str, Enum):
    TEXT = "TEXT"
    INTEGER = "INTEGER"
    DOUBLE = "DOUBLE"
    # Stored in Mongo as a BSON date, exposed to SQL as BIGINT unix seconds.
    EPOCH = "EPOCH"

    @property
    def sql_type(self) -> str:
        return "BIGINT" if self is ColumnType.EPOCH else self.value

    @property
    def is_numeric(self) -> bool:
        return self is not ColumnType.TEXT

    def to_mongo(self, expr: str) -> Any:
        """Convert an osquery constraint expression (always a string) to a Mongo value.

        Raises ValueError when the expression can't be represented, in which case the
        constraint is simply not pushed down (SQLite still applies it).
        """
        if self is ColumnType.TEXT:
            return expr
        number = float(expr)
        if self is ColumnType.EPOCH:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        if self is ColumnType.INTEGER and number.is_integer():
            return int(number)
        return number

    def from_mongo(self, value: Any) -> str:
        if self is ColumnType.EPOCH and isinstance(value, datetime):
            if value.tzinfo is None:  # pymongo returns naive UTC by default
                value = value.replace(tzinfo=timezone.utc)
            return str(int(value.timestamp()))
        return str(value)


@dataclass(frozen=True)
class Column:
    name: str
    type: ColumnType
    description: str
    source: str | None = None  # dotted Mongo path; defaults to the column name
    values: tuple[str, ...] = ()  # known values, rendered as a dropdown in the UI

    @property
    def path(self) -> str:
        return self.source or self.name


@dataclass(frozen=True)
class Table:
    name: str
    description: str
    columns: tuple[Column, ...]
    base_filter: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    unwind: str | None = None  # array path exploded into one row per element
    examples: tuple[tuple[str, str], ...] = ()  # (title, sql)

    def __post_init__(self) -> None:
        names = [c.name for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate column names in table {self.name}")

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)

    def is_unwound(self, column: Column) -> bool:
        """True when the column's value lives inside the unwound array."""
        return self.unwind is not None and column.path.startswith(self.unwind + ".")


PLATFORMS = ("arista_eos", "cisco_ios", "cisco_nxos")
ROLES = ("core", "dist", "access", "edge")
SITES = ("dfw1", "dfw2", "iah1", "aus1")


def _snapshot_columns() -> tuple[Column, ...]:
    """Columns every table shares: which device, and when the snapshot was taken."""
    return (
        Column("device", ColumnType.TEXT, "Device hostname"),
        Column("site", ColumnType.TEXT, "Site code", values=SITES),
        Column("role", ColumnType.TEXT, "Network role", values=ROLES),
        Column("platform", ColumnType.TEXT, "Netmiko-style platform", values=PLATFORMS),
        Column("mgmt_ip", ColumnType.TEXT, "Management IP"),
        Column("collected_date", ColumnType.TEXT, "Collection day (YYYY-MM-DD, UTC)"),
        Column("collected_at", ColumnType.EPOCH, "Collection time (unix seconds)"),
    )


SHOW_COMMANDS = Table(
    name="show_commands",
    description="One row per device, per day, per command, with the raw CLI output.",
    columns=(
        *_snapshot_columns(),
        Column("command", ColumnType.TEXT, "CLI command that was run",
               values=("show version", "show interfaces status", "show ip bgp summary")),
        Column("output", ColumnType.TEXT, "Raw command output"),
        Column("parsed", ColumnType.TEXT, "Parsed output as JSON (use json_extract)"),
    ),
    examples=(
        ("Raw output for one device today",
         "SELECT device, command, output FROM show_commands\n"
         "WHERE device = 'dfw1-core-01'\n"
         "  AND collected_date = (SELECT max(collected_date) FROM show_commands"
         " WHERE device = 'dfw1-core-01');"),
        ("Search raw output (grep across the fleet)",
         "SELECT device, collected_date FROM show_commands\n"
         "WHERE command = 'show version' AND output LIKE '%16.12%'\n"
         "ORDER BY collected_date DESC LIMIT 50;"),
    ),
)

DEVICE_VERSIONS = Table(
    name="device_versions",
    description="Parsed 'show version': OS, model, serial and uptime per device per day.",
    base_filter=MappingProxyType({"command": "show version"}),
    columns=(
        *_snapshot_columns(),
        Column("os_version", ColumnType.TEXT, "Running OS version", source="parsed.os_version"),
        Column("model", ColumnType.TEXT, "Hardware model", source="parsed.model"),
        Column("serial", ColumnType.TEXT, "Chassis serial number", source="parsed.serial"),
        Column("uptime_seconds", ColumnType.INTEGER, "Uptime in seconds",
               source="parsed.uptime_seconds"),
    ),
    examples=(
        ("Devices whose OS version changed during the month",
         "SELECT device, group_concat(DISTINCT os_version) AS versions\n"
         "FROM device_versions GROUP BY device\n"
         "HAVING count(DISTINCT os_version) > 1;"),
        ("Devices that rebooted in the last week",
         "SELECT device, collected_date, uptime_seconds FROM device_versions\n"
         "WHERE uptime_seconds < 86400\n"
         "  AND collected_date >= date((SELECT max(collected_date) FROM device_versions),"
         " '-7 days')\nORDER BY collected_date DESC;"),
    ),
)

INTERFACES = Table(
    name="interfaces",
    description="Parsed 'show interfaces status': one row per interface per device per day.",
    base_filter=MappingProxyType({"command": "show interfaces status"}),
    unwind="parsed.interfaces",
    columns=(
        *_snapshot_columns(),
        Column("interface", ColumnType.TEXT, "Interface name", source="parsed.interfaces.name"),
        Column("description", ColumnType.TEXT, "Interface description",
               source="parsed.interfaces.description"),
        Column("status", ColumnType.TEXT, "Link status", source="parsed.interfaces.status",
               values=("connected", "notconnect", "disabled", "err-disabled")),
        Column("vlan", ColumnType.TEXT, "Access VLAN, 'trunk' or 'routed'",
               source="parsed.interfaces.vlan"),
        Column("duplex", ColumnType.TEXT, "Duplex", source="parsed.interfaces.duplex"),
        Column("speed", ColumnType.TEXT, "Speed", source="parsed.interfaces.speed"),
    ),
    examples=(
        ("Err-disabled ports on the latest day",
         "SELECT device, interface, description FROM interfaces\n"
         "WHERE status = 'err-disabled'\n"
         "  AND collected_date = (SELECT max(collected_date) FROM device_versions);"),
        ("Ports that flapped most often this month",
         "SELECT device, interface, count(DISTINCT status) AS states,\n"
         "       sum(status != 'connected') AS down_days\n"
         "FROM interfaces WHERE role = 'access'\n"
         "GROUP BY device, interface HAVING states > 1\n"
         "ORDER BY down_days DESC LIMIT 25;"),
    ),
)

BGP_NEIGHBORS = Table(
    name="bgp_neighbors",
    description="Parsed 'show ip bgp summary': one row per BGP peer per device per day.",
    base_filter=MappingProxyType({"command": "show ip bgp summary"}),
    unwind="parsed.neighbors",
    columns=(
        *_snapshot_columns(),
        Column("neighbor", ColumnType.TEXT, "Peer address", source="parsed.neighbors.neighbor"),
        Column("remote_as", ColumnType.INTEGER, "Peer AS number",
               source="parsed.neighbors.remote_as"),
        Column("state", ColumnType.TEXT, "Session state", source="parsed.neighbors.state",
               values=("Established", "Idle", "Active", "Connect")),
        Column("prefixes_received", ColumnType.INTEGER, "Prefixes received (0 if not up)",
               source="parsed.neighbors.prefixes_received"),
        Column("uptime_seconds", ColumnType.INTEGER, "Session up/down time in seconds",
               source="parsed.neighbors.uptime_seconds"),
    ),
    examples=(
        ("Sessions not established on the latest day",
         "SELECT device, neighbor, remote_as, state FROM bgp_neighbors\n"
         "WHERE state != 'Established'\n"
         "  AND collected_date = (SELECT max(collected_date) FROM bgp_neighbors);"),
        ("Day-over-day prefix drops greater than 20%",
         "SELECT t.device, t.neighbor, t.collected_date,\n"
         "       y.prefixes_received AS before, t.prefixes_received AS after\n"
         "FROM bgp_neighbors t JOIN bgp_neighbors y\n"
         "  ON y.device = t.device AND y.neighbor = t.neighbor\n"
         " AND y.collected_date = date(t.collected_date, '-1 day')\n"
         "WHERE y.prefixes_received > 0\n"
         "  AND t.prefixes_received < y.prefixes_received * 0.8;"),
    ),
)

TABLES: tuple[Table, ...] = (SHOW_COMMANDS, DEVICE_VERSIONS, INTERFACES, BGP_NEIGHBORS)

# Built-in osquery tables users may also query. Everything else (processes, file, curl, ...)
# is disabled on the osqueryd side via --enable_tables so SQL can't touch the host.
SAFE_BUILTIN_TABLES: tuple[str, ...] = ("time",)


def table_by_name(name: str) -> Table | None:
    return next((t for t in TABLES if t.name == name), None)


def enabled_tables_flag() -> str:
    """Value for osqueryd's --enable_tables allowlist."""
    return ",".join([t.name for t in TABLES] + list(SAFE_BUILTIN_TABLES))
