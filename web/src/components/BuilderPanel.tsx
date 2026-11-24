import { QueryBuilder, type RuleGroupType } from "react-querybuilder";
import { builderFields, type BuilderState } from "../buildSql";
import type { TableSchema } from "../types";

interface Props {
  table: TableSchema;
  state: BuilderState;
  onChange: (next: BuilderState) => void;
  sql: string;
  running: boolean;
  onRun: () => void;
  onEditAsSql: () => void;
}

const translations = {
  addRule: { label: "Add filter", title: "Add filter" },
  addGroup: { label: "Add group", title: "Add a nested group of filters" },
  removeRule: { label: "Remove", title: "Remove filter" },
  removeGroup: { label: "Remove group", title: "Remove group" },
};

export function BuilderPanel({ table, state, onChange, sql, running, onRun, onEditAsSql }: Props) {
  const fields = builderFields(table);
  const set = (patch: Partial<BuilderState>) => onChange({ ...state, ...patch });
  const toggleColumn = (name: string) =>
    set({ columns: state.columns.includes(name) ? state.columns.filter((c) => c !== name) : [...state.columns, name] });

  return (
    <div className="builder">
      <div className="builder-step">
        <h3 className="step-title">Show these columns</h3>
        <div className="chips" role="group" aria-label="Columns to show">
          {table.columns
            .filter((c) => c.name !== "parsed")
            .map((c) => (
              <button
                key={c.name}
                className="chip"
                aria-pressed={state.columns.includes(c.name)}
                title={c.description}
                onClick={() => toggleColumn(c.name)}
              >
                {c.name}
              </button>
            ))}
        </div>
        {state.columns.length === 0 && <p className="step-note">No columns picked, so every column is shown.</p>}
      </div>

      <div className="builder-step">
        <h3 className="step-title">Where</h3>
        <QueryBuilder
          fields={fields}
          query={state.query}
          onQueryChange={(query: RuleGroupType) => set({ query })}
          translations={translations}
          resetOnFieldChange
          controlClassnames={{ queryBuilder: "qb" }}
        />
      </div>

      <div className="builder-step builder-row">
        <label className="field">
          <span>Sort by</span>
          <select value={state.orderBy ?? ""} onChange={(e) => set({ orderBy: e.target.value || null })}>
            <option value="">No sorting</option>
            {table.columns.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Direction</span>
          <select
            value={state.orderDir}
            disabled={!state.orderBy}
            onChange={(e) => set({ orderDir: e.target.value as "ASC" | "DESC" })}
          >
            <option value="DESC">Newest / largest first</option>
            <option value="ASC">Oldest / smallest first</option>
          </select>
        </label>
        <label className="field field-narrow">
          <span>Row limit</span>
          <input
            type="number"
            min={1}
            value={state.limit}
            onChange={(e) => set({ limit: Math.max(1, Number(e.target.value) || 1) })}
          />
        </label>
      </div>

      <div className="sql-preview">
        <div className="sql-preview-head">
          <h3 className="step-title">SQL this runs</h3>
          <button className="link-button" onClick={onEditAsSql}>
            Edit as SQL
          </button>
        </div>
        <pre>
          <code>{sql}</code>
        </pre>
      </div>

      <div className="actions">
        <button className="run" onClick={onRun} disabled={running}>
          {running ? "Running…" : "Run query"}
        </button>
      </div>
    </div>
  );
}
