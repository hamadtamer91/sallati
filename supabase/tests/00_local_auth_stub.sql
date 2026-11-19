-- LOCAL TESTING ONLY. Supabase already provides the auth schema, roles and auth.uid().
-- Run this before the migrations on a plain Postgres to emulate them.
create schema if not exists auth;
create table if not exists auth.users (id uuid primary key default gen_random_uuid(), email text);
create or replace function auth.uid() returns uuid language sql stable as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
end $$;
create schema if not exists extensions;
do $$ begin execute format('alter database %I set search_path = public, extensions', current_database()); end $$;
