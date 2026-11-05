# Barqarorlik yangilanishlari

2026-11-05 kuni upstream `chelaxian/tg-ytdlp-bot` loyihasidan tanlab olindi.
Bu yangilanish yuklash vaqtida buyruqlarning javobsiz qolishi, diskka log yozish
sababli kutish va takroriy xatolarni kamaytiradi.

## Qo‘shilgan tuzatishlar

- Buyruqlar/menyular va yuklashlar uchun alohida ishchi oqimlar; band navbat haqida xabar.
- Upload progress callback’larini alohida bajarish; upload davom etayotgan bo‘lsa
  kutishni uzaytirish, to‘xtab qolgan yoki maksimal vaqtga yetgan uploadni cheklash.
- Loglarni chegaralangan navbat orqali fonda yozish. Navbat to‘lsa, botni
  kutdirish o‘rniga ortiqcha loglar tashlanadi.
- Cookie fayllarini ishlatishdan oldin tekshirish, atomar yozish va aniq
  xatolarda foydasiz qayta urinishlarni to‘xtatish.
- Ishlayotgan yuklash fayllarini tozalashdan himoya qilish; eskirgan vaqtinchalik
  kataloglarni botning o‘zida tekshirib tozalash.
- Telegram kanal konstruktoriga moslik, ChannelGuard amallarini event loop’dan
  chiqarish, shutdown va restart kutishlarini chegaralash.
- Proksi login/parolini logda ko‘rsatmaslik, bo‘sh proksi sozlamalarini qabul qilish.
- Engine yangilashda loyiha venv’idan foydalanish va bir vaqtda ikki pip
  jarayonini boshlamaslik. Lock loyiha venv’ida; TgCrypto paketi requirements’ga mos.
- Kod yangilash manbasi bizning `Alimurod777/tg-ytdlp-bot` forkimizga yo‘naltirildi.

## Saqlangan va kiritilmagan qismlar

Bizdagi `.env` konfiguratsiyasi, Alwaysdata launcher, cookie diagnostikasi,
FloodWait progress tuzatishi va arxivlangan HLS’ni parallel yuklash saqlandi.

SQLite/PostgreSQL migratsiyasi, kunlik kvotalar, yangi playlist/rasm limitlari,
ustuvor navbat, yangi dashboard va WARP olinmadi. Mavjud dashboard’dagi xavfli
qo‘lda fayl tozalash amali active download’larni himoyalash uchun cheklangan.

## Tekshirish

```bash
venv/bin/python scripts/run_offline_smoke.py
venv/bin/python scripts/check_env_config.py
venv/bin/python scripts/check_cookie_diagnostics.py
venv/bin/python scripts/check_progress_edits.py
venv/bin/python -m pip check
bash -n scripts/start_alwaysdata.sh scripts/engines_updater.sh scripts/update_bgutil_provider.sh
```

Telegram sessiyasiga ulanmasdan, vaqtinchalik katalogda haqiqiy `magic` importi
ham tekshirildi. Offline tekshiruvlar real YouTube/Telegram yuklash tezligini
kafolatlamaydi. Ishlayotgan Alwaysdata xizmatiga tatbiq etish uchun paneldagi
**Services** orqali restart kerak.

## Upstream manbalari

To‘liq/moslashtirib olingan commitlar: `055bf25`, `0516767`, `4915b08`,
`8117d79`, `9468786`, `e223ca0`, `3d139d4`, `24e7876`, `409b86d`,
`21e7ce3`, `831aab9`, `b81d50b`, `21d4763`, `c7f29c9`.
`fc0f06a` commitidan faqat botning maxfiy proksi ma’lumotlarini logdan yashirish
qismlari va ularga tegishli regressiya tekshiruvi olindi.
