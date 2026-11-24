/**
 * Turns query-builder state into the SQL that is sent to osquery.
 *
 * One deliberate rewrite: osquery only pushes *range* filters down to the extension for
 * numeric columns. A rule like `collected_date >= 2026-11-20` would make the extension read
 * every day of history, so date ranges are expressed on the numeric `collected_at` column
 * instead (same meaning, but index-backed in MongoDB).
 */
import {
  defaultRuleProcessorSQL,
  formatQuery,
  type Field,
  type RuleGroupType,
  type RuleProcessor,
} from "react-querybuilder";
import type { ColumnSchema, TableSchema } from "./types";

export interface BuilderState {
  table: string;
  columns: string[]; // empty = all columns
  query: RuleGroupType;
  orderBy: string | null;
  orderDir: "ASC" | "DESC";
  limit: number;
}

export const DATE_COLUMN = "collected_date";
export const EPOCH_COLUMN = "collected_at";
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;
const BARE_IDENTIFIER = /^[a-z_][a-z0-9_]*$/;

export function ident(name: string): string {
  return BARE_IDENTIFIER.test(name) ? name : `"${name.replace(/"/g, '""')}"`;
}

const startOf = (day: string) => `strftime('%s', '${day}')`;
const startOfNext = (day: string) => `strftime('%s', '${day}', '+1 day')`;

function asPair(value: unknown): [string, string] | null {
  const parts = Array.isArray(value) ? value.map(String) : String(value ?? "").split(",");
  const [a, b] = parts.map((p) => p.trim());
  return a && b && ISO_DAY.test(a) && ISO_DAY.test(b) ? [a, b] : null;
}

/** Rewrites date-range rules on collected_date into collected_at epoch ranges. */
export const dateAwareRuleProcessor: RuleProcessor = (rule, options) => {
  if (rule.field === DATE_COLUMN) {
    const day = String(rule.value ?? "").trim();
    const at = EPOCH_COLUMN;
    if (ISO_DAY.test(day)) {
      switch (rule.operator) {
        case ">=": return `${at} >= ${startOf(day)}`;
        case ">": return `${at} >= ${startOfNext(day)}`;
        case "<": return `${at} < ${startOf(day)}`;
        case "<=": return `${at} < ${startOfNext(day)}`;
      }
    }
    const pair = rule.operator === "between" || rule.operator === "notBetween" ? asPair(rule.value) : null;
    if (pair) {
      const range = `(${at} >= ${startOf(pair[0])} AND ${at} < ${startOfNext(pair[1])})`;
      return rule.operator === "between" ? range : `NOT ${range}`;
    }
  }
  return defaultRuleProcessorSQL(rule, options);
};

const TEXT_OPERATORS = ["=", "!=", "contains", "beginsWith", "endsWith", "doesNotContain", "in", "notIn"];
const ENUM_OPERATORS = ["=", "!=", "in", "notIn"];
const NUMBER_OPERATORS = ["=", "!=", "<", "<=", ">", ">=", "between"];
const DATE_OPERATORS = ["=", ">=", ">", "<=", "<", "between"];

/** Columns hidden from the builder: raw JSON, and the epoch twin of collected_date. */
const BUILDER_HIDDEN = new Set(["parsed", EPOCH_COLUMN]);

export function builderFields(table: TableSchema): Field[] {
  return table.columns.filter((c) => !BUILDER_HIDDEN.has(c.name)).map(toField);
}

function toField(column: ColumnSchema): Field {
  const base = { name: column.name, label: column.name, title: column.description };
  if (column.name === DATE_COLUMN) {
    return { ...base, inputType: "date", operators: pick(DATE_OPERATORS), defaultOperator: ">=" };
  }
  if (column.name === "output") {
    return { ...base, operators: pick(["contains", "doesNotContain"]), defaultOperator: "contains" };
  }
  if (column.kind !== "text") {
    return { ...base, inputType: "number", operators: pick(NUMBER_OPERATORS) };
  }
  if (column.values.length) {
    return {
      ...base,
      valueEditorType: (operator: string) => (operator === "in" || operator === "notIn" ? "multiselect" : "select"),
      values: column.values.map((v) => ({ name: v, label: v })),
      defaultValue: column.values[0],
      operators: pick(ENUM_OPERATORS),
    };
  }
  return { ...base, operators: pick(TEXT_OPERATORS) };
}

const OPERATOR_LABELS: Record<string, string> = {
  "=": "is", "!=": "is not", "<": "less than", "<=": "at most", ">": "greater than",
  ">=": "at least", contains: "contains", doesNotContain: "doesn't contain",
  beginsWith: "starts with", endsWith: "ends with", in: "is any of", notIn: "is none of",
  between: "between",
};
const DATE_LABELS: Record<string, string> = { ">=": "on or after", ">": "after", "<=": "on or before", "<": "before", "=": "on" };

function pick(names: string[]) {
  const labels = names === DATE_OPERATORS ? { ...OPERATOR_LABELS, ...DATE_LABELS } : OPERATOR_LABELS;
  return names.map((name) => ({ name, value: name, label: labels[name] ?? name }));
}

export function whereClause(query: RuleGroupType, fields: Field[]): string {
  const sql = formatQuery(query, {
    format: "sql",
    fields,
    parseNumbers: "strict-limited", // only parse values of number-typed fields
    ruleProcessor: dateAwareRuleProcessor,
    fallbackExpression: "",
  });
  // Empty groups format as "(1 = 1)"; treat them as no filter.
  return sql === "(1 = 1)" || sql === "()" ? "" : sql;
}

export function buildSql(state: BuilderState, table: TableSchema): string {
  const known = new Set(table.columns.map((c) => c.name));
  const columns = state.columns.filter((c) => known.has(c));
  const lines = [`SELECT ${columns.length ? columns.map(ident).join(", ") : "*"}`, `FROM ${ident(table.name)}`];
  const where = whereClause(state.query, builderFields(table));
  if (where) lines.push(`WHERE ${where}`);
  if (state.orderBy && known.has(state.orderBy)) lines.push(`ORDER BY ${ident(state.orderBy)} ${state.orderDir}`);
  if (state.limit > 0) lines.push(`LIMIT ${Math.floor(state.limit)}`);
  return lines.join("\n") + ";";
}

export function emptyBuilderState(table: TableSchema): BuilderState {
  const defaults = ["device", "collected_date"].filter((c) => table.columns.some((col) => col.name === c));
  return {
    table: table.name,
    columns: [],
    query: { combinator: "and", rules: [] },
    orderBy: defaults.includes("collected_date") ? "collected_date" : null,
    orderDir: "DESC",
    limit: 200,
  };
}
