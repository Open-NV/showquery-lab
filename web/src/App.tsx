import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, fetchSchema, runQuery } from "./api";
import { buildSql, emptyBuilderState, type BuilderState } from "./buildSql";
import { BuilderPanel } from "./components/BuilderPanel";
import { ResultsTable } from "./components/ResultsTable";
import { SchemaRail } from "./components/SchemaRail";
import type { SqlPanelHandle } from "./components/SqlPanel";
import { load, save } from "./storage";
import type { RunState, Schema } from "./types";

// The SQL editor (CodeMirror) is only downloaded when someone switches to it.
const SqlPanel = lazy(() => import("./components/SqlPanel").then((m) => ({ default: m.SqlPanel })));

type Mode = "builder" | "sql";

export default function App() {
  const [schema, setSchema] = useState<Schema | null>(null);
  const [schemaError, setSchemaError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>(() => (load("showquery.mode") === "sql" ? "sql" : "builder"));
  const [tableName, setTableName] = useState<string>("interfaces");
  const [builders, setBuilders] = useState<Record<string, BuilderState>>({});
  const [sql, setSql] = useState<string>(() => load("showquery.sql") ?? "");
  const [run, setRun] = useState<RunState>({ status: "idle" });
  const sqlPanel = useRef<SqlPanelHandle>(null);

  useEffect(() => {
    fetchSchema()
      .then((s) => {
        setSchema(s);
        if (!sql) setSql(s.tables.find((t) => t.name === "bgp_neighbors")?.examples[0]?.sql ?? "");
      })
      .catch((e: Error) => setSchemaError(e.message));
  }, []);

  useEffect(() => save("showquery.mode", mode), [mode]);
  useEffect(() => save("showquery.sql", sql), [sql]);

  const table = useMemo(() => schema?.tables.find((t) => t.name === tableName) ?? schema?.tables[0], [schema, tableName]);
  const builder = table ? (builders[table.name] ?? emptyBuilderState(table)) : null;
  const builtSql = table && builder ? buildSql(builder, table) : "";

  const updateBuilder = useCallback(
    (next: BuilderState) => setBuilders((all) => ({ ...all, [next.table]: next })),
    [],
  );

  const execute = useCallback(async (text: string) => {
    if (!text.trim()) return;
    setRun((r) => ({ ...r, status: "running", sql: text, error: undefined }));
    try {
      const result = await runQuery(text);
      setRun({ status: "done", result, sql: text });
    } catch (e) {
      setRun({ status: "error", sql: text, error: e instanceof ApiError ? e.message : String(e) });
    }
  }, []);

  const editAsSql = () => {
    setSql(builtSql);
    setMode("sql");
  };

  const onColumnPick = (column: string) => {
    if (mode === "sql") {
      sqlPanel.current?.insert(column);
      return;
    }
    if (!table || !builder) return;
    updateBuilder({
      ...builder,
      query: { ...builder.query, rules: [...builder.query.rules, { field: column, operator: "=", value: "" }] },
    });
  };

  if (schemaError) {
    return (
      <main className="fatal">
        <h1>showquery can't load the table list</h1>
        <p>{schemaError}</p>
      </main>
    );
  }

  return (
    <div className="shell">
      <header className="masthead">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-name">showquery</span>
          <span className="brand-scope">daily show-command snapshots</span>
        </div>
        <div className="mode-switch" role="tablist" aria-label="Query mode">
          <button role="tab" aria-selected={mode === "builder"} onClick={() => setMode("builder")}>
            Build a query
          </button>
          <button role="tab" aria-selected={mode === "sql"} onClick={() => setMode("sql")}>
            Write SQL
          </button>
        </div>
      </header>

      <SchemaRail
        schema={schema}
        activeTable={table?.name ?? null}
        onSelectTable={(name) => setTableName(name)}
        onPickColumn={onColumnPick}
        pickHint={mode === "sql" ? "Click a column to insert it" : "Click a column to filter on it"}
      />

      <section className="workspace" aria-label="Query">
        {!schema || !table || !builder ? (
          <p className="loading">Loading tables…</p>
        ) : mode === "builder" ? (
          <BuilderPanel
            table={table}
            state={builder}
            onChange={updateBuilder}
            sql={builtSql}
            running={run.status === "running"}
            onRun={() => execute(builtSql)}
            onEditAsSql={editAsSql}
          />
        ) : (
          <Suspense fallback={<p className="loading">Loading the SQL editor…</p>}>
          <SqlPanel
            ref={sqlPanel}
            schema={schema}
            table={table}
            value={sql}
            onChange={setSql}
            running={run.status === "running"}
            onRun={() => execute(sql)}
          />
          </Suspense>
        )}
      </section>

      <section className="results" aria-label="Results" aria-live="polite">
        <ResultsTable run={run} maxRows={schema?.max_result_rows ?? 0} />
      </section>
    </div>
  );
}
