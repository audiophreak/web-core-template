import { afterAll, beforeAll, describe, expect, it } from "vitest";
import pg from "pg";
import { DatabaseError, closePool, getPool } from "@/server/db";
import { adminUrl } from "@/server/db/test-support/database";
import {
  createItem,
  deleteItem,
  getItem,
  listItems,
  updateItem,
} from "@/server/items/repository";
import { authHandler } from "./handler";
import { AuthenticationError, UnsafeRuntimeRoleError } from "./errors";
import { principalFromHeaders, type Principal } from "./principal";
import { assertRuntimeRole } from "./runtime-role";
import { withPrincipal } from "./scope";
import {
  createRuntimeDatabase,
  forgetApplication,
  requestTo,
  signUp,
  type RuntimeDatabase,
  type TestUser,
} from "./test-support/harness";

/**
 * Authorization against a real PostgreSQL, as the real runtime role: what the role may and may not do, row
 * level security with and without a user, the owner-scoped repository, injection attempts, and the
 * guarantee that one request's identity never reaches another through the connection pool.
 */
let database: RuntimeDatabase;

async function admin<T = Record<string, unknown>>(
  sql: string,
  values: unknown[] = [],
): Promise<T[]> {
  const url = new URL(adminUrl());
  url.pathname = `/${database.name}`;
  const client = new pg.Client({ connectionString: url.toString() });
  await client.connect();
  try {
    return (await client.query(sql, values)).rows as T[];
  } finally {
    await client.end();
  }
}

async function principalOf(user: TestUser): Promise<Principal> {
  const principal = await principalFromHeaders(user.headers);
  if (!principal) throw new Error("the test user has no session");
  return principal;
}

async function denied(promise: Promise<unknown>): Promise<string | undefined> {
  try {
    await promise;
  } catch (error) {
    return (error as { code?: string }).code;
  }
  return "allowed";
}

let alice: TestUser;
let bob: TestUser;
let alicePrincipal: Principal;
let bobPrincipal: Principal;

beforeAll(async () => {
  database = await createRuntimeDatabase();
  alice = await signUp("alice");
  bob = await signUp("bob");
  alicePrincipal = await principalOf(alice);
  bobPrincipal = await principalOf(bob);
});

afterAll(async () => {
  await forgetApplication();
  await database.drop();
});

describe("the runtime role", () => {
  it("is not an administrator, owns nothing and cannot bypass row level security", async () => {
    const [role] = await admin<Record<string, unknown>>(
      `select rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls, rolinherit, rolconnlimit
         from pg_roles where rolname = 'app_runtime'`,
    );
    expect(role).toMatchObject({
      rolsuper: false,
      rolcreaterole: false,
      rolcreatedb: false,
      rolreplication: false,
      rolbypassrls: false,
    });
    const owned = await admin(
      `select 'schema' as kind from pg_namespace n join pg_roles r on r.oid = n.nspowner where r.rolname = 'app_runtime'
       union all select 'relation' from pg_class c join pg_roles r on r.oid = c.relowner where r.rolname = 'app_runtime'`,
    );
    expect(owned).toEqual([]);
    const whoami = await getPool().query("select current_user as name");
    expect(whoami.rows[0].name).toBe("app_runtime");
  });

  it("cannot run DDL, administer roles or read what is not its own", async () => {
    await admin(
      "create schema other; create table other.secret (x int); insert into other.secret values (1)",
    );
    const statements = [
      "create schema evil",
      "create table app.evil (id int)",
      "create table public.evil (id int)",
      "create table app_auth.evil (id int)",
      "drop table app.items",
      "drop table app_auth.session",
      "truncate app.items",
      "alter table app.items disable row level security",
      "alter table app.items add column evil int",
      "create role evil login",
      "alter role app_runtime bypassrls",
      "select * from pg_catalog.pg_authid",
      "select * from other.secret",
      "create function app.evil() returns int language sql as 'select 1'",
      "create policy evil on app.items using (true)",
    ];
    for (const sql of statements)
      expect(await denied(getPool().query(sql)), sql).toBe("42501");
  });

  it("can only use the tables of the application and of Better Auth", async () => {
    const privileges = await admin<{ table: string; privileges: string }>(
      `select table_schema || '.' || table_name as "table",
              string_agg(privilege_type, ',' order by privilege_type) as privileges
         from information_schema.role_table_grants
        where grantee = 'app_runtime' group by 1 order by 1`,
    );
    expect(privileges.map((row) => row.table)).toEqual([
      "app.items",
      "app_auth.account",
      "app_auth.rateLimit",
      "app_auth.session",
      "app_auth.user",
      "app_auth.verification",
    ]);
    const items = privileges.find((row) => row.table === "app.items");
    expect(items?.privileges).toBe("DELETE,SELECT"); // no TRUNCATE, REFERENCES or TRIGGER; INSERT and UPDATE are per column
    const columns = await admin<{ privilege_type: string; columns: string }>(
      `select privilege_type, string_agg(column_name, ',' order by column_name) as columns
         from information_schema.column_privileges
        where grantee = 'app_runtime' and table_schema = 'app' and table_name = 'items'
          and privilege_type in ('INSERT', 'UPDATE') group by 1 order by 1`,
    );
    expect(columns).toEqual([
      { privilege_type: "INSERT", columns: "body,owner_id,title" },
      { privilege_type: "UPDATE", columns: "body,title,updated_at" },
    ]);
  });

  it("cannot change who owns an item, even for its own rows", async () => {
    const item = await withPrincipal(alicePrincipal, (scope) =>
      createItem(scope, { title: "Mine to keep" }),
    );
    const code = await withPrincipal(alicePrincipal, async (scope) =>
      denied(
        scope.db.query("update app.items set owner_id = $1 where id = $2", [
          bob.id,
          item.id,
        ]),
      ),
    ).catch((error: unknown) => (error as { code?: string }).code);
    expect(code).toBe("42501");
    expect(
      (await withPrincipal(alicePrincipal, (scope) => getItem(scope, item.id)))
        .title,
    ).toBe("Mine to keep");
  });
});

