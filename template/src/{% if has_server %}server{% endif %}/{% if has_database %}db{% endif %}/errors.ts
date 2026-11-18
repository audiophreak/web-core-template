import "server-only";

export type DatabaseErrorCode =
  | "not_found"
  | "conflict"
  | "constraint_violation"
  | "invalid_input"
  | "timeout"
  | "unavailable"
  | "unexpected";

const MESSAGES: Record<DatabaseErrorCode, string> = {
  not_found: "The record does not exist",
  conflict: "The record conflicts with an existing one",
  constraint_violation: "The data breaks a database rule",
  invalid_input: "The input is not valid",
  timeout: "The database took too long to answer",
  unavailable: "The database is unavailable",
  unexpected: "The database operation failed",
};

/**
 * What every data-access function throws. The message is fixed text: it never contains the SQL, its
 * parameters, the connection string or the driver's detail (which can quote row data), so it is safe
 * to log or to turn into an API problem. `sqlState` is the five-character PostgreSQL code, if any.
 */
export class DatabaseError extends Error {
  constructor(
    readonly code: DatabaseErrorCode,
    message: string = MESSAGES[code],
    readonly sqlState?: string,
  ) {
    super(message);
    this.name = "DatabaseError";
  }
}

const CONNECTION_CODES = new Set([
  "ECONNREFUSED",
  "ECONNRESET",
  "ENOTFOUND",
  "EAI_AGAIN",
  "EHOSTUNREACH",
  "ENETUNREACH",
  "EPIPE",
]);

/** Classifies anything thrown while talking to PostgreSQL. Existing DatabaseErrors pass through. */
export function toDatabaseError(error: unknown): DatabaseError {
  if (error instanceof DatabaseError) return error;
  const { code, message } = (error ?? {}) as {
    code?: unknown;
    message?: unknown;
  };
  const state = typeof code === "string" ? code : undefined;
  if (state === "23505") return new DatabaseError("conflict", undefined, state);
  if (state === "57014") return new DatabaseError("timeout", undefined, state);
  if (state && /^(22021|22P02|22001|22003|22007|22008)$/.test(state))
    return new DatabaseError("invalid_input", undefined, state);
  if (state && /^23/.test(state))
    return new DatabaseError("constraint_violation", undefined, state);
  if (state && (/^(08|28|53|57P)/.test(state) || state === "3D000"))
    return new DatabaseError("unavailable", undefined, state);
  if (state && CONNECTION_CODES.has(state))
    return new DatabaseError("unavailable", undefined, state);
  if (
    state === "ETIMEDOUT" ||
    (typeof message === "string" &&
      /timeout exceeded when trying to connect|Connection terminated due to connection timeout/i.test(
        message,
      ))
  ) {
    return new DatabaseError("timeout", undefined, state);
  }
  if (
    typeof message === "string" &&
    /Connection terminated|Client has encountered a connection error/i.test(
      message,
    )
  ) {
    return new DatabaseError("unavailable", undefined, state);
  }
  return new DatabaseError("unexpected", undefined, state);
}
