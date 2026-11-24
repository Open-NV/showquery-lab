import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const api = process.env.SHOWQUERY_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": api } },
  preview: { proxy: { "/api": api } },
});
