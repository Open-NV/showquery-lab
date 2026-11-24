"""HTTP API: table schema for the query builder, and a read-only SQL endpoint."""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .catalog import SAFE_BUILTIN_TABLES, TABLES
from .config import Settings, get_settings
from .osquery_client import OsqueryClient, OsqueryQueryError, OsqueryUnavailable
from .sql_guard import SqlValidationError, prepare

log = logging.getLogger("showquery.api")


# ---- models -----------------------------------------------------------------------------

class ColumnSchema(BaseModel):
    name: str
    type: str = Field(description="SQL type as osquery reports it")
    kind: str = Field(description="'text' | 'number' | 'epoch' (drives builder widgets)")
    description: str
    values: list[str] = []


class ExampleQuery(BaseModel):
    title: str
    sql: str


class TableSchema(BaseModel):
    name: str
    description: str
    columns: list[ColumnSchema]
    examples: list[ExampleQuery]


class SchemaResponse(BaseModel):
    tables: list[TableSchema]
    builtin_tables: list[str]
    max_result_rows: int


class QueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=20_000)


class QueryResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, str | None]]
    row_count: int
    truncated: bool
    elapsed_ms: float


# ---- dependencies -----------------------------------------------------------------------

@lru_cache
def get_osquery_client() -> OsqueryClient:
    settings = get_settings()
    return OsqueryClient(settings.osquery_socket, timeout_s=settings.osquery_timeout_s)


SettingsDep = Annotated[Settings, Depends(get_settings)]
ClientDep = Annotated[OsqueryClient, Depends(get_osquery_client)]


# ---- app --------------------------------------------------------------------------------

def _kind(sql_type: str) -> str:
    return {"TEXT": "text", "EPOCH": "epoch"}.get(sql_type, "number")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="showquery", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
    )

    @app.get("/api/health")
    def health(client: ClientDep) -> dict[str, str]:
        if not client.ping():
            raise HTTPException(503, "osquery is not reachable")
        return {"status": "ok"}

    @app.get("/api/schema", response_model=SchemaResponse)
    def schema(settings: SettingsDep) -> SchemaResponse:
        return SchemaResponse(
            tables=[
                TableSchema(
                    name=t.name,
                    description=t.description,
                    columns=[
                        ColumnSchema(
                            name=c.name,
                            type=c.type.sql_type,
                            kind=_kind(c.type.value),
                            description=c.description,
                            values=list(c.values),
                        )
                        for c in t.columns
                    ],
                    examples=[ExampleQuery(title=title, sql=sql) for title, sql in t.examples],
                )
                for t in TABLES
            ],
            builtin_tables=list(SAFE_BUILTIN_TABLES),
            max_result_rows=settings.api_max_result_rows,
        )

    # Plain `def`: FastAPI runs it in a worker thread, so the blocking thrift call
    # doesn't stall the event loop.
    @app.post("/api/query", response_model=QueryResponse)
    def query(body: QueryRequest, settings: SettingsDep, client: ClientDep) -> QueryResponse:
        try:
            prepared = prepare(body.sql, settings.api_max_result_rows)
        except SqlValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

        started = time.perf_counter()
        try:
            result = client.query(prepared.wrapped)
        except OsqueryQueryError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OsqueryUnavailable as exc:
            log.error("osquery unavailable: %s", exc)
            raise HTTPException(503, "osquery is not reachable") from exc
        elapsed_ms = (time.perf_counter() - started) * 1000

        truncated = len(result.rows) > prepared.row_cap
        rows = result.rows[: prepared.row_cap]
        log.info("query rows=%d truncated=%s ms=%.1f", len(rows), truncated, elapsed_ms)
        return QueryResponse(
            columns=result.columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=round(elapsed_ms, 1),
        )

    # Serve the built UI from the same origin when it exists (`npm run build` in web/).
    dist = Path(settings.web_dist) if settings.web_dist else (
        Path(__file__).resolve().parents[2] / "web" / "dist"
    )
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")

    return app


def main() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Run the showquery API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(create_app(), host=args.host, port=args.port)



if __name__ == "__main__":
    main()
