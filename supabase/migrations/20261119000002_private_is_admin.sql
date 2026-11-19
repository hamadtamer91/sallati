-- Keep the admin check out of the public REST API (Supabase advisor 0028/0029).
create schema if not exists private;
grant usage on schema private to anon, authenticated;

create or replace function private.is_admin()
returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.app_admins where user_id = (select auth.uid()))
$$;
revoke all on function private.is_admin() from public;
grant execute on function private.is_admin() to anon, authenticated;

drop policy public_read on promotions;
drop policy admin_read on source_products;
drop policy admin_read on flyers;
drop policy admin_read on scraper_runs;
drop policy admin_read on app_admins;
drop function public.is_admin();

create policy public_read on promotions      for select using (status = 'active' or private.is_admin());
create policy admin_read on source_products  for select using (private.is_admin());
create policy admin_read on flyers           for select using (private.is_admin());
create policy admin_read on scraper_runs     for select using (private.is_admin());
create policy admin_read on app_admins       for select using (private.is_admin());
