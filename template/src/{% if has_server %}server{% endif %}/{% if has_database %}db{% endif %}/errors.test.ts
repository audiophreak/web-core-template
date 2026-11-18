import { describe, expect, it } from "vitest";
import { DatabaseError, toDatabaseError } from "@/server/db/errors";

const pgError = (code: string) =>
  Object.assign(
    new Error(
      'duplicate key value violates unique constraint "x" Key (title)=(secret title) already exists',
    ),
    { code },
  );

describe("toDatabaseError", () => {
  it.each([
    ["23505", "conflict"],
    ["23502", "constraint_violation"],
    ["23503", "constraint_violation"],
    ["23514", "constraint_violation"],
    ["22P02", "invalid_input"],
    ["22021", "invalid_input"],
    ["22001", "invalid_input"],
    ["57014", "timeout"],
    ["57P01", "unavailable"],
    ["08006", "unavailable"],
    ["28P01", "unavailable"],
    ["53300", "unavailable"],
    ["3D000", "unavailable"],
    ["ECONNREFUSED", "unavailable"],
    ["ENOTFOUND", "unavailable"],
    ["ETIMEDOUT", "timeout"],
    ["42P01", "unexpected"],
    ["XX000", "unexpected"],
  ])("maps SQLSTATE or system code %s to %s", (code, expected) => {
    expect(toDatabaseError(pgError(code)).code).toBe(expected);
  });

  it("maps the driver's connection messages", () => {
    expect(
      toDatabaseError(new Error("timeout exceeded when trying to connect"))
        .code,
    ).toBe("timeout");
    expect(
      toDatabaseError(new Error("Connection terminated unexpectedly")).code,
    ).toBe("unavailable");
    expect(toDatabaseError("not even an error").code).toBe("unexpected");
    expect(toDatabaseError(undefined).code).toBe("unexpected");
  });

  it("never carries the driver's text, which can quote data, SQL or the connection string", () => {
    for (const code of ["23505", "28P01", "42P01", "ECONNREFUSED"]) {
      const error = toDatabaseError(
        Object.assign(
          new Error(
            "password authentication failed for user app at postgres://app:s3cr3t@host/db: Key (title)=(secret title)",
          ),
          { code },
        ),
      );
      expect(error.message).not.toMatch(
        /s3cr3t|secret title|postgres:|password|Key \(/,
      );
      expect(JSON.stringify(error)).not.toMatch(/s3cr3t|secret title/);
    }
  });

  it("passes DatabaseErrors through unchanged and keeps the SQLSTATE", () => {
    const original = new DatabaseError("not_found");
    expect(toDatabaseError(original)).toBe(original);
    expect(toDatabaseError(pgError("23505")).sqlState).toBe("23505");
  });
});
