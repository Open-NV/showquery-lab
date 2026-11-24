import type { RunState } from "../types";

// Port-LED language: values that describe link or session health get an indicator.
const LED: Record<string, "up" | "down" | "fault" | "off"> = {
  connected: "up",
  Established: "up",
  notconnect: "down",
  Idle: "fault",
  Active: "fault",
  Connect: "fault",
  "err-disabled": "fault",
  disabled: "off",
};
const LED_COLUMNS = new Set(["status", "state"]);

function toCsv(columns: string[], rows: Record<string, string | null>[]): string {
  const cell = (v: string | null | undefined) => {
    const s = v ?? "";
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns.map(cell).join(","), ...rows.map((r) => columns.map((c) => cell(r[c])).join(","))].join("\n");
}

function download(columns: string[], rows: Record<string, string | null>[]) {
  const blob = new Blob([toCsv(columns, rows)], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), { href: url, download: "showquery-results.csv" });
  a.click();
  URL.revokeObjectURL(url);
}

export function ResultsTable({ run, maxRows }: { run: RunState; maxRows: number }) {
  if (run.status === "idle") {
    return <p className="results-empty">Run a query to see results here.</p>;
  }
  if (run.status === "error") {
    return (
      <div className="results-error" role="alert">
        <h3>The query didn't run</h3>
        <p>{run.error}</p>
      </div>
    );
  }
  const result = run.result;
  if (!result) return <p className="results-empty">Running…</p>;

  const multiline = new Set(result.columns.filter((c) => result.rows.some((r) => (r[c] ?? "").includes("\n"))));

  return (
    <div className={run.status === "running" ? "results-body is-stale" : "results-body"}>
      <div className="results-meta">
        <p>
          <strong>{result.row_count.toLocaleString()}</strong> {result.row_count === 1 ? "row" : "rows"} in{" "}
          {result.elapsed_ms.toLocaleString()} ms
          {result.truncated && (
            <span className="truncated">. Showing the first {maxRows.toLocaleString()}; add filters to narrow it down.</span>
          )}
        </p>
        {result.row_count > 0 && (
          <button className="link-button" onClick={() => download(result.columns, result.rows)}>
            Download CSV
          </button>
        )}
      </div>
      {result.row_count === 0 ? (
        <p className="results-empty">No rows matched. Try loosening a filter or widening the date range.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {result.columns.map((c) => (
                  <th key={c} scope="col">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row, i) => (
                <tr key={i}>
                  {result.columns.map((c) => {
                    const value = row[c];
                    const led = LED_COLUMNS.has(c) && value ? LED[value] : undefined;
                    return (
                      <td key={c} className={multiline.has(c) ? "cell-pre" : undefined}>
                        {value == null ? (
                          <span className="null">null</span>
                        ) : led ? (
                          <span className={`led led-${led}`}>{value}</span>
                        ) : (
                          value
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
