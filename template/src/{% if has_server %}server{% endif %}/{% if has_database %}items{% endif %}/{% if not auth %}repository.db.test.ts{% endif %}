import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  DatabaseError,
  closePool,
  getPool,
  toDatabaseError,
  withTransaction,
} from "@/server/db";
import {
  createItem,
  deleteItem,
  getItem,
  listItems,
  updateItem,
} from "@/server/items/repository";
import {
  createTestDatabase,
  type TestDatabase,
} from "@/server/db/test-support/database";

const MISSING_ID = "00000000-0000-4000-8000-000000000000";
let database: TestDatabase;

async function failure(promise: Promise<unknown>): Promise<DatabaseError> {
  try {
    await promise;
  } catch (error) {
    expect(error).toBeInstanceOf(DatabaseError);
    return error as DatabaseError;
  }
  throw new Error("expected the operation to fail");
}

beforeAll(async () => {
  database = await createTestDatabase();
  process.env.DATABASE_URL = database.url;
  process.env.DATABASE_STATEMENT_TIMEOUT_MS = "5000";
});

afterAll(async () => {
  await closePool();
  await database.drop();
});

describe("create, read, update, delete", () => {
  it("creates an item with generated id and timestamps, and reads it back", async () => {
    const created = await createItem({
      title: "  First item  ",
      body: "hello",
    });

    expect(created).toMatchObject({ title: "First item", body: "hello" });
    expect(created.id).toMatch(/^[0-9a-f-]{36}$/);
    expect(created.createdAt).toBeInstanceOf(Date);
    expect(await getItem(created.id)).toEqual(created);
    expect((await createItem({ title: "No body" })).body).toBe("");
  });

  it("updates one field or both, keeps the rest, and moves updatedAt forward", async () => {
    const created = await createItem({ title: "To change", body: "original" });

    const retitled = await updateItem(created.id, { title: "Changed" });
    expect([retitled.title, retitled.body]).toEqual(["Changed", "original"]);
    const rewritten = await updateItem(created.id, { body: "" });
    expect([rewritten.title, rewritten.body]).toEqual(["Changed", ""]);
    const both = await updateItem(created.id, { title: "Both", body: "two" });
    expect([both.title, both.body]).toEqual(["Both", "two"]);
    expect(both.updatedAt.getTime()).toBeGreaterThanOrEqual(
      created.updatedAt.getTime(),
    );
    expect(both.createdAt).toEqual(created.createdAt);
  });

  it("deletes an item once, and reports that there is nothing left to delete", async () => {
    const created = await createItem({ title: "Short lived" });

    expect(await deleteItem(created.id)).toBe(true);
    expect(await deleteItem(created.id)).toBe(false);
    expect((await failure(getItem(created.id))).code).toBe("not_found");
  });

  it("lists newest first within bounds and pages without overlap", async () => {
    for (const index of [1, 2, 3, 4, 5])
      await createItem({ title: `listed ${index}` });

    const firstPage = await listItems({ limit: 2 });
    const secondPage = await listItems({ limit: 2, offset: 2 });
    expect(firstPage).toHaveLength(2);
    expect(secondPage).toHaveLength(2);
    expect(
      new Set([...firstPage, ...secondPage].map((item) => item.id)).size,
    ).toBe(4);
    expect(firstPage[0].createdAt.getTime()).toBeGreaterThanOrEqual(
      firstPage[1].createdAt.getTime(),
    );
    expect((await listItems()).length).toBeLessThanOrEqual(20);
    expect(await listItems({ offset: 100_000 })).toEqual([]);
  });
});

describe("missing records", () => {
  it("reports not_found for an unknown id on read and update, and false on delete", async () => {
    expect((await failure(getItem(MISSING_ID))).code).toBe("not_found");
    expect((await failure(updateItem(MISSING_ID, { title: "x" }))).code).toBe(
      "not_found",
    );
    expect(await deleteItem(MISSING_ID)).toBe(false);
  });
});

