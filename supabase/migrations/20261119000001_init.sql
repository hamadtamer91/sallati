-- =====================================================================
-- Sallati (سلّتي) — Lebanon supermarket price intelligence
-- Migration 0001: core schema
-- Target: Supabase Postgres 15+ (PostGIS + pg_trgm)
-- Writes come from workers (service role / direct DB URL, bypasses RLS).
-- Reads come from the web app (anon / authenticated, governed by RLS).
-- =====================================================================

-- Supabase keeps extensions out of the public API schema.
create schema if not exists extensions;
create extension if not exists postgis with schema extensions;
create extension if not exists pg_trgm with schema extensions;

-- ---------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------

-- Same rules as workers/pricewatch/core/text.py::normalize_text
-- (Arabic letter folding, tashkeel/tatweel removal, digit folding, lowercase).
create or replace function norm_text(p text)
returns text language sql immutable parallel safe set search_path = public, extensions as $$
  select trim(both ' .' from regexp_replace(
           regexp_replace(
             translate(
               lower(coalesce(p, '')),
               'أإآٱىةؤئ٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹×',
               'اااايهوي01234567890123456789x'),
             '[\u064B-\u0652\u0670\u0640]', '', 'g'),
           '[^[:alnum:]\.]+', ' ', 'g'))
$$;

create or replace function set_updated_at()
returns trigger language plpgsql set search_path = public, extensions as $$
begin
  new.updated_at := now();
  return new;
end $$;

-- ---------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------

create table regions (
  id              smallint generated always as identity primary key,
  slug            text not null unique check (slug ~ '^[a-z0-9-]+$'),
  name_en         text not null,
  name_ar         text not null,
  governorate_en  text not null,
  governorate_ar  text not null
);

create table categories (
  id          int generated always as identity primary key,
  slug        text not null unique check (slug ~ '^[a-z0-9-]+$'),
  parent_id   int references categories(id) on delete set null,
  name_en     text not null,
  name_ar     text not null,
  sort_order  int not null default 0
);

create table exchange_rates (
  id            bigint generated always as identity primary key,
  base          text not null default 'USD' check (base = 'USD'),
  quote         text not null default 'LBP' check (quote = 'LBP'),
  rate          numeric(14,2) not null check (rate > 0),
  source        text,
  effective_at  timestamptz not null default now()
);
create index exchange_rates_effective_idx on exchange_rates (effective_at desc);

create table fuel_prices (
  id                 bigint generated always as identity primary key,
  fuel_type          text not null check (fuel_type in ('gasoline_95','gasoline_98','diesel')),
  price_per_20l_lbp  numeric(14,0) not null check (price_per_20l_lbp > 0),
  effective_date     date not null,
  source             text,
  unique (fuel_type, effective_date)
);

-- ---------------------------------------------------------------------
-- Stores & branches
-- ---------------------------------------------------------------------

