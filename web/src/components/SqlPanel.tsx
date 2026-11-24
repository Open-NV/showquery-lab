import { sql as sqlLanguage, SQLite } from "@codemirror/lang-sql";
import { EditorView, keymap } from "@codemirror/view";
import { Prec } from "@codemirror/state";
import CodeMirror from "@uiw/react-codemirror";
import { forwardRef, useImperativeHandle, useMemo, useRef } from "react";
import type { Schema, TableSchema } from "../types";

export interface SqlPanelHandle {
  insert: (text: string) => void;
}

interface Props {
  schema: Schema;
  table: TableSchema;
  value: string;
  onChange: (value: string) => void;
  running: boolean;
  onRun: () => void;
}

const editorTheme = EditorView.theme({
  "&": { fontSize: "14px", backgroundColor: "var(--surface)" },
  ".cm-content": { fontFamily: "var(--mono)", padding: "12px 0" },
  ".cm-gutters": { backgroundColor: "var(--surface)", borderRight: "1px solid var(--rule)", color: "var(--ink-muted)" },
  "&.cm-focused": { outline: "none" },
});

export const SqlPanel = forwardRef<SqlPanelHandle, Props>(function SqlPanel(
  { schema, table, value, onChange, running, onRun },
  ref,
) {
  const view = useRef<EditorView | null>(null);
  const runRef = useRef(onRun);
  runRef.current = onRun;

  useImperativeHandle(ref, () => ({
    insert(text: string) {
      const v = view.current;
      if (!v) return;
      const { from, to } = v.state.selection.main;
      v.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + text.length } });
      v.focus();
    },
  }), []);

  const extensions = useMemo(() => {
    const completionSchema = Object.fromEntries(schema.tables.map((t) => [t.name, t.columns.map((c) => c.name)]));
    return [
      sqlLanguage({ dialect: SQLite, schema: completionSchema, upperCaseKeywords: true }),
      Prec.highest(keymap.of([{ key: "Mod-Enter", run: () => (runRef.current(), true) }])),
      editorTheme,
      EditorView.lineWrapping,
    ];
  }, [schema]);

  return (
    <div className="sql-panel">
      <div className="sql-toolbar">
        <label className="field">
          <span>Start from an example</span>
          <select
            value=""
            onChange={(e) => {
              if (e.target.value) onChange(e.target.value);
            }}
          >
            <option value="">Examples for {table.name}…</option>
            {table.examples.map((ex) => (
              <option key={ex.title} value={ex.sql}>
                {ex.title}
              </option>
            ))}
          </select>
        </label>
        <div className="sql-help">
          SQLite syntax, read-only. Date ranges are much faster on <code>collected_at</code>:
          <pre>collected_at &gt;= strftime('%s', '2026-11-20')</pre>
        </div>
      </div>
      <div className="editor">
        <CodeMirror
          value={value}
          onChange={onChange}
          extensions={extensions}
          onCreateEditor={(v) => (view.current = v)}
          basicSetup={{ foldGutter: false, highlightActiveLine: false }}
          minHeight="220px"
          aria-label="SQL query"
        />
      </div>
      <div className="actions">
        <span className="kbd-hint">
          <kbd>⌘</kbd>/<kbd>Ctrl</kbd> + <kbd>Enter</kbd> runs the query
        </span>
        <button className="run" onClick={onRun} disabled={running || !value.trim()}>
          {running ? "Running…" : "Run query"}
        </button>
      </div>
    </div>
  );
});
