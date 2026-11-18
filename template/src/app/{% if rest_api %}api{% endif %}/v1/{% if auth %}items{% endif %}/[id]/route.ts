import { itemIdSchema } from "@/lib/schemas";
import { itemResponseSchema, updateItemRequestSchema } from "@/lib/schemas/v1";
import { readJson, toFieldErrors } from "@/server/api/request";
import { json, methodNotAllowed, problem } from "@/server/api/responses";
import { withAuthentication, withPrincipal } from "@/server/auth";
import { itemProblem, toItemResponse } from "@/server/items/http";
import { deleteItem, getItem, updateItem } from "@/server/items/repository";

const MAX_BODY_BYTES = 8 * 1024;

type Context = { params: Promise<{ id: string }> };

/** A malformed id cannot exist, so it is answered like a missing item. */
async function idOf(context: Context): Promise<string | null> {
  const parsed = itemIdSchema.safeParse((await context.params).id);
  return parsed.success ? parsed.data : null;
}

/** GET /api/v1/items/:id: one of the caller's items. Someone else's item is 404, like a missing one. */
export const GET = withAuthentication<Context>(
  async (_request, principal, context) => {
    const id = await idOf(context);
    if (!id) return problem("not_found");
    try {
      const item = await withPrincipal(principal, (scope) =>
        getItem(scope, id),
      );
      return json(itemResponseSchema, toItemResponse(item));
    } catch (error) {
      return itemProblem(error);
    }
  },
);

/** PATCH /api/v1/items/:id: changes the title and/or body of one of the caller's items. */
export const PATCH = withAuthentication<Context>(
  async (request, principal, context) => {
    const id = await idOf(context);
    if (!id) return problem("not_found");
    const body = await readJson(request, MAX_BODY_BYTES);
    if (!body.ok) return body.response;
    const parsed = updateItemRequestSchema.safeParse(body.data);
    if (!parsed.success)
      return problem("validation_failed", {
        errors: toFieldErrors(parsed.error),
      });
    try {
      const item = await withPrincipal(principal, (scope) =>
        updateItem(scope, id, parsed.data),
      );
      return json(itemResponseSchema, toItemResponse(item));
    } catch (error) {
      return itemProblem(error);
    }
  },
);

/** DELETE /api/v1/items/:id: 204 when one of the caller's items was deleted, otherwise 404. */
export const DELETE = withAuthentication<Context>(
  async (_request, principal, context) => {
    const id = await idOf(context);
    if (!id) return problem("not_found");
    try {
      const deleted = await withPrincipal(principal, (scope) =>
        deleteItem(scope, id),
      );
      if (!deleted) return problem("not_found");
      return new Response(null, {
        status: 204,
        headers: { "X-Content-Type-Options": "nosniff" },
      });
    } catch (error) {
      return itemProblem(error);
    }
  },
);

const notAllowed = methodNotAllowed(["GET", "PATCH", "DELETE", "OPTIONS"]);
export const POST = notAllowed;
export const PUT = notAllowed;
