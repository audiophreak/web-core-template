import { z } from "zod";

/**
 * GET /api/v1/app-info: the application's name and description.
 *
 * Declared here, not borrowed from the internal `appInfoSchema`, so the public contract only changes
 * on purpose. Fields are only ever added within v1; clients must ignore fields they do not know.
 */
export const appInfoResponseSchema = z.strictObject({
  name: z.string().min(1).max(64),
  description: z.string().min(1).max(200),
});

export type AppInfoResponse = z.infer<typeof appInfoResponseSchema>;
