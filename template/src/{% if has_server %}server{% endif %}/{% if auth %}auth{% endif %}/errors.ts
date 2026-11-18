import "server-only";

/** There is no valid session. API handlers turn it into the 401 problem; it carries no detail. */
export class AuthenticationError extends Error {
  constructor() {
    super("Authentication is required");
    this.name = "AuthenticationError";
  }
}

/**
 * The application is connected to the database with a role that is allowed to bypass the protections
 * (an owner, a superuser, a role that can create roles or databases, or one that bypasses row-level
 * security). The message is fixed text: it never names the role or the connection.
 */
export class UnsafeRuntimeRoleError extends Error {
  constructor() {
    super(
      "The application database role must not be an owner or an administrator: set DATABASE_RUNTIME_URL to the app_runtime role (npm run db:init)",
    );
    this.name = "UnsafeRuntimeRoleError";
  }
}
