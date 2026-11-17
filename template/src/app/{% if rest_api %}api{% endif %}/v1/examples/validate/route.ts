import {
  validateExampleRequestSchema,
  validateExampleResponseSchema,
} from "@/lib/schemas/v1";
import { readJson, toFieldErrors } from "@/server/api/request";
import {
  guarded,
  json,
  methodNotAllowed,
  problem,
} from "@/server/api/responses";

const MAX_BODY_BYTES = 8 * 1024;

/**
 * POST /api/v1/examples/validate: the pattern for every endpoint that accepts input. Read the body
 * defensively, `safeParse` it with the versioned schema, and answer with a bounded, explicitly
 * validated result. Nothing is stored and nothing is sent anywhere.
 */
export const POST = guarded(async (request: Request) => {
  const body = await readJson(request, MAX_BODY_BYTES);
  if (!body.ok) return body.response;

  const parsed = validateExampleRequestSchema.safeParse(body.data);
  if (!parsed.success) {
    return problem("validation_failed", {
      errors: toFieldErrors(parsed.error),
    });
  }

  const { title, priority, tags } = parsed.data;
  return json(validateExampleResponseSchema, {
    valid: true,
    titleLength: title.length,
    priority,
    tagCount: tags.length,
  });
});

const notAllowed = methodNotAllowed(["POST", "OPTIONS"]);
export const GET = notAllowed;
export const PUT = notAllowed;
export const PATCH = notAllowed;
export const DELETE = notAllowed;
