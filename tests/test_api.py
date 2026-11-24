import pytest
from fastapi.testclient import TestClient

from showquery.api import create_app, get_osquery_client
from showquery.osquery_client import OsqueryQueryError, OsqueryUnavailable, QueryResult


class FakeClient:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.seen = result, error, []

    def query(self, sql):
        self.seen.append(sql)
        if self.error:
            raise self.error
        return self.result

    def ping(self):
        return self.error is None


@pytest.fixture
def make_client():
    def _make(fake):
        app = create_app()
        app.dependency_overrides[get_osquery_client] = lambda: fake
        return TestClient(app)
    return _make


def test_schema_lists_catalog_tables(make_client):
    body = make_client(FakeClient()).get("/api/schema").json()
    names = [t["name"] for t in body["tables"]]
    assert names == ["show_commands", "device_versions", "interfaces", "bgp_neighbors"]
    interfaces = body["tables"][2]
    status = next(c for c in interfaces["columns"] if c["name"] == "status")
    assert status["kind"] == "text" and "err-disabled" in status["values"]
    collected_at = next(c for c in interfaces["columns"] if c["name"] == "collected_at")
    assert collected_at == {**collected_at, "type": "BIGINT", "kind": "epoch"}


def test_query_wraps_sql_and_reports_truncation(make_client, monkeypatch):
    monkeypatch.setenv("SHOWQUERY_API_MAX_RESULT_ROWS", "2")
    from showquery.config import get_settings
    get_settings.cache_clear()
    rows = [{"device": f"r{i}"} for i in range(3)]
    fake = FakeClient(QueryResult(columns=["device"], rows=rows))
    response = make_client(fake).post("/api/query", json={"sql": "SELECT device FROM interfaces;"})
    get_settings.cache_clear()
    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == rows[:2] and body["truncated"] is True and body["row_count"] == 2
    assert fake.seen == ["SELECT * FROM (\nSELECT device FROM interfaces\n) LIMIT 3"]


@pytest.mark.parametrize(
    ("fake", "sql", "status"),
    [
        (FakeClient(), "DROP TABLE x", 400),
        (FakeClient(error=OsqueryQueryError("no such table: processes")), "SELECT 1", 400),
        (FakeClient(error=OsqueryUnavailable("down")), "SELECT 1", 503),
    ],
)
def test_query_errors_map_to_http_status(make_client, fake, sql, status):
    response = make_client(fake).post("/api/query", json={"sql": sql})
    assert response.status_code == status
    assert response.json()["detail"]


def test_health_reflects_osquery(make_client):
    assert make_client(FakeClient()).get("/api/health").status_code == 200
    down = make_client(FakeClient(error=OsqueryUnavailable("x")))
    assert down.get("/api/health").status_code == 503
