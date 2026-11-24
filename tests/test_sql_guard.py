import pytest

from showquery.sql_guard import SqlValidationError, prepare


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM interfaces",
        "select device from interfaces;",
        "  -- leading comment\nSELECT 1",
        "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
        "SELECT ';' AS semi, 'it''s' AS quoted",
        'SELECT "weird;name" FROM interfaces',
        "SELECT 1 /* inline ; comment */",
        "SELECT 1; -- trailing comment",
    ],
)
def test_accepts_single_read_queries(sql):
    prepared = prepare(sql, row_cap=10)
    assert prepared.wrapped.endswith("LIMIT 11")
    assert not prepared.sql.rstrip().endswith(";")


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("SELECT 1; SELECT 2", "single"),
        ("SELECT 1; ATTACH DATABASE '/tmp/x' AS x", "single"),
        ("ATTACH DATABASE '/tmp/x' AS x", "only SELECT"),
        ("PRAGMA table_info(interfaces)", "only SELECT"),
        ("DELETE FROM interfaces", "only SELECT"),
        ("/* hide */ DROP TABLE x", "only SELECT"),
        ("SELECT 'unterminated", "unterminated"),
        ("SELECT 1 /* never closed", "unterminated"),
        ("SELECT " + "1," * 10_000 + "1", "longer"),
    ],
)
def test_rejects_unsafe_or_invalid_sql(sql, message):
    with pytest.raises(SqlValidationError, match=message):
        prepare(sql, row_cap=10)


def test_wrapping_preserves_order_by_and_cte():
    prepared = prepare("WITH d AS (SELECT 1 AS n) SELECT n FROM d ORDER BY n DESC", row_cap=5)
    assert prepared.wrapped == (
        "SELECT * FROM (\nWITH d AS (SELECT 1 AS n) SELECT n FROM d ORDER BY n DESC\n) LIMIT 6"
    )
