# Alwaysdata'da ishga tushirish

## 1. Repozitoriy va Python

Alwaysdata panelida **Environment → Python** orqali Python **3.11** ni tanlang.
SSH orqali hisobingizga ulaning. Quyidagi `ACCOUNT` o'rniga Alwaysdata hisob nomini yozing:

```bash
ssh ACCOUNT@ssh-ACCOUNT.alwaysdata.net
git clone https://github.com/Alimurod777/tg-ytdlp-bot.git
cd tg-ytdlp-bot
PYTHON_VERSION=3.11 python -m venv venv
venv/bin/python -m pip install --upgrade pip
venv/bin/python -m pip install -r requirements.txt
```

Alwaysdata'da virtual muhitni yaratish uchun `python` ishlatiladi, `python3.11` emas.
Botni Docker va WARP xizmatlarisiz Python orqali ishga tushiramiz.

## 2. .env faylni yuklash

Tayyor `.env` faylingizni SFTP bilan `/home/ACCOUNT/tg-ytdlp-bot/.env` manziliga yuklang.
Yoki SSH orqali namunasidan yarating:

```bash
cp .env.example .env
nano .env
chmod 600 .env
```

Quyidagi qiymatlarni o'zingiznikiga almashtiring:

```dotenv
BOT_NAME=your_bot_username
API_ID=12345678
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
ADMIN=123456789
LOGS_ID=-1001234567890
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD='your_long_random_password'
USE_FIREBASE=false
YOUTUBE_POT_ENABLED=false
```

Botni `LOGS_ID` kanaliga admin qiling. PO token provider alohida sozlanmaguncha
`YOUTUBE_POT_ENABLED=false` qoldiring. Ayrim YouTube yuklashlari uchun amaldagi
cookies yoki qo'shimcha provider kerak bo'lishi mumkin.
Docker cookie serveri ham ishga tushmaydi: zarur `*_COOKIE_URL` manzillarini
o'zingizning yopiq cookie manbangizga almashtiring yoki botning cookie buyruqlaridan foydalaning.

Sozlamalar ustuvorligi: **jarayon environment qiymatlari → `.env` → `CONFIG/config.py`
→ standart sozlamalar**. Ya'ni `.env` dagi qiymat Python config'dagi qiymatdan ustun.
Ikkalasi bilan ham ishlaydi; `.env` yetarli bo'lsa `CONFIG/config.py` shart emas.
Eski usul uchun `cp CONFIG/_config.py CONFIG/config.py` qilib, Python config'ni to'ldirish mumkin.
`.env` ichida bo'sh yozilgan qiymat ham override hisoblanadi: config'dan olish uchun
o'sha qatorni olib tashlang. `.env` va maxfiy Python config Git'ga kiritilmaydi.

Cookie yuborilgach avtomatik diagnostika chiqadi. Qayta sinash uchun
`/check_cookie` yoki `/check_cookie YOUTUBE_VIDEO_URL` yuboring.
[Cookie natijalari va sabablar haqida](COOKIE_DIAGNOSTICS_UZ.md).

## 3. Tizim dasturlarini tekshirish

```bash
export PATH="$HOME/bin:$HOME/.local/bin:$PATH"
command -v ffmpeg
command -v ffprobe
command -v mediainfo
node --version
bash scripts/start_alwaysdata.sh check
```

FFmpeg va FFprobe video/audio ishlovi uchun, MediaInfo `/mediainfo` uchun kerak.
YouTube JavaScript masalalarini yechish uchun Node.js **22+** ishlating
(Alwaysdata panelida Node.js versiyasini tanlang). Paketlar mavjud bo'lmasa,
Alwaysdata support'dan so'rang yoki hisobingizning `~/bin` katalogiga mos Linux
binary o'rnating va xizmat/sayt environment'ida shu katalogni `PATH` ga qo'shing.
Shared hosting'da `sudo apt install` ishlatishga tayanmang.

Avval SSH orqali sinang:

```bash
bash scripts/start_alwaysdata.sh bot
```

Bot javob bersa `Ctrl+C` bilan to'xtating; keyin doimiy xizmat yarating.
Bir vaqtning o'zida ayni session fayli bilan ikki bot jarayonini ishga tushirmang.

## 4. Botni doimiy ishlatish

**Advanced → Services → Add a service**:

| Maydon | Qiymat |
| --- | --- |
| Command | `bash /home/ACCOUNT/tg-ytdlp-bot/scripts/start_alwaysdata.sh bot` |
| Working directory | `/home/ACCOUNT/tg-ytdlp-bot` |
| Environment | `PYTHON_VERSION=3.11` |

Bot Telegram bilan polling orqali ishlaydi, domen yoki kiruvchi HTTP port kerak emas.
Xizmat foreground'da ishlaydi; Alwaysdata to'xtagan jarayonni qayta ishga tushiradi.
Loglar `/home/ACCOUNT/admin/logs/services/` ichida bo'ladi.

**Services faqat pullik tariflarda mavjud.** Bepul hisobda SSH orqali qo'lda sinash
24/7 xizmat kafolatini bermaydi; doimiy bot uchun Services mavjud tarif yoki VPS kerak.
Oddiy web sayt yaratishning o'zi botni doimiy polling xizmatiga aylantirmaydi.

## 5. Dashboard sayti (ixtiyoriy)

**Web → Sites → Add a site**:

| Maydon | Qiymat |
| --- | --- |
| Type | `User program` |
| Addresses | `ACCOUNT.alwaysdata.net` yoki o'z domeningiz |
| Command | `bash /home/ACCOUNT/tg-ytdlp-bot/scripts/start_alwaysdata.sh dashboard` |
| Working directory | `/home/ACCOUNT/tg-ytdlp-bot` |
| Environment | `PYTHON_VERSION=3.11` |

Skript Alwaysdata ajratgan `IP` va `PORT` orqali Uvicorn'ni ishga tushiradi.
WSGI turini tanlamang: dashboard FastAPI/ASGI ishlatadi.
Bot xizmati va dashboard alohida jarayonlardir; saytni ochish botni ishga tushirmaydi.
Dashboard loglari `/home/ACCOUNT/admin/logs/sites/` katalogida bo'ladi.
Dashboard'ning Docker boshqaruv tugmalari bu Python o'rnatishdagi bot xizmatini boshqarmaydi;
botni Alwaysdata **Services** panelidan restart qiling.

## 6. Yangilash

```bash
cd ~/tg-ytdlp-bot
git pull --ff-only
venv/bin/python -m pip install -r requirements.txt
bash scripts/start_alwaysdata.sh check
```

Keyin Alwaysdata panelida bot xizmatini va dashboard saytini restart qiling.
`.env` o'zgarganda ham shu restart kerak. Docker'da esa `env_file` qiymatlarini
qayta olish uchun konteynerlarni `docker compose up -d --force-recreate` bilan yangilang.

Rasmiy hujjatlar:

- [Python va virtual muhit](https://help.alwaysdata.com/en/docs/web-hosting/languages/python/configuration/)
- [Doimiy xizmatlar va tarif cheklovi](https://help.alwaysdata.com/en/docs/web-hosting/services/)
- [User program sayti](https://help.alwaysdata.com/en/docs/web-hosting/sites/http-servers/user-program/)
