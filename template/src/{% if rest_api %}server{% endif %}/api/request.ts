import type { z } from "zod";
import type { ProblemFieldError } from "@/lib/schemas/v1";
import { problem } from "@/server/api/responses";

const MAX_FIELD_ERRORS = 10;

type JsonBody = { ok: true; data: unknown } | { ok: false; response: Response };

/**
 * Reads a JSON request body defensively: the media type must be application/json, the size is
 * bounded while streaming (a missing or false Content-Length cannot defeat it), the bytes must be
 * valid UTF-8 and valid JSON. Failures are problem responses; the body is never echoed.
 */
export async function readJson(
  request: Request,
  maxBytes: number,
): Promise<JsonBody> {
  const mediaType = (request.headers.get("content-type") ?? "")
    .split(";")[0]
    .trim()
    .toLowerCase();
  if (mediaType !== "application/json") {
    return {
      ok: false,
      response: problem("unsupported_media_type", {
        headers: { "Accept-Post": "application/json" },
      }),
    };
  }

  const declared = Number(request.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > maxBytes) {
    return { ok: false, response: problem("payload_too_large") };
  }

  const chunks: Uint8Array[] = [];
  let received = 0;
  const reader = request.body?.getReader();
  while (reader) {
    const { done, value } = await reader.read();
    if (done) break;
    received += value.byteLength;
    if (received > maxBytes) {
      await reader.cancel();
      return { ok: false, response: problem("payload_too_large") };
    }
    chunks.push(value);
  }

  const bytes = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }

  try {
    return {
      ok: true,
      data: JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)),
    };
  } catch {
    return { ok: false, response: problem("invalid_json") };
  }
}

/**
 * Field errors for a failed `safeParse`, bounded and free of submitted values: Zod messages describe
 * the rule, and an unexpected field is reported without its name.
 */
export function toFieldErrors(error: z.ZodError): ProblemFieldError[] {
  return error.issues.slice(0, MAX_FIELD_ERRORS).map((issue) => ({
    path: issue.path.map(String).join(".").slice(0, 100),
    message: (issue.code === "unrecognized_keys"
      ? "Unexpected field"
      : issue.message
    ).slice(0, 200),
  }));
}
