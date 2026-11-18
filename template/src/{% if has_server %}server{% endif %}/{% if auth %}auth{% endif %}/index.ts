import "server-only";

export { AuthConfigError, loadAuthConfig, type AuthConfig } from "./config";
export { AuthenticationError, UnsafeRuntimeRoleError } from "./errors";
export { createAuth, getAuth, resetAuth, type Auth } from "./auth";
export { authHandler } from "./handler";
export { withAuthentication } from "./guard";
export {
  principalFromHeaders,
  requirePrincipal,
  sessionFromHeaders,
  userIdOf,
  type Principal,
  type SessionView,
} from "./principal";
export { assertRuntimeRole, resetRuntimeRoleCheck } from "./runtime-role";
export { withPrincipal, type OwnerScope } from "./scope";
