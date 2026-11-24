export type ColumnKind = "text" | "number" | "epoch";

export interface ColumnSchema {
  name: string;
  type: string;
  kind: ColumnKind;
  description: string;
  values: string[];
}

export interface ExampleQuery {
  title: string;
  sql: string;
}

export interface TableSchema {
  name: string;
  description: string;
  columns: ColumnSchema[];
  examples: ExampleQuery[];
}

export interface Schema {
  tables: TableSchema[];
  builtin_tables: string[];
  max_result_rows: number;
}

export interface QueryResult {
  columns: string[];
  rows: Record<string, string | null>[];
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
}

export interface RunState {
  status: "idle" | "running" | "done" | "error";
  result?: QueryResult;
  error?: string;
  sql?: string;
}
