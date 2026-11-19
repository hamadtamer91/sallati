# سلّتي — Sallati

منصة لمقارنة أسعار السوبرماركت في لبنان، تبني لك أفضل خطة شراء (متجر واحد / تقسيم ذكي / أقصى توفير مع العروض)
بعد حساب البنزين وصافي التوفير الحقيقي. جمع الأسعار أوتوماتيكي بالكامل: Adapters للمواقع والتطبيقات + قراءة الكتالوجات بالذكاء الاصطناعي.

الشرح الكامل للمعمارية والرياضيات: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## محتوى المشروع

| المجلد | ماذا فيه |
|---|---|
| `supabase/` | قاعدة البيانات: الجداول، الفهارس، الصلاحيات (RLS)، دوال البحث والسلة والعروض، بيانات المناطق والفئات وسعر البنزين |
| `workers/` | محرك الجمع (Python): Adapters، توحيد الأصناف، سعر الوحدة، قراءة الكتالوجات، التنبيهات، 53 اختبار |
| `web/` | الواجهة (Next.js + TypeScript + Tailwind): السلة، المقارنة، العروض، لوحة المراقبة، وخوارزمية السلة والوقود مع 13 اختبار |
| `.github/workflows/` | جدولة السحب أوتوماتيكياً |

## تشغيل الواجهة محلياً (بيانات تجريبية، بدون أي مفاتيح)

```bash
cd web
npm install
npm run dev        # ثم افتح http://localhost:3000
```

## التشغيل الحقيقي (مرة واحدة)

1. **Supabase:** أنشئ مشروعاً، ثم نفّذ `supabase/migrations/*.sql` ثم `supabase/seed.sql` (من SQL Editor أو `supabase db push`).
2. **الواجهة على Vercel:** اربط مجلد `web/` وأضف المتغيرات من `web/.env.example`
   (`NEXT_PUBLIC_SUPABASE_URL`، `NEXT_PUBLIC_SUPABASE_ANON_KEY`، `SUPABASE_SERVICE_ROLE_KEY`، `ADMIN_PASSWORD`).
3. **محرك الجمع على GitHub:** أضف في Secrets: `DATABASE_URL` و `ANTHROPIC_API_KEY` (للكتالوجات فقط)،
   واختيارياً `ALERT_WEBHOOK_URL` أو `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.

## إضافة متجر جديد (بدون تعديل قلب النظام)

```bash
cd workers && pip install -e ".[dev]"
pricewatch adapters                      # الأنواع المتوفرة
# أضف المتجر في workers/config/stores.yaml (أمثلة جاهزة: shopify, woocommerce, json_api, html_css)
pricewatch try --store <slug> --limit 20 # تجربة بدون قاعدة بيانات: يعرض الأسعار والمطابقة وأي تنبيه
pricewatch stores sync                   # بعد active: true
```

إذا كانت منصة المتجر غير مدعومة: انسخ `workers/plugins/example_lb_app_api.py` وعدّل القراءة فقط.

## أوامر مفيدة

```bash
pricewatch run catalog --all             # سحب الأسعار
pricewatch run flyers --all              # قراءة الكتالوجات الجديدة
pricewatch flyer --store <slug> --file offers.pdf
pricewatch expire                        # إنهاء العروض المنتهية
pytest -q                                # اختبارات المحرك
cd ../web && npm test                    # اختبارات الخوارزمية
```

## ملاحظات مهمة

- **قبل تفعيل أي متجر حقيقي** راجع شروط استخدام موقعه و`robots.txt` (النظام يحترمه تلقائياً)، والأفضل طلب ملف أسعار رسمي أو شراكة.
- المتاجر في البيانات التجريبية **وهمية** (سيدر مارت، بيت ماركت…) وأسعارها تقريبية للعرض فقط.
- سعر الصفيحة الافتراضي (2,844,000 ل.ل لبنزين 95، جدول 29 أيلول 2026) وسعر الصرف 89,700 يُحدَّثان من جدولي `fuel_prices` و`exchange_rates`، ويمكن للمستخدم تعديل سعره.
