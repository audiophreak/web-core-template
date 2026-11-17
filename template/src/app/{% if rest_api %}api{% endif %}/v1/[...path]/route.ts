import { problem } from "@/server/api/responses";

// Any /api/v1 path without its own route gets the same problem format as every other error.
const notFound = () => problem("not_found");

export const GET = notFound;
export const POST = notFound;
export const PUT = notFound;
export const PATCH = notFound;
export const DELETE = notFound;