create table stores (
  id              uuid primary key default gen_random_uuid(),
  slug            text not null unique check (slug ~ '^[a-z0-9-]+$'),
  name_en         text not null,
  name_ar         text not null,
  website         text,
  logo_url        text,
  -- Which worker adapter handles this store, and its (non-secret) config.
  -- Secrets are referenced as ${ENV_VAR} and resolved by the worker at runtime.
  adapter_key     text not null,
  adapter_config  jsonb not null default '{}'::jsonb,
  pricing_scope   text not null default 'chain' check (pricing_scope in ('chain','branch')),
  rate_limit_rps  numeric(5,2) not null default 0.5 check (rate_limit_rps > 0),
  schedule_cron   text not null default '0 */6 * * *',
  is_active       boolean not null default true,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create trigger stores_updated_at before update on stores
  for each row execute function set_updated_at();

create table store_branches (
  id             uuid primary key default gen_random_uuid(),
  store_id       uuid not null references stores(id) on delete cascade,
  external_id    text not null,
  name_en        text not null,
  name_ar        text,
  region_id      smallint references regions(id) on delete set null,
  address        text,
  lat            double precision not null check (lat between 33.0 and 34.8),
  lng            double precision not null check (lng between 35.0 and 36.7),
  geog           extensions.geography(Point, 4326)
                   generated always as (extensions.st_setsrid(extensions.st_makepoint(lng, lat), 4326)::extensions.geography) stored,
  opening_hours  jsonb,
  is_active      boolean not null default true,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (store_id, external_id)
);
create index store_branches_geog_idx on store_branches using gist (geog);
create index store_branches_store_idx on store_branches (store_id) where is_active;
create index store_branches_region_idx on store_branches (region_id);
create trigger store_branches_updated_at before update on store_branches
  for each row execute function set_updated_at();

-- ---------------------------------------------------------------------
-- Scraper runs (needed before offers/flyers reference them)
-- ---------------------------------------------------------------------

create table scraper_runs (
  id              bigint generated always as identity primary key,
  store_id        uuid references stores(id) on delete cascade,
  adapter_key     text not null,
  run_kind        text not null check (run_kind in ('catalog','flyer')),
  status          text not null default 'running'
                    check (status in ('running','success','partial','failed')),
  started_at      timestamptz not null default now(),
  finished_at     timestamptz,
  items_seen      int not null default 0,
  items_upserted  int not null default 0,
  items_failed    int not null default 0,
  new_products    int not null default 0,
  matched_products int not null default 0,
  baseline_items  int,
  alerts          jsonb not null default '[]'::jsonb,
  error_sample    jsonb not null default '[]'::jsonb,
  stats           jsonb not null default '{}'::jsonb
);
create index scraper_runs_store_idx on scraper_runs (store_id, started_at desc);
create index scraper_runs_status_idx on scraper_runs (status, started_at desc);

-- ---------------------------------------------------------------------
-- Products: canonical catalogue + per-store source listings
-- ---------------------------------------------------------------------

create table canonical_products (
  id              uuid primary key default gen_random_uuid(),
  gtin            text unique check (gtin ~ '^[0-9]{14}$'),     -- normalized GTIN-14
  brand           text,
  brand_norm      text not null default '',
  name_en         text,
  name_ar         text,
  name_norm       text not null,
  category_id     int references categories(id) on delete set null,
  base_unit       text not null check (base_unit in ('g','ml','piece')),
  unit_size       numeric(12,3) not null check (unit_size > 0),  -- size of ONE item in base unit
  pack_count      int not null default 1 check (pack_count >= 1),
  total_quantity  numeric(14,3) generated always as (unit_size * pack_count) stored,
  sold_by_weight  boolean not null default false,
  -- everything a shopper might type (both languages + brand), for search
  search_norm     text generated always as
                    (norm_text(coalesce(name_en, '') || ' ' || coalesce(name_ar, '') || ' ' || coalesce(brand, ''))) stored,
  image_url       text,
  is_active       boolean not null default true,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  check (name_en is not null or name_ar is not null)
);
-- Attribute matching key: brand + unit + pack + size (see matching.py)
create index canonical_match_idx on canonical_products (brand_norm, base_unit, pack_count, unit_size);
create index canonical_search_trgm_idx on canonical_products using gin (search_norm extensions.gin_trgm_ops);
create index canonical_category_idx on canonical_products (category_id);
create trigger canonical_products_updated_at before update on canonical_products
  for each row execute function set_updated_at();

create table source_products (
  id                    uuid primary key default gen_random_uuid(),
  store_id              uuid not null references stores(id) on delete cascade,
  external_id           text not null,
  canonical_product_id  uuid references canonical_products(id) on delete set null,
  raw_name              text not null,
  raw_brand             text,
  raw_size_text         text,
  raw_category          text,
  gtin                  text check (gtin ~ '^[0-9]{14}$'),
  url                   text,
  image_url             text,
  match_method          text check (match_method in ('barcode','attributes','fuzzy','manual','new','none')),
  match_confidence      numeric(4,3) check (match_confidence between 0 and 1),
  needs_review          boolean not null default false,
  review_reason         text,
  first_seen_at         timestamptz not null default now(),
  last_seen_at          timestamptz not null default now(),
  unique (store_id, external_id)
);
create index source_products_canonical_idx on source_products (canonical_product_id);
create index source_products_gtin_idx on source_products (gtin) where gtin is not null;
create index source_products_review_idx on source_products (store_id) where needs_review;

-- Current price per (listing, branch). branch_id NULL = chain-wide / online price.
create table product_offers (
  id                    bigint generated always as identity primary key,
  source_product_id     uuid not null references source_products(id) on delete cascade,
  canonical_product_id  uuid references canonical_products(id) on delete set null,
  store_id              uuid not null references stores(id) on delete cascade,
  branch_id             uuid references store_branches(id) on delete cascade,
  price                 numeric(14,2) not null check (price > 0),
  currency              text not null check (currency in ('USD','LBP')),
  price_usd             numeric(12,4) not null check (price_usd > 0),
  regular_price_usd     numeric(12,4) check (regular_price_usd > 0),
  unit_price_usd        numeric(14,6),
  unit_price_basis      text check (unit_price_basis in ('kg','l','piece')),
  in_stock              boolean not null default true,
  stale                 boolean not null default false,
  source_kind           text not null check (source_kind in ('api','html','flyer','manual')),
  scraper_run_id        bigint references scraper_runs(id) on delete set null,
  observed_at           timestamptz not null default now(),
  constraint product_offers_listing_branch_uniq unique nulls not distinct (source_product_id, branch_id)
);
create index product_offers_canonical_idx on product_offers (canonical_product_id, price_usd)
  where in_stock and not stale;
create index product_offers_store_branch_idx on product_offers (store_id, branch_id);
create index product_offers_run_idx on product_offers (scraper_run_id);

-- Append-only history, written only when the price actually changes (trigger below).
create table price_history (
  id                 bigint generated always as identity primary key,
  source_product_id  uuid not null references source_products(id) on delete cascade,
  branch_id          uuid references store_branches(id) on delete cascade,
  price              numeric(14,2) not null,
  currency           text not null,
  price_usd          numeric(12,4) not null,
  observed_at        timestamptz not null default now()
);
create index price_history_listing_idx on price_history (source_product_id, observed_at desc);

create or replace function log_price_change()
returns trigger language plpgsql set search_path = public, extensions as $$
begin
  if tg_op = 'INSERT'
     or new.price is distinct from old.price
     or new.currency is distinct from old.currency then
    insert into price_history (source_product_id, branch_id, price, currency, price_usd, observed_at)
    values (new.source_product_id, new.branch_id, new.price, new.currency, new.price_usd, new.observed_at);
  end if;
  return new;
end $$;
create trigger product_offers_price_history
  after insert or update of price, currency on product_offers
  for each row execute function log_price_change();

-- ---------------------------------------------------------------------
-- Flyers (catalogues) & promotions
-- ---------------------------------------------------------------------

create table flyers (
  id               uuid primary key default gen_random_uuid(),
  store_id         uuid not null references stores(id) on delete cascade,
  source_url       text,
  file_sha256      text not null unique,
  storage_path     text,
  mime_type        text,
  title            text,
  page_count       int,
  valid_from       date,
  valid_until      date,
  status           text not null default 'queued'
                     check (status in ('queued','processing','done','needs_review','failed')),
  items_extracted  int not null default 0,
  items_accepted   int not null default 0,
  llm_calls        int not null default 0,
  scraper_run_id   bigint references scraper_runs(id) on delete set null,
  error            text,
  created_at       timestamptz not null default now(),
  processed_at     timestamptz
);
create index flyers_store_idx on flyers (store_id, created_at desc);

create table promotions (
  id                    uuid primary key default gen_random_uuid(),
  store_id              uuid not null references stores(id) on delete cascade,
  branch_id             uuid references store_branches(id) on delete cascade,  -- NULL = all branches
  canonical_product_id  uuid references canonical_products(id) on delete set null,
  source_product_id     uuid references source_products(id) on delete set null,
  flyer_id              uuid references flyers(id) on delete set null,
  flyer_page            int,
  title                 text not null,
  brand                 text,
  size_text             text,
  promo_type            text not null check (promo_type in ('price_cut','percent_off','multibuy','bogo')),
  promo_price           numeric(14,2),
  currency              text check (currency in ('USD','LBP')),
  promo_price_usd       numeric(12,4) check (promo_price_usd > 0),
  regular_price_usd     numeric(12,4) check (regular_price_usd > 0),
  percent_off           numeric(5,2) check (percent_off > 0 and percent_off <= 95),
  buy_qty               int check (buy_qty >= 1),
  get_qty               int check (get_qty >= 1),
  valid_from            date not null,
  valid_until           date not null,
  dates_inferred        boolean not null default false,
  status                text not null default 'pending_review'
                          check (status in ('pending_review','active','expired','rejected')),
  confidence            numeric(4,3) check (confidence between 0 and 1),
  review_reasons        text[] not null default '{}',
  dedupe_key            text not null unique,
  raw                   jsonb not null default '{}'::jsonb,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  constraint promotions_dates_chk check (valid_until >= valid_from and valid_until - valid_from <= 120),
  constraint promotions_shape_chk check (
       (promo_type = 'price_cut'   and promo_price_usd is not null)
    or (promo_type = 'percent_off' and percent_off is not null)
    or (promo_type = 'multibuy'    and buy_qty >= 2 and promo_price_usd is not null)
    or (promo_type = 'bogo'        and buy_qty >= 1 and get_qty >= 1)
  )
);
create index promotions_active_idx on promotions (store_id, valid_until) where status = 'active';
create index promotions_canonical_idx on promotions (canonical_product_id) where status = 'active';
create index promotions_source_idx on promotions (source_product_id) where status = 'active';
create index promotions_flyer_idx on promotions (flyer_id);
create trigger promotions_updated_at before update on promotions
  for each row execute function set_updated_at();

-- Effective per-unit price of a promotion given the shelf price.
create or replace function promo_unit_price(
  p_type text, p_shelf numeric, p_promo_price numeric, p_percent numeric, p_buy int, p_get int)
returns numeric language sql immutable parallel safe set search_path = public, extensions as $$
  select case p_type
    when 'price_cut'   then least(p_shelf, p_promo_price)
    when 'percent_off' then p_shelf * (1 - p_percent / 100.0)
    when 'multibuy'    then least(p_shelf, p_promo_price / nullif(p_buy, 0))
    when 'bogo'        then p_shelf * p_buy / nullif(p_buy + p_get, 0)
    else p_shelf end
$$;

-- Daily housekeeping (schedule with pg_cron: select cron.schedule('expire-promos','5 0 * * *','select expire_promotions()');)
create or replace function expire_promotions()
returns int language sql set search_path = public, extensions as $$
  with x as (
    update promotions set status = 'expired'
    where status in ('active','pending_review') and valid_until < current_date
    returning 1)
  select count(*)::int from x
$$;

-- ---------------------------------------------------------------------
-- User data
-- ---------------------------------------------------------------------

create table app_admins (
  user_id     uuid primary key references auth.users(id) on delete cascade,
  created_at  timestamptz not null default now()
);

create or replace function is_admin()
returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from app_admins where user_id = auth.uid())
$$;