describe("invalid input", () => {
  it.each([
    ["an empty title", () => createItem({ title: "   " })],
    [
      "a title over 100 characters",
      () => createItem({ title: "t".repeat(101) }),
    ],
    [
      "a body over 2000 characters",
      () => createItem({ title: "x", body: "b".repeat(2001) }),
    ],
    [
      "an unexpected field",
      () => createItem({ title: "x", isAdmin: true } as never),
    ],
    ["a non-string title", () => createItem({ title: 5 } as never)],
    ["nothing to update", () => updateItem(MISSING_ID, {})],
    ["a malformed id on read", () => getItem("not-a-uuid")],
    ["a malformed id on update", () => updateItem("1", { title: "x" })],
    ["a malformed id on delete", () => deleteItem("")],
    ["a limit of zero", () => listItems({ limit: 0 })],
    ["a limit over 100", () => listItems({ limit: 101 })],
    ["a negative offset", () => listItems({ offset: -1 })],
  ])(
    "rejects %s with invalid_input before the database is touched",
    async (_label, operation) => {
      const error = await failure(operation());
      expect(error.code).toBe("invalid_input");
      expect(error.sqlState).toBeUndefined(); // it never reached PostgreSQL
    },
  );

  it("rejects a NUL character, which PostgreSQL cannot store, as invalid input", async () => {
    expect((await failure(createItem({ title: "has\u0000nul" }))).code).toBe(
      "invalid_input",
    );
  });
});

describe("SQL injection attempts are stored or rejected as data, never executed", () => {
  const attacks = [
    "'; drop table app.items; --",
    "x' or '1'='1",
    "Robert'); delete from app.items; --",
    "$1",
    "\\'; select pg_sleep(30); --",
    "%' union select id, title, body, created_at, updated_at from app.items --",
  ];

  it.each(attacks)(
    "stores %j literally as a title and body",
    async (attack) => {
      const created = await createItem({ title: attack, body: attack });
      expect(await getItem(created.id)).toMatchObject({
        title: attack,
        body: attack,
      });
      expect(
        (await updateItem(created.id, { title: `${attack}!` })).title,
      ).toBe(`${attack}!`);
    },
  );

  it("leaves the table and its rows intact after the attacks", async () => {
    const result = await getPool().query(
      "select count(*)::int as n from app.items where title like '%drop table%'",
    );
    expect(result.rows[0].n).toBeGreaterThan(0);
    expect(
      (
        await getPool().query(
          "select to_regclass('app.items') is not null as present",
        )
      ).rows[0].present,
    ).toBe(true);
  });

  it.each([
    "1 or 1=1",
    "' or ''='",
    "00000000-0000-4000-8000-000000000000; drop table app.items",
  ])("rejects %j used as an id", async (id) => {
    expect((await failure(getItem(id))).code).toBe("invalid_input");
    expect((await failure(deleteItem(id))).code).toBe("invalid_input");
  });

  it("rejects injection through paging parameters", async () => {
    expect(
      (await failure(listItems({ limit: "1; drop table app.items" as never })))
        .code,
    ).toBe("invalid_input");
    expect(
      (await failure(listItems({ offset: "0 union select 1" as never }))).code,
    ).toBe("invalid_input");
  });
});

describe("duplicate keys and constraints", () => {
  it("reports a duplicate title, ignoring case, as a conflict without quoting the data", async () => {
    await createItem({ title: "Unique Title" });

    const error = await failure(createItem({ title: "unique title" }));
    expect(error.code).toBe("conflict");
    expect(error.sqlState).toBe("23505");
    expect(error.message).not.toMatch(/unique title/i);
    expect((await failure(createItem({ title: "UNIQUE TITLE" }))).code).toBe(
      "conflict",
    );
  });

  it("reports a conflicting rename as a conflict and leaves the row unchanged", async () => {
    const a = await createItem({ title: "Rename A" });
    await createItem({ title: "Rename B" });

    expect((await failure(updateItem(a.id, { title: "rename b" }))).code).toBe(
      "conflict",
    );
    expect((await getItem(a.id)).title).toBe("Rename A");
  });

  it("maps a database-level check violation, even when the application checks are bypassed", async () => {
    const mapped = await getPool()
      .query("insert into app.items (title) values ('')")
      .then(
        () => undefined,
        (caught: unknown) => toDatabaseError(caught),
      );

    expect(mapped?.code).toBe("constraint_violation");
    expect(mapped?.sqlState).toBe("23514");
    expect(mapped?.message).toBe("The data breaks a database rule");
  });
});

