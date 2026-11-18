import {
  createItemRequestSchema,
  itemListResponseSchema,
  itemResponseSchema,
  listItemsQuerySchema,
} from "@/lib/schemas/v1";
import { readJson, toFieldErrors } from "@/server/api/request";
import { json, methodNotAllowed, problem } from "@/server/api/responses";
import { withAuthentication, withPrincipal } from "@/server/auth";
import { itemProblem, toItemResponse } from "@/server/items/http";
import { createItem, listItems } from "@/server/items/repository";

const MAX_BODY_BYTES = 8 * 1024;

/** GET /api/v1/items?limit=&offset=: the signed-in caller's items, newest first. */
export const GET = withAuthentication(async (request, principal) => {
  const query = listItemsQuerySchema.safeParse(
    Object.fromEntries(new URL(request.url).searchParams),
  );
  if (!query.success)
    return problem("validation_failed", { errors: toFieldErrors(query.error) });
  try {
    const items = await withPrincipal(principal, (scope) =>
      listItems(scope, query.data),
    );
    return json(itemListResponseSchema, {
      items: items.map(toItemResponse),
      limit: query.data.limit,
      offset: query.data.offset,
    });
  } catch (error) {
    return itemProblem(error);
  }
});

/** POST /api/v1/items: creates an item owned by the signed-in caller. */
export const POST = withAuthentication(async (request, principal) => {
  const body = await readJson(request, MAX_BODY_BYTES);
  if (!body.ok) return body.response;
  const parsed = createItemRequestSchema.safeParse(body.data);
  if (!parsed.success)
    return problem("validation_failed", {
      errors: toFieldErrors(parsed.error),
    });
  try {
    const item = await withPrincipal(principal, (scope) =>
      createItem(scope, parsed.data),
    );
    return json(itemResponseSchema, toItemResponse(item), {
      status: 201,
      headers: { Location: `/api/v1/items/${item.id}` },
    });
  } catch (error) {
    return itemProblem(error);
  }
});

const notAllowed = methodNotAllowed(["GET", "POST", "OPTIONS"]);
export const PUT = notAllowed;
export const PATCH = notAllowed;
export const DELETE = notAllowed;
