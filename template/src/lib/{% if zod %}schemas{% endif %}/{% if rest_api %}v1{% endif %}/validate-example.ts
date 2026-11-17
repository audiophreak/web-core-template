import { z } from "zod";

export const priorities = ["low", "normal", "high"] as const;

/**
 * POST /api/v1/examples/validate: an example request body, e.g.
 * `{ "title": "Write the report", "priority": "high", "tags": ["work", "q4"] }`.
 *
 * Unknown fields are rejected. `priority` defaults to "normal" and `tags` to an empty list, so new
 * optional fields can be added within v1 without breaking existing callers.
 */
export const validateExampleRequestSchema = z.strictObject({
  title: z
    .string()
    .trim()
    .min(1, "Title is required")
    .max(100, "Title must be 100 characters or fewer"),
  priority: z.enum(priorities).default("normal"),
  tags: z
    .array(
      z
        .string()
        .min(1, "A tag cannot be empty")
        .max(20, "A tag must be 20 characters or fewer")
        .regex(
          /^[a-z0-9]+(-[a-z0-9]+)*$/,
          "A tag uses lowercase letters, digits and single hyphens",
        ),
    )
    .max(5, "Use at most 5 tags")
    .default([]),
});

/**
 * The bounded result. It reports what was accepted without echoing the submitted text: no title,
 * no tags, only lengths, a count and the (enumerated) priority.
 */
export const validateExampleResponseSchema = z.strictObject({
  valid: z.literal(true),
  titleLength: z.int().min(1).max(100),
  priority: z.enum(priorities),
  tagCount: z.int().min(0).max(5),
});

export type ValidateExampleRequest = z.input<
  typeof validateExampleRequestSchema
>;
export type ValidateExampleResponse = z.infer<
  typeof validateExampleResponseSchema
>;