create table fuel_settings (
  user_id                  uuid primary key references auth.users(id) on delete cascade,
  fuel_type                text not null default 'gasoline_95'
                             check (fuel_type in ('gasoline_95','gasoline_98','diesel')),
  custom_price_per_20l     numeric(14,2) check (custom_price_per_20l > 0),  -- NULL = latest official price
  custom_price_currency    text not null default 'LBP' check (custom_price_currency in ('USD','LBP')),
  consumption_l_per_100km  numeric(4,1) not null default 9.0
                             check (consumption_l_per_100km between 2 and 40),
  home_lat                 double precision check (home_lat between 33.0 and 34.8),
  home_lng                 double precision check (home_lng between 35.0 and 36.7),
  home_label               text,
  display_currency         text not null default 'USD' check (display_currency in ('USD','LBP','BOTH')),
  max_stores               smallint not null default 3 check (max_stores between 1 and 4),
  search_radius_km         numeric(5,1) not null default 15 check (search_radius_km between 1 and 80),
  road_factor              numeric(3,2) not null default 1.35 check (road_factor between 1 and 2.5),
  time_value_usd_per_hour  numeric(6,2) not null default 0 check (time_value_usd_per_hour >= 0),
  updated_at               timestamptz not null default now()
);
create trigger fuel_settings_updated_at before update on fuel_settings
  for each row execute function set_updated_at();

