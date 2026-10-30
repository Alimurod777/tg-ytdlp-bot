# Cookie tekshiruvi

Cookie TXT faylni botning shaxsiy chatiga hujjat sifatida yuboring. Bot faylni
tekshiradi, to'g'ri faylni saqlaydi va natijani chiqaradi. Docker kerak emas.
Qayta tekshirish:

```text
/check_cookie
```

Ma'lum bir YouTube videosi uchun tekshirish:

```text
/check_cookie https://www.youtube.com/watch?v=VIDEO_ID
```

`VIDEO_ID` o'rniga haqiqiy 11 belgili YouTube video ID yozing yoki oddiy havolani
nusxalang. `youtu.be`, `shorts` va `live` havolalari ham qabul qilinadi. Havola
berilmasa `YOUTUBE_COOKIE_TEST_URL` ishlatiladi.

Natija uch xil dalilni ajratadi:

- **Fayl:** Netscape sarlavhasi, har bir qatorning 7 ta TAB ustuni, domen, bayroqlar,
  yo'l, Unix muddati tekshiriladi. Xatoli qator raqami ko'rsatiladi. `#HttpOnly_`
  yozuvlari ham hisoblanadi. Muddati `0` yoki bo'sh bo'lsa, bu sessiya cookie'si.
- **Akkaunt:** shu cookie va proxy bilan YouTube sahifasi so'raladi. Faqat server
  bergan `ytcfg` ichidagi `LOGGED_IN=true` login sessiyasini tasdiqlaydi.
  `false` bo'lsa akkauntga kirilmagan; bu avtomatik ravishda cookie eskirganini
  anglatmaydi. Holat kelmasa yoki so'rov bajarilmasa, tasdiq berilmaydi.
- **Video:** yt-dlp audio/video formatlarini ochishga urinadi. Faqat sarlavha,
  rasm yoki DRM format chiqishi muvaffaqiyat hisoblanmaydi. To'liq video yuklanmaydi;
  format topilishi butun videoning yuklanishiga yoki akkaunt loginiga kafolat bermaydi.

Quyidagi sabablar alohida ko'rsatiladi: muddati tugagan cookie, buzilgan fayl,
login talabi, yosh cheklovi, private video, pullik a'zolik, o'chirilgan video,
mualliflik cheklovi, hudud cheklovi, CAPTCHA/anti-bot, 403, 429/402, DNS,
timeout, TLS, proxy, 5xx, JavaScript va PO token muammosi. Har bir natijada
nima qilish kerakligi tushuntiriladi. Server aniq sabab bermasa, taxmin haqiqat
sifatida ko'rsatilmaydi.

Boshqa sayt cookie'lari uchun **fayl formati va muddati** tekshiriladi. Ularning
login sessiyasi bu buyruq bilan jonli tasdiqlanmaydi; natijada shu cheklov ochiq
aytiladi. Sinash uchun o'sha saytning havolasini botga yuboring.

Buzilgan yoki butunlay muddati tugagan yangi fayl oldingi cookie faylni almashtirmaydi.
Cookie manbalaridan avtomatik yangilashda ham nomzod fayl vaqtinchalik joyda sinaladi.
DNS, timeout, 403, CAPTCHA yoki format xatosi sabab oldingi fayl o'chirilmaydi.
Qo'lda tekshiruv eski boolean keshdan foydalanmaydi. Yangi fayl yuborilganda
oldingi tekshiruv keshlari tozalanadi. Tekshiruv cookie faylning nusxasida ishlaydi,
shuning uchun yt-dlp original faylni o'zgartirmaydi.

Natijalar o'zbekcha yoki inglizcha beriladi (`/lang` orqali o'zbek tilini tanlang).
Cookie qiymatlari diagnostika matniga yoki yangi diagnostika loglariga qo'shilmaydi.

Offline regressiya tekshiruvlari:

```bash
python scripts/check_cookie_diagnostics.py -v
```

Bu testlar server javoblari va xatolarini simulyatsiya qiladi; haqiqiy akkauntning
cookie'sini talab qilmaydi. `yt-dlp` o'rnatilgan bo'lsa kutubxona integratsiyasi ham sinaladi.