describe("row level security", () => {
  it("shows nothing and accepts nothing without an authenticated user", async () => {
    await admin(
      "insert into app.items (owner_id, title) values ($1, 'Alice private'), ($2, 'Bob private')",
      [alice.id, bob.id],
    );
    const visible = await getPool().query("select id from app.items");
    expect(visible.rowCount).toBe(0);
    expect(
      await denied(
        getPool().query(
          "insert into app.items (owner_id, title) values ($1, 'sneaky')",
          [alice.id],
        ),
      ),
    ).toBe("42501");
    expect(
      (await getPool().query("update app.items set title = 'changed'"))
        .rowCount,
    ).toBe(0);
    expect((await getPool().query("delete from app.items")).rowCount).toBe(0);
    expect(
      await admin(
        "select 1 from app.items where title = 'sneaky' or title = 'changed'",
      ),
    ).toEqual([]);
  });

  it("limits even a raw query on the scope to the caller's rows", async () => {
    const mine = await withPrincipal(alicePrincipal, (scope) =>
      scope.db.query("select title from app.items order by title"),
    );
    const titles = mine.rows.map((row) => row.title);
    expect(titles).toContain("Alice private");
    expect(titles).not.toContain("Bob private");
    const theirs = await withPrincipal(alicePrincipal, (scope) =>
      scope.db.query("select 1 from app.items where owner_id = $1", [bob.id]),
    );
    expect(theirs.rowCount).toBe(0);
  });

  it("refuses a raw insert for another owner and cannot update or delete another user's row", async () => {
    const insertCode = await withPrincipal(alicePrincipal, async (scope) =>
      denied(
        scope.db.query(
          "insert into app.items (owner_id, title) values ($1, 'as bob')",
          [bob.id],
        ),
      ),
    ).catch((error: unknown) => (error as { code?: string }).code);
    expect(insertCode).toBe("42501");
    const counts = await withPrincipal(alicePrincipal, async (scope) => ({
      updated: (
        await scope.db.query(
          "update app.items set title = 'hijacked' where owner_id = $1",
          [bob.id],
        )
      ).rowCount,
      deleted: (
        await scope.db.query("delete from app.items where owner_id = $1", [
          bob.id,
        ])
      ).rowCount,
    }));
    expect(counts).toEqual({ updated: 0, deleted: 0 });
    expect(
      await admin(
        "select title from app.items where owner_id = $1 order by title",
        [bob.id],
      ),
    ).toContainEqual({ title: "Bob private" });
  });
});

