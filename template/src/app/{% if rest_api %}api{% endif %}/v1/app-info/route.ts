import appInfo from "@/lib/app-info.json";
import { appInfoResponseSchema } from "@/lib/schemas/v1";
import { json, methodNotAllowed } from "@/server/api/responses";

/** GET /api/v1/app-info: the application's name and description. No input, no side effects. */
export function GET() {
  return json(appInfoResponseSchema, {
    name: appInfo.name,
    description: appInfo.description,
  });
}

const notAllowed = methodNotAllowed(["GET", "HEAD", "OPTIONS"]);
export const POST = notAllowed;
export const PUT = notAllowed;
export const PATCH = notAllowed;
export const DELETE = notAllowed;