describe("transactions", () => {
  it("commits everything when the work returns", async () => {
    const [a, b] = await withTransaction(async (tx) => [
      await createItem({ title: "tx commit a" }, tx),
      await createItem({ title: "tx commit b" }, tx),
    ]);

    expect((await getItem(a.id)).title).toBe("tx commit a");
    expect((await getItem(b.id)).title).toBe("tx commit b");
  });

  it("rolls everything back when the work throws, and rethrows the error", async () => {
    let createdId = "";
    const error = await failure(
      withTransaction(async (tx) => {
        createdId = (await createItem({ title: "tx rollback" }, tx)).id;
        expect((await getItem(createdId, tx)).title).toBe("tx rollback"); // visible inside the transaction
        throw new DatabaseError("unexpected", "boom");
      }),
    );

    expect(error.message).toBe("boom");
    expect((await failure(getItem(createdId))).code).toBe("not_found");
  });

  it("rolls back when a statement inside fails, undoing the earlier statements", async () => {
    await createItem({ title: "tx duplicate" });

    const error = await failure(
      withTransaction(async (tx) => {
        await createItem({ title: "tx before failure" }, tx);
        await createItem({ title: "TX DUPLICATE" }, tx);
      }),
    );

    expect(error.code).toBe("conflict");
    expect(
      (await listItems({ limit: 100 })).some(
        (item) => item.title === "tx before failure",
      ),
    ).toBe(false);
  });

  it("isolates concurrent transactions until they commit", async () => {
    let id = "";
    await withTransaction(async (tx) => {
      id = (await createItem({ title: "tx isolated" }, tx)).id;
      expect((await failure(getItem(id))).code).toBe("not_found"); // another connection does not see it yet
    });
    expect((await getItem(id)).title).toBe("tx isolated");
  });

  it("always returns its connection: nothing leaks after many successes and failures", async () => {
    const pool = getPool();
    for (let round = 0; round < 12; round += 1) {
      await withTransaction(async (tx) =>
        createItem({ title: `leak check ${round}` }, tx),
      );
      await failure(
        withTransaction(async () => {
          throw new DatabaseError("unexpected");
        }),
      );
    }
    expect(pool.waitingCount).toBe(0);
    expect(pool.idleCount).toBe(pool.totalCount);
  });

  it("serves more concurrent requests than the pool has connections", async () => {
    const created = await Promise.all(
      Array.from({ length: 30 }, (_, index) =>
        createItem({ title: `concurrent ${index}` }),
      ),
    );
    expect(new Set(created.map((item) => item.id)).size).toBe(30);
    expect(getPool().totalCount).toBeLessThanOrEqual(5);
  });
});

describe("timeouts", () => {
  it("cancels a statement that runs longer than the statement timeout", async () => {
    await closePool();
    process.env.DATABASE_STATEMENT_TIMEOUT_MS = "300";
    const started = Date.now();
    const error = await failure(
      withTransaction(async (tx) => {
        await tx.query("select pg_sleep(5)", []);
      }),
    );
    expect(error.code).toBe("timeout");
    expect(Date.now() - started).toBeLessThan(3_000);
    expect((await createItem({ title: "after the timeout" })).title).toBe(
      "after the timeout",
    ); // the pool recovered
    await closePool();
    process.env.DATABASE_STATEMENT_TIMEOUT_MS = "5000";
  });
});