describe("the owner-scoped repository", () => {
  it("creates, reads, updates, lists and deletes the caller's own items", async () => {
    const { created, read, updated, listed, deleted, afterDelete } =
      await withPrincipal(alicePrincipal, async (scope) => {
        const created = await createItem(scope, {
          title: "Repository item",
          body: "hello",
        });
        const read = await getItem(scope, created.id);
        const updated = await updateItem(scope, created.id, {
          body: "changed",
        });
        const listed = await listItems(scope, { limit: 100 });
        const deleted = await deleteItem(scope, created.id);
        const afterDelete = await deleteItem(scope, created.id);
        return { created, read, updated, listed, deleted, afterDelete };
      });
    expect(read).toEqual(created);
    expect(updated.body).toBe("changed");
    expect(listed.map((item) => item.id)).toContain(created.id);
    expect([deleted, afterDelete]).toEqual([true, false]);
  });

  it("treats another user's item exactly like a missing one", async () => {
    const [bobItem] = await admin<{ id: string }>(
      "select id from app.items where owner_id = $1 limit 1",
      [bob.id],
    );
    const missing = "00000000-0000-4000-8000-000000000000";
    const outcomes = await withPrincipal(alicePrincipal, async (scope) => {
      const attempt = async (work: () => Promise<unknown>) => {
        try {
          return String(await work());
        } catch (error) {
          return error instanceof DatabaseError ? error.code : "other";
        }
      };
      return {
        foreign: [
          await attempt(() => getItem(scope, bobItem.id)),
          await attempt(() =>
            updateItem(scope, bobItem.id, { title: "mine now" }),
          ),
          await attempt(() => deleteItem(scope, bobItem.id)),
        ],
        absent: [
          await attempt(() => getItem(scope, missing)),
          await attempt(() =>
            updateItem(scope, missing, { title: "mine now" }),
          ),
          await attempt(() => deleteItem(scope, missing)),
        ],
      };
    });
    expect(outcomes.foreign).toEqual(outcomes.absent);
    expect(outcomes.foreign).toEqual(["not_found", "not_found", "false"]);
  });

  it("lets two users have items with the same title, and tells nobody about the other's titles", async () => {
    await withPrincipal(alicePrincipal, (scope) =>
      createItem(scope, { title: "Shared Title" }),
    );
    await withPrincipal(bobPrincipal, (scope) =>
      createItem(scope, { title: "shared title" }),
    );
    const again = await withPrincipal(alicePrincipal, (scope) =>
      createItem(scope, { title: "SHARED TITLE" }),
    ).catch((error: unknown) => error);
    expect(again).toBeInstanceOf(DatabaseError);
    expect((again as DatabaseError).code).toBe("conflict");
  });

  it("stores injection attempts as plain text and rejects them as identifiers or options", async () => {
    const evil = "x'); drop table app.items; --";
    const stored = await withPrincipal(alicePrincipal, async (scope) => {
      const item = await createItem(scope, {
        title: evil,
        body: "' or '1'='1",
      });
      return getItem(scope, item.id);
    });
    expect(stored.title).toBe(evil.trim());
    expect(
      await admin("select to_regclass('app.items') is not null as present"),
    ).toEqual([{ present: true }]);
    const codes = await withPrincipal(alicePrincipal, async (scope) => {
      const code = async (work: () => Promise<unknown>) => {
        try {
          await work();
          return "no error";
        } catch (error) {
          return error instanceof DatabaseError ? error.code : "other";
        }
      };
      return [
        await code(() => getItem(scope, "1 or 1=1")),
        await code(() =>
          getItem(scope, `${bob.id}' or owner_id is not null --`),
        ),
        await code(() => deleteItem(scope, "x'; delete from app.items; --")),
        await code(() =>
          listItems(scope, { limit: "1; drop table app.items" as never }),
        ),
        await code(() =>
          updateItem(scope, stored.id, { title: undefined, body: undefined }),
        ),
      ];
    });
    expect(codes).toEqual([
      "invalid_input",
      "invalid_input",
      "invalid_input",
      "invalid_input",
      "invalid_input",
    ]);
    expect(
      (await admin("select count(*)::int as n from app.items"))[0].n,
    ).toBeGreaterThan(0);
  });

  it("refuses a principal this application did not issue", async () => {
    const forged = { userId: alice.id } as unknown as Principal;
    await expect(
      withPrincipal(forged, async () => "reached"),
    ).rejects.toBeInstanceOf(AuthenticationError);
    await expect(
      withPrincipal(
        Object.create(Object.getPrototypeOf(alicePrincipal)) as Principal,
        async () => "reached",
      ),
    ).rejects.toBeInstanceOf(AuthenticationError);
  });
});

