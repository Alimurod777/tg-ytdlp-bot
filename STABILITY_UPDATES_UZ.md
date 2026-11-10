# Botimizga moslashtirilgan yangilanishlar

2026-11-10 kuni `chelaxian/tg-ytdlp-bot` repozitoriysining `e84072b`
(2026-11-08) versiyasi bizning `main` tarmog‘imizga birlashtirildi.
Oldingi 2026-11-05 integratsiyasi ham shu versiyada mavjud.

## Olingan imkoniyatlar

- Cookie manbalarini tekshirish, xatoda browser sessiyasiga murojaat qilish,
  YouTube bot-check xatosida cookie urinishini o‘tkazib yubormaslik.
- Cookie, token va proksi parollarini loglarda yashirish; cookie URL tekshiruvi.
- Rasm yuklashdan keyin foydalanuvchining format, proksi, subtitr va boshqa
  sozlamalarini saqlash; `/args` parametrlarini yt-dlp’ga yetkazish.
- `/clean` bilan bekor qilish, HLS tarmoq kutish vaqti, atomar foydalanuvchi
  yuklash slotlari, navbatlar va hajmni davomiylik orqali baholash.
- Playlistning oxiridan diapazonlar, har bir audio uchun alohida muqova,
  nisbatni saqlagan holda videoning o‘rtasidan kadr olish.
- Rasmlarni Telegram media sifatida yuborish, katta rasmlarni tayyorlash,
  parallel `/img` ishlarini ajratish va yuborilmagan fayllarni saqlash.
- `/split`, `/link`, `/list`, `/mediainfo`, `/subs`, `/dubs`, `/lang`,
  `/keyboard` tuzatishlari; keshni guruhlab yozish va dublyajni alohida keshlash.
- NSFW qidiruvini oldindan kompilyatsiya qilish, progress xabarlarini cheklash,
  guruhlarni butunlay ban qilmaslik va unblock’da anti-bot tarixini tozalash.
- Yangi Telegram obyektlariga moslik, ixtiyoriy dashboard va browser paneli,
  ixtiyoriy WARP IPv6 almashtirish, SQLite/PostgreSQL qo‘llab-quvvatlashi.

## Bizdagi moslamalar

- Guruhda video sifati **480p–720p**. Zaxira urinishlar va jonli efir ham
  shu chegarani saqlaydi. Kerakli format bo‘lmasa, 144p yuborilmaydi.
- Guruhdagi oddiy reply videoni qayta yubormaydi; caption tahriri lichkada ishlaydi.
- Jonli efirda oblojka video deb yuborilmaydi, nol davomiylik va bo‘sh natija
  muvaffaqiyat deb ko‘rsatilmaydi, yuklangan efir qayta yuborilmaydi.
- Barcha tillarda begona bot/kanal reklamalari va `/help`dagi eski repo tugmalari yo‘q.
- Yangilash manbasi `Alimurod777/tg-ytdlp-bot`, tarmoq esa mavjud tanlovga mos.
- `.env`, Alwaysdata launcher, servis yo‘llari va cookie diagnostikasi saqlangan.
- `USE_FIREBASE=false` bo‘lsa, eski `dump.json` **o‘z-o‘zidan boshqa bazaga
  ko‘chirilmaydi**: `DB_ENGINE=json` standart bo‘lib qoladi.
- Dashboard sozlamalari `.env`ni tahrirlamasdan
  `CONFIG/.dashboard_overrides.json`ga yoziladi; bot startida ham qo‘llanadi.

## Ixtiyoriy qismlar

Bot uchun `requirements.txt` yetarli. Dashboard ishlatilsa, qo‘shimcha:

```bash
venv/bin/python -m pip install -r requirements-dashboard.txt
bash scripts/start_alwaysdata.sh dashboard
```

Browser uchun serverda Chromium/Chrome kerak. `.env`da
`BROWSER_SESSION_ENABLED=true` va kerak bo‘lsa `BROWSER_EXECUTABLE` yoziladi.
Admin panelning Browser bo‘limida kerakli xizmatga kiriladi; browserga kirilmagan
bo‘lsa, odatdagi cookie manbalari ishlaydi. `BROWSER_COOKIES_ADMIN_ONLY=true`
browser cookie’larini adminlargagina cheklaydi. Ularni boshqa foydalanuvchilar
uchun ishlatish ushbu sozlama orqali alohida boshqariladi.

WARP standart holatda o‘chiq (`WARP_PER_TASK=false`). Uni ishga tushirish
alohida wireproxy/WARP profillari va tarmoq sozlamalarini talab qiladi;
Alwaysdata’da bot uchun majburiy emas.

SQLite WAL tarmoq disklariga mos emas. Alwaysdata’dagi joriy NFS katalogida
JSON bazasi saqlanadi; PostgreSQL yoki mahalliy diskka o‘tish alohida migratsiya.
Hech qanday migratsiya, browser login yoki WARP xizmati avtomatik ishga tushirilmadi.

## Tekshirish va ishga tushirish

Alwaysdata’da venv ichidagi ffmpeg/ffprobe topilishi uchun PATH va SQL testlari
uchun mahalliy vaqtinchalik disk ishlatiladi:

```bash
export PATH="$PWD/venv/bin:$HOME/bin:$HOME/.local/bin:$PATH"
TMPDIR=/tmp venv/bin/python scripts/run_offline_smoke.py
TMPDIR=/tmp venv/bin/python scripts/check_env_config.py
TMPDIR=/tmp venv/bin/python scripts/check_cookie_diagnostics.py
venv/bin/python scripts/check_progress_edits.py
venv/bin/python -m pip check
bash -n scripts/start_alwaysdata.sh scripts/engines_updater.sh scripts/update_bgutil_provider.sh
```

2026-11-10 tekshiruvi: 60/60 offline to‘plam, 9 ta konfiguratsiya,
26 ta cookie diagnostikasi va 5 ta progress testi o‘tdi. `pip check`, Python
kompilyatsiyasi, shell sintaksisi va Telegram’ga ulanmasdan `magic` importi ham o‘tdi.

Offline testlar real YouTube/Telegram yuklashini yoki haqiqiy browser loginini
kafolatlamaydi. Ishlayotgan Alwaysdata xizmatiga tatbiq etish uchun paneldagi
Services orqali yangilangan kod bilan restart kerak.
