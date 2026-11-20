-- Explore page: besides flyer promotions, show discounts that stores display on their own
-- website (current price below the struck-through "was" price). These have no end date.

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
  with promo as (
    select p.id, p.store_id, p.branch_id, p.canonical_product_id, p.title, p.brand, p.size_text,
           p.promo_type, p.promo_price_usd, p.regular_price_usd, p.percent_off, p.buy_qty, p.get_qty,
           round(coalesce(p.percent_off,
             case when p.regular_price_usd > 0 then
               100 * (1 - promo_unit_price(p.promo_type, p.regular_price_usd, p.promo_price_usd,
                                           p.percent_off, p.buy_qty, p.get_qty) / p.regular_price_usd)
             end), 1) as discount_pct,
           p.valid_from, p.valid_until,
           case when p.flyer_id is not null then 'flyer' else 'site' end as source
    from promotions p
    where p.status = 'active' and current_date between p.valid_from and p.valid_until
  ),
  site as (
    select distinct on (o.source_product_id)
           o.source_product_id as id, o.store_id, o.branch_id, o.canonical_product_id,
           sp.raw_name as title, sp.raw_brand as brand, sp.raw_size_text as size_text,
           'price_cut'::text as promo_type, o.price_usd as promo_price_usd, o.regular_price_usd,
           null::numeric as percent_off, null::int as buy_qty, null::int as get_qty,
           round(100 * (1 - o.price_usd / o.regular_price_usd), 1) as discount_pct,
           o.observed_at::date as valid_from, null::date as valid_until, 'site'::text as source
    from product_offers o
    join source_products sp on sp.id = o.source_product_id
    where o.regular_price_usd > o.price_usd and o.in_stock and not o.stale
      and not exists (select 1 from promo p
                      where p.store_id = o.store_id and p.canonical_product_id = o.canonical_product_id)
    order by o.source_product_id, o.price_usd
  ),
  deals as (select * from promo union all select * from site)
  select d.id, s.id, s.slug, s.name_en, s.name_ar, d.canonical_product_id, d.title,
         c.name_en, c.name_ar, coalesce(c.brand, d.brand), d.size_text, cat.slug,
         d.promo_type, d.promo_price_usd, d.regular_price_usd, d.percent_off, d.buy_qty, d.get_qty,
         d.discount_pct, d.valid_from, d.valid_until, d.source
  from deals d
  join stores s on s.id = d.store_id and s.is_active
  left join canonical_products c on c.id = d.canonical_product_id
  left join categories cat on cat.id = c.category_id
  where (p_category_slug is null or cat.slug = p_category_slug
         or cat.parent_id = (select id from categories where slug = p_category_slug))
    and (p_region_slug is null or exists (
          select 1 from store_branches b join regions r on r.id = b.region_id
          where b.store_id = d.store_id and b.is_active and r.slug = p_region_slug
            and (d.branch_id is null or d.branch_id = b.id)))
  order by case when p_sort = 'ending' then d.valid_until end asc nulls last,
           d.discount_pct desc nulls last
  limit least(greatest(p_limit, 1), 200)
$$;
