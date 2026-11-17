-- Sample schema: a small `items` table for trying out the data-access layer.
--
-- It lives in its own schema, `app`, which no HTTP API exposes (on Supabase the Data API serves only
-- the schemas listed in supabase/config.toml), and nothing is granted to API roles. The application
-- connects as the database owner.

create schema if not exists app;

revoke all on schema app from public;

create table app.items (
  id uuid primary key default gen_random_uuid(),
  title text not null check (char_length(title) between 1 and 100),
  body text not null default '' check (char_length(body) <= 2000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index items_title_key on app.items (lower(title));

-- Deny by default: a role that is ever granted access to this table still sees no rows until a policy
-- allows it. (The table owner and roles with BYPASSRLS are not subject to row level security.)
alter table app.items enable row level security;

-- Supabase's API roles exist only on Supabase; make sure they hold nothing here.
do $$
declare
  api_role text;
begin
  foreach api_role in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = api_role) then
      execute format('revoke all on schema app from %I', api_role);
      execute format('revoke all on all tables in schema app from %I', api_role);
      execute format('revoke all on all sequences in schema app from %I', api_role);
      execute format('revoke all on all functions in schema app from %I', api_role);
    end if;
  end loop;
end
$$;
