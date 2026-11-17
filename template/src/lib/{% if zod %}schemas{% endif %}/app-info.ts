import { z } from "zod";

/**
 * Shape of `src/lib/app-info.json`: the application's name and description.
 *
 * This is the pattern for every input boundary: describe the shape once here, share it between
 * client and server code, and parse untrusted data with `safeParse` before using it.
 */
export const appInfoSchema = z.strictObject({
  name: z
    .string()
    .max(64)
    .regex(
      /^[a-z][a-z0-9]*(-[a-z0-9]+)*$/,
      "Use lowercase letters, digits and single hyphens, starting with a letter",
    ),
  description: z.string().trim().min(1).max(200),
});

export type AppInfo = z.infer<typeof appInfoSchema>;
