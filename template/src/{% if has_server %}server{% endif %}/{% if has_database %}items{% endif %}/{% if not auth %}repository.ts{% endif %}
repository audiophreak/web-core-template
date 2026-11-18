import "server-only";
import { z } from "zod";
import {
  createItemInputSchema,
  itemIdSchema,
  itemSchema,
  listItemsOptionsSchema,
  updateItemInputSchema,
  type CreateItemInput,
  type Item,
  type ListItemsOptions,
  type UpdateItemInput,
} from "@/lib/schemas";
import { DatabaseError, toDatabaseError } from "@/server/db/errors";
import { getPool, type Queryable } from "@/server/db/pool";

/**
 * The example repository over `app.items`: the only place that knows its SQL. Every function takes an
 * optional `db`, the pool by default or the `tx` of `withTransaction`, validates its input with the shared
 * Zod schemas before touching the database, uses parameterized SQL only, and returns validated `Item`s.
 * Failures are DatabaseErrors (see ../db/errors.ts) with fixed messages.
 */
const COLUMNS = "id, title, body, created_at, updated_at";

const rowSchema = z.object({
  id: z.string(),
  title: z.string(),
  body: z.string(),
  created_at: z.date(),
  updated_at: z.date(),
});

function parseInput<S extends z.ZodType>(
  schema: S,
  value: unknown,
): z.output<S> {
  const parsed = schema.safeParse(value);
  if (parsed.success) return parsed.data;
  // Rule descriptions only: Zod's messages name the rule, not the submitted value.
  const reasons = parsed.error.issues.map(
    (issue) => `${issue.path.join(".") || "input"}: ${issue.message}`,
  );
  throw new DatabaseError(
    "invalid_input",
    `The input is not valid (${reasons.join("; ")})`,
  );
}

async function run(db: Queryable, text: string, values: unknown[]) {
  try {
    return await db.query(text, values);
  } catch (error) {
    throw toDatabaseError(error);
  }
}

function toItem(row: unknown): Item {
  const parsed = rowSchema.parse(row);
  return itemSchema.parse({
    id: parsed.id,
    title: parsed.title,
    body: parsed.body,
    createdAt: parsed.created_at,
    updatedAt: parsed.updated_at,
  });
}

export async function createItem(
  input: CreateItemInput,
  db: Queryable = getPool(),
): Promise<Item> {
  const { title, body } = parseInput(createItemInputSchema, input);
  const result = await run(
    db,
    `insert into app.items (title, body) values ($1, $2) returning ${COLUMNS}`,
    [title, body],
  );
  return toItem(result.rows[0]);
}

export async function getItem(
  id: string,
  db: Queryable = getPool(),
): Promise<Item> {
  const itemId = parseInput(itemIdSchema, id);
  const result = await run(
    db,
    `select ${COLUMNS} from app.items where id = $1`,
    [itemId],
  );
  if (result.rows.length === 0) throw new DatabaseError("not_found");
  return toItem(result.rows[0]);
}

export async function listItems(
  options: ListItemsOptions = {},
  db: Queryable = getPool(),
): Promise<Item[]> {
  const { limit, offset } = parseInput(listItemsOptionsSchema, options);
  const result = await run(
    db,
    `select ${COLUMNS} from app.items order by created_at desc, id limit $1 offset $2`,
    [limit, offset],
  );
  return result.rows.map(toItem);
}

export async function updateItem(
  id: string,
  input: UpdateItemInput,
  db: Queryable = getPool(),
): Promise<Item> {
  const itemId = parseInput(itemIdSchema, id);
  const changes = parseInput(updateItemInputSchema, input);
  // Column names come from this fixed list, never from the caller; values are always parameters.
  const assignments: string[] = [];
  const values: unknown[] = [itemId];
  for (const column of ["title", "body"] as const) {
    if (changes[column] !== undefined) {
      values.push(changes[column]);
      assignments.push(`${column} = $${values.length}`);
    }
  }
  const result = await run(
    db,
    `update app.items set ${assignments.join(", ")}, updated_at = now() where id = $1 returning ${COLUMNS}`,
    values,
  );
  if (result.rows.length === 0) throw new DatabaseError("not_found");
  return toItem(result.rows[0]);
}

/** Deletes one item; resolves to false when there was nothing to delete. */
export async function deleteItem(
  id: string,
  db: Queryable = getPool(),
): Promise<boolean> {
  const itemId = parseInput(itemIdSchema, id);
  const result = await run(
    db,
    "delete from app.items where id = $1 returning id",
    [itemId],
  );
  return result.rows.length > 0;
}
