import { defineConfig } from "vitest/config";

export default defineConfig({
  // Resolve the "@/*" import alias from tsconfig.json.
  resolve: { tsconfigPaths: true },
});
