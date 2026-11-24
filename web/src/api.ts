import type { QueryResult, Schema } from "./types";

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError("Can't reach the showquery API. Check that it's running (scripts/stack.sh up).");
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : response.statusText;
    throw new ApiError(detail);
  }
  return body as T;
}

export const fetchSchema = () => request<Schema>("/api/schema");

export const runQuery = (sql: string) =>
  request<QueryResult>("/api/query", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ sql }),
  });