describe("the identity of one request never reaches another", () => {
  it("is gone from the connection after a commit and after a rollback (one pooled connection)", async () => {
    await forgetApplication();
    process.env.DATABASE_RUNTIME_URL = database.runtimeUrl;
    process.env.DATABASE_POOL_MAX = "1";
    try {
      await withPrincipal(alicePrincipal, async (scope) => {
        expect(
          (
            await scope.db.query(
              "select current_setting('app.user_id', true) as v",
            )
          ).rows[0].v,
        ).toBe(alice.id);
      });
      const after = await getPool().query(
        "select current_setting('app.user_id', true) as v, count(*)::int as items from app.items",
      );
      expect([null, ""]).toContain(after.rows[0].v);
      expect(after.rows[0].items).toBe(0);

      await withPrincipal(bobPrincipal, async () => {
        throw new Error("fail on purpose");
      }).catch(() => undefined);
      const afterRollback = await getPool().query(
        "select current_setting('app.user_id', true) as v, count(*)::int as items from app.items",
      );
      expect([null, ""]).toContain(afterRollback.rows[0].v);
      expect(afterRollback.rows[0].items).toBe(0);
    } finally {
      delete process.env.DATABASE_POOL_MAX;
      await forgetApplication();
      process.env.DATABASE_RUNTIME_URL = database.runtimeUrl;
    }
  });

  it("holds under concurrent requests from many users on a small pool", async () => {
    await forgetApplication();
    process.env.DATABASE_RUNTIME_URL = database.runtimeUrl;
    process.env.DATABASE_POOL_MAX = "3";
    try {
      const users = await Promise.all(
        Array.from({ length: 10 }, (_, index) => signUp(`crowd${index}`)),
      );
      const principals = await Promise.all(users.map(principalOf));
      await Promise.all(
        principals.flatMap((principal, index) =>
          Array.from({ length: 6 }, (_, count) =>
            withPrincipal(principal, (scope) =>
              createItem(scope, { title: `crowd-${index}-${count}` }),
            ),
          ),
        ),
      );
      const reads = await Promise.all(
        Array.from({ length: 150 }, (_, round) => {
          const index = (round * 7) % principals.length;
          return withPrincipal(principals[index], async (scope) => ({
            index,
            items: await listItems(scope, { limit: 100 }),
            raw: (await scope.db.query("select title from app.items")).rows.map(
              (row) => row.title as string,
            ),
          }));
        }),
      );
      for (const { index, items, raw } of reads) {
        expect(items).toHaveLength(6);
        expect(
          items.every((item) => item.title.startsWith(`crowd-${index}-`)),
        ).toBe(true);
        expect(raw).toHaveLength(6);
        expect(raw.every((title) => title.startsWith(`crowd-${index}-`))).toBe(
          true,
        );
      }
    } finally {
      delete process.env.DATABASE_POOL_MAX;
      await forgetApplication();
      process.env.DATABASE_RUNTIME_URL = database.runtimeUrl;
    }
  });
});

describe("running as an administrator is refused", () => {
  it("fails closed when the application is pointed at the owner, and never reaches the database", async () => {
    await forgetApplication();
    process.env.DATABASE_RUNTIME_URL = database.url; // the owner / administrator connection
    try {
      await expect(assertRuntimeRole()).rejects.toBeInstanceOf(
        UnsafeRuntimeRoleError,
      );
      await expect(
        withPrincipal(alicePrincipal, async () => "reached"),
      ).rejects.toBeInstanceOf(UnsafeRuntimeRoleError);
      const response = await authHandler(requestTo("/api/auth/get-session"));
      expect(response.status).toBe(500);
      const text = await response.text();
      expect(text).not.toMatch(/app_runtime|postgres|DATABASE|owner/i);
    } finally {
      await forgetApplication();
      process.env.DATABASE_RUNTIME_URL = database.runtimeUrl;
    }
    await closePool();
  });
});