create table baskets (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  name        text not null default 'سلّتي',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index baskets_user_idx on baskets (user_id);
create trigger baskets_updated_at before update on baskets
  for each row execute function set_updated_at();

create table basket_items (
  id                    uuid primary key default gen_random_uuid(),
  basket_id             uuid not null references baskets(id) on delete cascade,
  canonical_product_id  uuid not null references canonical_products(id) on delete cascade,
  quantity              numeric(8,2) not null check (quantity > 0),
  allow_substitutes     boolean not null default true,
  note                  text,
  created_at            timestamptz not null default now(),
  unique (basket_id, canonical_product_id)
);
create index basket_items_basket_idx on basket_items (basket_id);

-- ---------------------------------------------------------------------
-- Read API (called from the web app via supabase.rpc)
-- ---------------------------------------------------------------------

create or replace function search_products(p_query text, p_limit int default 20)
returns table (
  id uuid, name_en text, name_ar text, brand text, base_unit text, unit_size numeric,
  pack_count int, total_quantity numeric, category_slug text, image_url text,
  offers_count bigint, min_price_usd numeric, score real)
language sql stable set search_path = public, extensions as $$
  with q as (select norm_text(p_query) as nq)
  select c.id, c.name_en, c.name_ar, c.brand, c.base_unit, c.unit_size, c.pack_count,
         c.total_quantity, cat.slug, c.image_url,
         count(o.id), min(o.price_usd),
         word_similarity(q.nq, c.search_norm) as score
  from canonical_products c
  cross join q
  left join categories cat on cat.id = c.category_id
  left join product_offers o on o.canonical_product_id = c.id and o.in_stock and not o.stale
  where c.is_active
    and (q.nq <% c.search_norm or c.search_norm like '%' || q.nq || '%')
  group by c.id, cat.slug, q.nq
  order by (count(o.id) > 0) desc, score desc, c.name_norm
  limit least(greatest(p_limit, 1), 50)
$$;

-- Everything the basket optimizer needs, in one round trip:
-- one row per (branch within radius, requested product) with the best applicable promotion.
create or replace function offers_for_basket(
  p_product_ids uuid[], p_lat double precision, p_lng double precision, p_radius_km double precision default 15)
returns table (
  branch_id uuid, store_id uuid, store_slug text, store_name_en text, store_name_ar text,
  branch_name_en text, branch_name_ar text, lat double precision, lng double precision,
  straight_km double precision, canonical_product_id uuid, price_usd numeric,
  regular_price_usd numeric, promo_id uuid, promo_type text, promo_price_usd numeric,
  percent_off numeric, buy_qty int, get_qty int, promo_valid_until date, observed_at timestamptz)
language sql stable set search_path = public, extensions as $$
  with origin as (
    select st_setsrid(st_makepoint(p_lng, p_lat), 4326)::geography as g
  ),
  br as (
    select b.*, st_distance(b.geog, origin.g) / 1000.0 as km
    from store_branches b, origin
    where b.is_active and st_dwithin(b.geog, origin.g, p_radius_km * 1000)
  ),
  best as (
    -- prefer a branch-specific price over the chain-wide one
    select distinct on (br.id, o.canonical_product_id)
           br.id as branch_id, br.store_id, br.name_en as branch_name_en, br.name_ar as branch_name_ar,
           br.lat, br.lng, br.km, o.canonical_product_id, o.source_product_id,
           o.price_usd, o.regular_price_usd, o.observed_at
    from br
    join product_offers o
      on o.store_id = br.store_id and (o.branch_id = br.id or o.branch_id is null)
    where o.canonical_product_id = any (p_product_ids) and o.in_stock and not o.stale
    order by br.id, o.canonical_product_id, (o.branch_id is null), o.price_usd
  )
  select b.branch_id, b.store_id, s.slug, s.name_en, s.name_ar, b.branch_name_en, b.branch_name_ar,
         b.lat, b.lng, b.km, b.canonical_product_id, b.price_usd, b.regular_price_usd,
         p.id, p.promo_type, p.promo_price_usd, p.percent_off, p.buy_qty, p.get_qty, p.valid_until,
         b.observed_at
  from best b
  join stores s on s.id = b.store_id and s.is_active
  left join lateral (
    select pr.* from promotions pr
    where pr.status = 'active'
      and current_date between pr.valid_from and pr.valid_until
      and pr.store_id = b.store_id
      and (pr.branch_id is null or pr.branch_id = b.branch_id)
      and (pr.source_product_id = b.source_product_id or pr.canonical_product_id = b.canonical_product_id)
    order by promo_unit_price(pr.promo_type, b.price_usd, pr.promo_price_usd, pr.percent_off, pr.buy_qty, pr.get_qty)
    limit 1
  ) p on true
$$;

-- Close substitutes: same category & unit, total quantity within ±35%.
create or replace function product_substitutes(p_product_ids uuid[], p_per_product int default 3)
returns table (product_id uuid, substitute_id uuid, name_en text, name_ar text,
               qty_factor numeric, similarity real)
language sql stable set search_path = public, extensions as $$
  select x.product_id, x.substitute_id, x.name_en, x.name_ar, x.qty_factor, x.sim
  from (
    select src.id as product_id, c.id as substitute_id, c.name_en, c.name_ar,
           round(src.total_quantity / c.total_quantity, 3) as qty_factor,
           similarity(src.name_norm, c.name_norm) as sim,
           row_number() over (partition by src.id
                              order by similarity(src.name_norm, c.name_norm) desc) as rn
    from canonical_products src
    join canonical_products c
      on c.id <> src.id and c.is_active
     and c.category_id = src.category_id
     and c.base_unit = src.base_unit
     and c.total_quantity between src.total_quantity * 0.65 and src.total_quantity * 1.35
    where src.id = any (p_product_ids)
      and exists (select 1 from product_offers o
                  where o.canonical_product_id = c.id and o.in_stock and not o.stale)
  ) x
  where x.rn <= p_per_product
$$;

-- Explore page: active deals filtered by region and category.
create or replace function explore_deals(
  p_region_slug text default null, p_category_slug text default null,
  p_sort text default 'discount', p_limit int default 60)
returns table (
  promo_id uuid, store_id uuid, store_slug text, store_name_en text, store_name_ar text,
  canonical_product_id uuid, title text, name_en text, name_ar text, brand text, size_text text,
  category_slug text, promo_type text, promo_price_usd numeric, regular_price_usd numeric,
  percent_off numeric, buy_qty int, get_qty int, discount_pct numeric,
  valid_from date, valid_until date, source text)
language sql stable set search_path = public, extensions as $$
  select p.id, s.id, s.slug, s.name_en, s.name_ar, p.canonical_product_id, p.title,
         c.name_en, c.name_ar, coalesce(c.brand, p.brand), p.size_text, cat.slug,
         p.promo_type, p.promo_price_usd, p.regular_price_usd, p.percent_off, p.buy_qty, p.get_qty,
         round(coalesce(
           p.percent_off,
           case when p.regular_price_usd > 0 then
             100 * (1 - promo_unit_price(p.promo_type, p.regular_price_usd, p.promo_price_usd,
                                         p.percent_off, p.buy_qty, p.get_qty) / p.regular_price_usd)
           end), 1) as discount_pct,
         p.valid_from, p.valid_until,
         case when p.flyer_id is not null then 'flyer' else 'site' end
  from promotions p
  join stores s on s.id = p.store_id and s.is_active
  left join canonical_products c on c.id = p.canonical_product_id
  left join categories cat on cat.id = c.category_id
  where p.status = 'active'
    and current_date between p.valid_from and p.valid_until
    and (p_category_slug is null or cat.slug = p_category_slug
         or cat.parent_id = (select id from categories where slug = p_category_slug))
    and (p_region_slug is null or exists (
          select 1 from store_branches b join regions r on r.id = b.region_id
          where b.store_id = p.store_id and b.is_active and r.slug = p_region_slug
            and (p.branch_id is null or p.branch_id = b.id)))
  order by case when p_sort = 'ending' then extract(epoch from p.valid_until::timestamp) end asc nulls last,
           discount_pct desc nulls last
  limit least(greatest(p_limit, 1), 200)
$$;

create or replace function market_settings()
returns table (usd_lbp_rate numeric, rate_effective_at timestamptz,
               gasoline_95_per_20l_lbp numeric, gasoline_98_per_20l_lbp numeric,
               diesel_per_20l_lbp numeric, fuel_effective_date date)
language sql stable set search_path = public, extensions as $$
  select (select rate from exchange_rates order by effective_at desc limit 1),
         (select effective_at from exchange_rates order by effective_at desc limit 1),
         (select price_per_20l_lbp from fuel_prices where fuel_type = 'gasoline_95' order by effective_date desc limit 1),
         (select price_per_20l_lbp from fuel_prices where fuel_type = 'gasoline_98' order by effective_date desc limit 1),
         (select price_per_20l_lbp from fuel_prices where fuel_type = 'diesel' order by effective_date desc limit 1),
         (select max(effective_date) from fuel_prices)
$$;

-- ---------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------

alter table regions            enable row level security;
alter table categories         enable row level security;
alter table exchange_rates     enable row level security;
alter table fuel_prices        enable row level security;
alter table stores             enable row level security;
alter table store_branches     enable row level security;
alter table canonical_products enable row level security;
alter table source_products    enable row level security;
alter table product_offers     enable row level security;
alter table price_history      enable row level security;
alter table promotions         enable row level security;
alter table flyers             enable row level security;
alter table scraper_runs       enable row level security;
alter table app_admins         enable row level security;
alter table fuel_settings      enable row level security;
alter table baskets            enable row level security;
alter table basket_items       enable row level security;

-- Public catalogue: readable by everyone
create policy public_read on regions            for select using (true);
create policy public_read on categories         for select using (true);
create policy public_read on exchange_rates     for select using (true);
create policy public_read on fuel_prices        for select using (true);
create policy public_read on stores             for select using (is_active);
create policy public_read on store_branches     for select using (is_active);
create policy public_read on canonical_products for select using (is_active);
create policy public_read on product_offers     for select using (true);
create policy public_read on price_history      for select using (true);
create policy public_read on promotions         for select using (status = 'active' or is_admin());

-- Internal: admins only
create policy admin_read on source_products for select using (is_admin());
create policy admin_read on flyers          for select using (is_admin());
create policy admin_read on scraper_runs    for select using (is_admin());
create policy admin_read on app_admins      for select using (is_admin());

-- Personal data: owner only
create policy owner_all on fuel_settings for all
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy owner_all on baskets for all
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy owner_all on basket_items for all
  using (exists (select 1 from baskets b where b.id = basket_id and b.user_id = (select auth.uid())))
  with check (exists (select 1 from baskets b where b.id = basket_id and b.user_id = (select auth.uid())));
