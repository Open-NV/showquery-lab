"""Validation for user-supplied SQL before it reaches osquery.

Defence in depth: osqueryd already exposes only the catalog tables (--enable_tables)
and extension tables are read-only. This module additionally makes sure the request
is exactly one SELECT/WITH statement (no ATTACH, PRAGMA, or stacked statements) and
caps how many rows come back.
"""

from __future__ import annotations

from dataclasses import dataclass


class SqlValidationError(ValueError):
    pass


MAX_SQL_LENGTH = 20_000
_ALLOWED_LEADING_KEYWORDS = ("select", "with")


@dataclass(frozen=True)
class PreparedQuery:
    sql: str  # the user's statement, normalised (no trailing semicolon or comments at ends)
    wrapped: str  # what is actually executed, with the row cap applied
    row_cap: int


def strip_comments_and_check_statements(sql: str) -> str:
    """Remove comments and reject multiple statements, respecting quoted text.

    Returns the SQL with comments replaced by spaces and any trailing ';' removed.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    statement_ended = False
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if ch in ("'", '"', "`", "["):
            close = "]" if ch == "[" else ch
            j = i + 1
            while j < n:
                if sql[j] == close:
                    # '' and "" are escaped quotes inside a literal / identifier
                    if close != "]" and j + 1 < n and sql[j + 1] == close:
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                raise SqlValidationError("unterminated quoted string or identifier")
            if statement_ended:
                raise SqlValidationError("only a single SQL statement is allowed")
            out.append(sql[i : j + 1])
            i = j + 1
            continue
        if ch == "-" and nxt == "-":
            j = sql.find("\n", i)
            i = n if j == -1 else j
            out.append(" ")
            continue
        if ch == "/" and nxt == "*":
            j = sql.find("*/", i + 2)
            if j == -1:
                raise SqlValidationError("unterminated /* comment")
            i = j + 2
            out.append(" ")
            continue
        if ch == ";":
            statement_ended = True
            i += 1
            continue
        if statement_ended and not ch.isspace():
            raise SqlValidationError("only a single SQL statement is allowed")
        out.append(ch)
        i += 1
    return "".join(out).strip()


def prepare(sql: str, row_cap: int) -> PreparedQuery:
    if not sql or not sql.strip():
        raise SqlValidationError("query is empty")
    if len(sql) > MAX_SQL_LENGTH:
        raise SqlValidationError(f"query is longer than {MAX_SQL_LENGTH} characters")

    cleaned = strip_comments_and_check_statements(sql)
    first_word = cleaned.split(None, 1)[0].lower() if cleaned else ""
    if first_word not in _ALLOWED_LEADING_KEYWORDS:
        raise SqlValidationError("only SELECT (or WITH ... SELECT) queries are allowed")

    # Fetch one extra row so callers can tell the result was truncated.
    wrapped = f"SELECT * FROM (\n{cleaned}\n) LIMIT {row_cap + 1}"
    return PreparedQuery(sql=cleaned, wrapped=wrapped, row_cap=row_cap)
