import { describe, expect, it } from "vitest";
import { buildSql, emptyBuilderState, whereClause, builderFields } from "./buildSql";
import type { TableSchema } from "./types";

const interfaces: TableSchema = {
  name: "interfaces",
  description: "",
  examples: [],
  columns: [
    { name: "device", type: "TEXT", kind: "text", description: "", values: [] },
    { name: "collected_date", type: "TEXT", kind: "text", description: "", values: [] },
    { name: "collected_at", type: "BIGINT", kind: "epoch", description: "", values: [] },
    { name: "status", type: "TEXT", kind: "text", description: "", values: ["connected", "err-disabled"] },
    { name: "vlan", type: "TEXT", kind: "text", description: "", values: [] },
    { name: "uptime_seconds", type: "INTEGER", kind: "number", description: "", values: [] },
  ],
};
const fields = builderFields(interfaces);
const rule = (field: string, operator: string, value: unknown) => ({ field, operator, value });

describe("whereClause", () => {
  it("is empty when there are no rules", () => {
    expect(whereClause({ combinator: "and", rules: [] }, fields)).toBe("");
  });

  it("escapes values to prevent breaking out of string literals", () => {
    const sql = whereClause({ combinator: "and", rules: [rule("device", "=", "x' OR '1'='1")] }, fields);
    expect(sql).toBe("(device = 'x'' OR ''1''=''1')");
  });

  it("rewrites date ranges onto the numeric epoch column so they push down", () => {
    const q = (operator: string, value: unknown) =>
      whereClause({ combinator: "and", rules: [rule("collected_date", operator, value)] }, fields);
    expect(q(">=", "2026-11-20")).toBe("(collected_at >= strftime('%s', '2026-11-20'))");
    expect(q(">", "2026-11-20")).toBe("(collected_at >= strftime('%s', '2026-11-20', '+1 day'))");
    expect(q("<", "2026-11-20")).toBe("(collected_at < strftime('%s', '2026-11-20'))");
    expect(q("<=", "2026-11-20")).toBe("(collected_at < strftime('%s', '2026-11-20', '+1 day'))");
    expect(q("between", "2026-11-01,2026-11-07")).toBe(
      "((collected_at >= strftime('%s', '2026-11-01') AND collected_at < strftime('%s', '2026-11-07', '+1 day')))",
    );
    expect(q("=", "2026-11-20")).toBe("(collected_date = '2026-11-20')");
  });

  it("falls back to the plain rule when the date isn't a valid day", () => {
    const sql = whereClause({ combinator: "and", rules: [rule("collected_date", ">=", "2026-11-2' --")] }, fields);
    expect(sql).toBe("(collected_date >= '2026-11-2'' --')");
  });

  it("emits numbers unquoted for numeric fields", () => {
    const sql = whereClause({ combinator: "and", rules: [rule("uptime_seconds", "<", "86400")] }, fields);
    expect(sql).toBe("(uptime_seconds < 86400)");
  });

  it("supports nested groups and multi-value operators", () => {
    const sql = whereClause(
      {
        combinator: "or",
        rules: [
          rule("status", "in", ["err-disabled", "connected"]),
          { combinator: "and", rules: [rule("vlan", "=", "30"), rule("device", "beginsWith", "dfw")] },
        ],
      },
      fields,
    );
    expect(sql).toBe("(status in ('err-disabled', 'connected') or (vlan = '30' and device like 'dfw%'))");
  });
});

describe("buildSql", () => {
  it("assembles select, where, order and limit", () => {
    const state = {
      ...emptyBuilderState(interfaces),
      columns: ["device", "status", "not_a_column"],
      query: { combinator: "and", rules: [rule("status", "=", "err-disabled")] },
    };
    expect(buildSql(state, interfaces)).toBe(
      "SELECT device, status\nFROM interfaces\nWHERE (status = 'err-disabled')\nORDER BY collected_date DESC\nLIMIT 200;",
    );
  });

  it("selects everything when no columns are chosen", () => {
    expect(buildSql({ ...emptyBuilderState(interfaces), orderBy: null, limit: 0 }, interfaces)).toBe(
      "SELECT *\nFROM interfaces;",
    );
  });
});

describe("builderFields", () => {
  it("hides raw JSON and the epoch twin of collected_date", () => {
    expect(fields.map((f) => f.name)).not.toContain("collected_at");
  });

  it("uses dropdowns for columns with known values", () => {
    const status = fields.find((f) => f.name === "status")!;
    expect(status.values).toHaveLength(2);
  });
});
