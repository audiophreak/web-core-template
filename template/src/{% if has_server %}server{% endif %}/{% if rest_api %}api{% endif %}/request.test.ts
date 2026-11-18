import { describe, expect, it } from "vitest";
import { z } from "zod";
import { problemDetailsSchema } from "@/lib/schemas/v1";
import { readJson, toFieldErrors } from "@/server/api/request";

const MAX = 64;

function post(
  body: BodyInit | null,
  headers: Record<string, string> = { "content-type": "application/json" },
) {
  return new Request("http://localhost/x", { method: "POST", headers, body });
}

async function failure(result: Awaited<ReturnType<typeof readJson>>) {
  expect(result.ok).toBe(false);
  if (result.ok) throw new Error("expected a failure");
  return {
    status: result.response.status,
    body: problemDetailsSchema.parse(await result.response.json()),
  };
}

describe("readJson", () => {
  it("parses a JSON body", async () => {
    expect(await readJson(post('{"a":1}'), MAX)).toEqual({
      ok: true,
      data: { a: 1 },
    });
  });

  it.each([
    "application/json",
    "Application/JSON",
    "application/json; charset=utf-8",
  ])("accepts the media type %s", async (type) => {
    expect((await readJson(post("{}", { "content-type": type }), MAX)).ok).toBe(
      true,
    );
  });

  it.each([
    ["text/plain", { "content-type": "text/plain" }],
    [
      "application/x-www-form-urlencoded",
      { "content-type": "application/x-www-form-urlencoded" },
    ],
    ["application/jsonp", { "content-type": "application/jsonp" }],
    ["no content type", {}],
  ])("rejects %s with 415", async (_label, headers) => {
    const { status, body } = await failure(
      await readJson(post("{}", headers), MAX),
    );
    expect([status, body.code]).toEqual([415, "unsupported_media_type"]);
  });

  it.each([
    ["malformed JSON", "{not json"],
    ["an empty body", ""],
    ["trailing garbage", '{"a":1} x'],
  ])("rejects %s with 400", async (_label, text) => {
    const { status, body } = await failure(await readJson(post(text), MAX));
    expect([status, body.code]).toEqual([400, "invalid_json"]);
  });

  it("rejects bytes that are not valid UTF-8 with 400", async () => {
    const bytes = new Uint8Array([0x7b, 0x22, 0xff, 0x22, 0x3a, 0x31, 0x7d]);
    const { status, body } = await failure(await readJson(post(bytes), MAX));
    expect([status, body.code]).toEqual([400, "invalid_json"]);
  });

  it("rejects a declared length over the limit with 413 before reading", async () => {
    const request = post("{}", {
      "content-type": "application/json",
      "content-length": String(MAX + 1),
    });
    const { status, body } = await failure(await readJson(request, MAX));
    expect([status, body.code]).toEqual([413, "payload_too_large"]);
  });

  it("enforces the limit while streaming, whatever the headers claim", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(`{"a":"${"x".repeat(40)}`));
        controller.enqueue(new TextEncoder().encode(`${"y".repeat(40)}"}`));
        controller.close();
      },
    });
    const request = new Request("http://localhost/x", {
      method: "POST",
      headers: { "content-type": "application/json", "content-length": "5" },
      body: stream,
      duplex: "half",
    } as RequestInit & { duplex: "half" });

    const { status, body } = await failure(await readJson(request, MAX));
    expect([status, body.code]).toEqual([413, "payload_too_large"]);
  });

  it("accepts a body of exactly the limit", async () => {
    const text = JSON.stringify({ a: "x".repeat(MAX - 8) });
    expect(new TextEncoder().encode(text).byteLength).toBe(MAX);
    expect((await readJson(post(text), MAX)).ok).toBe(true);
  });

  it("never echoes the body in a failure", async () => {
    const result = await readJson(post("{secret-sentinel: oops"), MAX);
    if (result.ok) throw new Error("expected a failure");
    expect(await result.response.text()).not.toContain("secret-sentinel");
  });
});

describe("toFieldErrors", () => {
  const schema = z.strictObject({
    name: z.string().min(2),
    tags: z.array(z.string().max(1)),
  });

  it("describes rules by path without quoting submitted values", () => {
    const result = schema.safeParse({
      name: "SECRET-VALUE-TOO-LONG".slice(0, 1),
      tags: ["SECRET-LONG-TAG"],
      extra: "SECRET-EXTRA",
    });
    if (result.success) throw new Error("expected a failure");
    const errors = toFieldErrors(result.error);
    const text = JSON.stringify(errors);

    expect(errors.map((error) => error.path).sort()).toEqual([
      "",
      "name",
      "tags.0",
    ]);
    expect(errors.find((error) => error.path === "")?.message).toBe(
      "Unexpected field",
    );
    expect(text).not.toMatch(/SECRET|extra/);
  });

  it("is bounded to ten entries", () => {
    const result = z
      .array(z.string())
      .safeParse(Array.from({ length: 30 }, () => 1));
    if (result.success) throw new Error("expected a failure");
    expect(toFieldErrors(result.error)).toHaveLength(10);
  });
});
