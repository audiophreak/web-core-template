import { describe, expect, it } from "vitest";
import { DatabaseConfigError, loadDatabaseConfig } from "@/server/db/config";

const URL = "postgresql://app:s3cr3t-pass@127.0.0.1:5432/app";

describe("loadDatabaseConfig", () => {
  it("reads the connection string and applies bounded defaults", () => {
    expect(loadDatabaseConfig({ DATABASE_URL: URL })).toEqual({
      connectionString: URL,
      poolMax: 5,
      connectTimeoutMs: 5_000,
      statementTimeoutMs: 10_000,
    });
  });

  it("accepts postgres:// and explicit tuning within its bounds", () => {
    const config = loadDatabaseConfig({
      DATABASE_URL: "postgres://u@localhost/db",
      DATABASE_POOL_MAX: "20",
      DATABASE_CONNECT_TIMEOUT_MS: "100",
      DATABASE_STATEMENT_TIMEOUT_MS: "300000",
    });
    expect([
      config.poolMax,
      config.connectTimeoutMs,
      config.statementTimeoutMs,
    ]).toEqual([20, 100, 300_000]);
  });

  it.each([
    ["a missing URL", {}, ["DATABASE_URL"]],
    ["an empty URL", { DATABASE_URL: "" }, ["DATABASE_URL"]],
    [
      "a URL that is not PostgreSQL",
      { DATABASE_URL: "mysql://u:s3cr3t-pass@h/db" },
      ["DATABASE_URL"],
    ],
    [
      "text that is not a URL",
      { DATABASE_URL: "s3cr3t-pass" },
      ["DATABASE_URL"],
    ],
    [
      "a pool of zero",
      { DATABASE_URL: URL, DATABASE_POOL_MAX: "0" },
      ["DATABASE_POOL_MAX"],
    ],
    [
      "a pool over the limit",
      { DATABASE_URL: URL, DATABASE_POOL_MAX: "21" },
      ["DATABASE_POOL_MAX"],
    ],
    [
      "a pool that is not a number",
      { DATABASE_URL: URL, DATABASE_POOL_MAX: "many" },
      ["DATABASE_POOL_MAX"],
    ],
    [
      "an empty pool value",
      { DATABASE_URL: URL, DATABASE_POOL_MAX: "" },
      ["DATABASE_POOL_MAX"],
    ],
    [
      "a connect timeout that is too small",
      { DATABASE_URL: URL, DATABASE_CONNECT_TIMEOUT_MS: "5" },
      ["DATABASE_CONNECT_TIMEOUT_MS"],
    ],
    [
      "an unbounded statement timeout",
      { DATABASE_URL: URL, DATABASE_STATEMENT_TIMEOUT_MS: "999999999" },
      ["DATABASE_STATEMENT_TIMEOUT_MS"],
    ],
    [
      "several problems at once",
      { DATABASE_POOL_MAX: "0", DATABASE_STATEMENT_TIMEOUT_MS: "1" },
      ["DATABASE_URL", "DATABASE_POOL_MAX", "DATABASE_STATEMENT_TIMEOUT_MS"],
    ],
  ])("rejects %s and names only the variables", (_label, env, variables) => {
    let error: unknown;
    try {
      loadDatabaseConfig(env);
    } catch (caught) {
      error = caught;
    }
    expect(error).toBeInstanceOf(DatabaseConfigError);
    expect((error as DatabaseConfigError).variables).toEqual(variables);
    expect((error as Error).message).not.toContain("s3cr3t-pass");
  });
});
