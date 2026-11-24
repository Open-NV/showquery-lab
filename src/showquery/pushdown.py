"""Translate osquery's virtual-table constraints into a MongoDB aggregation pipeline.

osquery hands a table plugin the WHERE-clause constraints it can see, e.g.::

    {"constraints": [{"name": "device", "affinity": "TEXT",
                      "list": [{"op": 2, "expr": "dfw1-core-01"}]}],
     "colsUsed": ["device", "status"]}

Pushing these into Mongo keeps queries index-backed instead of scanning the whole
collection. Note that osquery only forwards *range* operators (<, >, ...) for numeric
columns: ``collected_date >= '...'`` arrives with no constraint, while the equivalent
``collected_at >= strftime('%s', '...')`` is pushed down.

SQLite re-applies every constraint to the rows we return, so a constraint we can't
translate is simply skipped, and pushing a *superset* filter is fine. Pushing a filter
that is narrower than SQLite's semantics would silently drop rows, so translations
must never be stricter than the SQL they come from.
"""

from __future__ import annotations

import hashlib
import re
from enum import IntEnum
from typing import Any

from .catalog import Column, ColumnType, Table


class Op(IntEnum):
    """osquery ConstraintOperator values (osquery/core/tables.h)."""

    EQUALS = 2
    GREATER_THAN = 4
    LESS_THAN_OR_EQUALS = 8
    LESS_THAN = 16
    GREATER_THAN_OR_EQUALS = 32
    MATCH = 64
    LIKE = 65
    GLOB = 66
    REGEXP = 67
    NOT_EQUALS = 68
    LIMIT = 73  # never pushed: SQLite may still have to filter rows we'd have cut off
    OFFSET = 74


_COMPARISONS = {
    Op.EQUALS: "$eq",
    Op.GREATER_THAN: "$gt",
    Op.LESS_THAN_OR_EQUALS: "$lte",
    Op.LESS_THAN: "$lt",
    Op.GREATER_THAN_OR_EQUALS: "$gte",
}


def like_to_regex(pattern: str) -> str:
    """SQLite LIKE (``%`` and ``_`` wildcards, no ESCAPE) as an anchored regex."""
    parts = []
    for ch in pattern:
        if ch == "%":
            parts.append(".*")
        elif ch == "_":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
    return "^" + "".join(parts) + "$"


def translate(column: Column, op: int, expr: str) -> dict[str, Any] | None:
    """Return a Mongo filter for one constraint, or None if it can't be pushed down."""
    try:
        op = Op(op)
    except ValueError:
        return None

    if op in _COMPARISONS:
        try:
            value = column.type.to_mongo(expr)
        except ValueError:
            return None
        return {column.path: {_COMPARISONS[op]: value}}

    if op is Op.NOT_EQUALS:
        # Mongo $ne also matches missing fields where SQL's != yields NULL (no match);
        # that's a superset, and SQLite re-checks every row, so the result is still exact.
        try:
            return {column.path: {"$ne": column.type.to_mongo(expr)}}
        except ValueError:
            return None

    if op is Op.LIKE and column.type is ColumnType.TEXT:
        # SQLite LIKE is case-insensitive for ASCII; on numeric columns it compares the
        # text form, which Mongo regex can't express, so only TEXT is pushed down.
        # DOTALL: SQLite's % matches newlines, which matters for multi-line raw output.
        return {column.path: {"$regex": like_to_regex(expr), "$options": "is"}}

    # GLOB / REGEXP / MATCH / LIMIT / OFFSET: left to SQLite.
    return None


ELEMENT_INDEX = "_element_index"


def row_id(doc: dict[str, Any]) -> int:
    """A stable, unique SQLite rowid for a result row.

    For ``a = 1 OR b = 2`` SQLite calls generate() once per branch and de-duplicates rows
    by rowid. osquery's default rowid is the row's position within each call, so rows
    from the second branch that share a position with first-branch rows are silently
    discarded. Returning a rowid derived from the document id (plus array index for
    unwound tables) makes the same logical row carry the same rowid in every branch.
    """
    key = f"{doc.get('_id')}:{doc.get(ELEMENT_INDEX, 0)}".encode()
    return int.from_bytes(hashlib.blake2b(key, digest_size=8).digest(), "big") >> 1  # >= 0


def build_pipeline(table: Table, context: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """Build the aggregation pipeline for one generate() call.

    Filters on document-level fields run before $unwind (so they can use indexes);
    filters on array-element fields run after it. ``limit`` should be the scan cap
    plus one so the caller can detect when it was exceeded.
    """
    before: list[dict[str, Any]] = [dict(table.base_filter)] if table.base_filter else []
    after: list[dict[str, Any]] = []

    for constraint in context.get("constraints", []):
        column = table.column(constraint.get("name", ""))
        if column is None:
            continue
        for item in constraint.get("list", []):
            clause = translate(column, item.get("op", 0), str(item.get("expr", "")))
            if clause is not None:
                (after if table.is_unwound(column) else before).append(clause)

    pipeline: list[dict[str, Any]] = [{"$match": _and(before)}]
    if table.unwind:
        unwind = {"path": "$" + table.unwind, "includeArrayIndex": ELEMENT_INDEX}
        pipeline.append({"$unwind": unwind})
        if after:
            pipeline.append({"$match": _and(after)})

    # Only fetch the columns the query uses: skipping the raw `output` text is the
    # single biggest win for most queries. _id (and the array index) are always kept
    # because they identify the row; see row_id().
    used = context.get("colsUsed") or []
    projection: dict[str, Any] = {"_id": 1}
    if table.unwind:
        projection[ELEMENT_INDEX] = 1
    projection.update({c.name: "$" + c.path for c in table.columns if c.name in used})
    pipeline.append({"$project": projection})
    pipeline.append({"$limit": limit})
    return pipeline


def _and(clauses: list[dict[str, Any]]) -> dict[str, Any]:
    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}
