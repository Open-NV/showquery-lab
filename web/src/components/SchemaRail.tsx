import type { Schema } from "../types";

interface Props {
  schema: Schema | null;
  activeTable: string | null;
  onSelectTable: (name: string) => void;
  onPickColumn: (name: string) => void;
  pickHint: string;
}

export function SchemaRail({ schema, activeTable, onSelectTable, onPickColumn, pickHint }: Props) {
  return (
    <nav className="rail" aria-label="Tables">
      <h2 className="rail-title">Tables</h2>
      <ul className="rail-tables">
        {schema?.tables.map((t) => {
          const active = t.name === activeTable;
          return (
            <li key={t.name} className={active ? "rail-table is-active" : "rail-table"}>
              <button className="rail-table-name" aria-expanded={active} onClick={() => onSelectTable(t.name)}>
                {t.name}
              </button>
              <p className="rail-table-desc">{t.description}</p>
              {active && (
                <>
                  <p className="rail-hint">{pickHint}</p>
                  <ul className="rail-columns">
                    {t.columns.map((c) => (
                      <li key={c.name}>
                        <button className="rail-column" title={c.description} onClick={() => onPickColumn(c.name)}>
                          <span className="rail-column-name">{c.name}</span>
                          <span className="rail-column-type">{c.type.toLowerCase()}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
