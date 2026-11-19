-- Reference data. Safe to re-run.

insert into regions (slug, name_en, name_ar, governorate_en, governorate_ar) values
  ('beirut',     'Beirut',      'بيروت',        'Beirut',          'بيروت'),
  ('metn',       'Metn',        'المتن',        'Mount Lebanon',   'جبل لبنان'),
  ('baabda',     'Baabda',      'بعبدا',        'Mount Lebanon',   'جبل لبنان'),
  ('kesrouan',   'Kesrouan',    'كسروان',       'Keserwan-Jbeil',  'كسروان - جبيل'),
  ('jbeil',      'Jbeil',       'جبيل',         'Keserwan-Jbeil',  'كسروان - جبيل'),
  ('aley',       'Aley',        'عاليه',        'Mount Lebanon',   'جبل لبنان'),
  ('chouf',      'Chouf',       'الشوف',        'Mount Lebanon',   'جبل لبنان'),
  ('tripoli',    'Tripoli',     'طرابلس',       'North',           'الشمال'),
  ('zgharta',    'Zgharta',     'زغرتا',        'North',           'الشمال'),
  ('batroun',    'Batroun',     'البترون',      'North',           'الشمال'),
  ('koura',      'Koura',       'الكورة',       'North',           'الشمال'),
  ('akkar',      'Akkar',       'عكار',         'Akkar',           'عكار'),
  ('saida',      'Saida',       'صيدا',         'South',           'الجنوب'),
  ('tyre',       'Tyre',        'صور',          'South',           'الجنوب'),
  ('nabatieh',   'Nabatieh',    'النبطية',      'Nabatieh',        'النبطية'),
  ('zahle',      'Zahle',       'زحلة',         'Bekaa',           'البقاع'),
  ('west-bekaa', 'West Bekaa',  'البقاع الغربي', 'Bekaa',           'البقاع'),
  ('baalbek',    'Baalbek',     'بعلبك',        'Baalbek-Hermel',  'بعلبك - الهرمل')
on conflict (slug) do nothing;

insert into categories (slug, name_en, name_ar, sort_order) values
  ('dairy',      'Dairy & Eggs',        'ألبان وأجبان وبيض', 10),
  ('bakery',     'Bakery',              'مخبوزات',          20),
  ('pantry',     'Pantry',              'مؤونة',            30),
  ('beverages',  'Beverages',           'مشروبات',          40),
  ('produce',    'Fruits & Vegetables', 'خضار وفواكه',      50),
  ('meat',       'Meat & Poultry',      'لحوم ودواجن',      60),
  ('frozen',     'Frozen',              'مثلّجات',          70),
  ('snacks',     'Snacks & Sweets',     'سناكس وحلويات',    80),
  ('household',  'Household',           'منظفات ومنزل',     90),
  ('personal',   'Personal Care',       'عناية شخصية',      100),
  ('baby',       'Baby',                'أطفال',            110)
on conflict (slug) do nothing;

insert into categories (slug, parent_id, name_en, name_ar, sort_order)
select v.slug, p.id, v.name_en, v.name_ar, v.sort_order
from (values
  ('milk',        'dairy',     'Milk',              'حليب',          11),
  ('cheese',      'dairy',     'Cheese',            'أجبان',          12),
  ('labneh-yogurt','dairy',    'Labneh & Yogurt',   'لبنة ولبن',      13),
  ('rice-grains', 'pantry',    'Rice & Grains',     'أرز وحبوب',      31),
  ('oil',         'pantry',    'Cooking Oil',       'زيوت',           32),
  ('water',       'beverages', 'Water',             'مياه',           41),
  ('soft-drinks', 'beverages', 'Soft Drinks',       'مشروبات غازية',  42),
  ('detergents',  'household', 'Detergents',        'مساحيق وسوائل',  91),
  ('tissues',     'household', 'Tissues & Paper',   'محارم وورقيات',  92)
) as v(slug, parent_slug, name_en, name_ar, sort_order)
join categories p on p.slug = v.parent_slug
on conflict (slug) do nothing;

-- Official rate used by fuel stations / Ministry of Energy tables (update via worker or admin).
insert into exchange_rates (rate, source, effective_at)
select 89700, 'MoEW fuel table reference rate', '2026-09-29T00:00:00+03'
where not exists (select 1 from exchange_rates);

-- Ministry of Energy & Water table of 2026-09-29 (as reported by Al-Modon). Update weekly.
insert into fuel_prices (fuel_type, price_per_20l_lbp, effective_date, source) values
  ('gasoline_95', 2844000, '2026-09-29', 'MoEW table via almodon.com'),
  ('gasoline_98', 2862000, '2026-09-29', 'MoEW table via almodon.com')
on conflict (fuel_type, effective_date) do nothing;
