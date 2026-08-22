# AQLUSTAXONA — kunlik post boti

`@aqlustaxonastartap` kanaliga har kuni **ikkita** post chiqaradi.

| Vaqt | Nima chiqadi | Preview |
|---|---|---|
| **08:45** | 365 kunlik startap hikoyalaridan navbatdagisi | yo'q |
| **19:45** | Startap kursi va yangilik **almashib** | faqat yangilikka |
| **Yakshanba 20:30** | Kelgusi 7 kunlik postlar adminga yuboriladi | — |

Har bir postga Gemini (`gemini-2.5-flash-image`) rasm chizadi va ElevenLabs
qisqa audio o'qiydi.

## Kontent

| Fayl | Nima |
|---|---|
| `stories.json` | 365 ta tayyor hikoya. Matn AI tomonidan o'zgartirilmaydi |
| `kurs.json` | 96 ta startap darsi, aniq tartibda. Matn o'zgartirilmaydi |
| yangilik | jonli — Google qidiruvi orqali so'nggi 7 kun xabarlari |

Kurs bloklari: g'oya → mijoz va bozor → MVP → sotuv → narx → marketing →
mahsulot → raqamlar → huquq va soliq → jamoa → sarmoya → operatsiya.

Kurs haftada 3-4 marta chiqadi, ya'ni 96 dars ≈ 7 oyga yetadi.
Hikoyalar har kuni — 365 kun.

## Haftalik ko'rik

Har yakshanba kechqurun adminga keladi:

1. Kelgusi 7 kunlik jadval (qaysi kuni nima chiqishi)
2. O'sha haftaning hikoya va dars matnlari to'liq

Yangilik bu ro'yxatga kirmaydi — u har safar chiqishidan 10 daqiqa oldin
alohida preview bo'lib keladi, «🔄 Qayta qilish» tugmasi bilan.

## Qo'lda ishga tushirish

Actions → *Kunlik post* → **Run workflow**:

- `slot` — `ertalab` (hikoya) yoki `kechqurun` (kurs/yangilik)
- `day` — aniq hikoya raqami
- `dars` — aniq kurs darsi raqami
- `weekly` — haftalik ko'rikni hozir yuborish
- `doctor` — sozlamalarni tekshirish
- `preview_only` — kanalga chiqarmasdan faqat adminga yuborish

## Sozlamalar

**Secrets:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL`, `TELEGRAM_ADMIN_ID`,
`GEMINI_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`

**Variables** (ixtiyoriy): `ERTALAB_VAQT` (08:45), `KECHQURUN_VAQT` (19:45),
`POST_SOURCE` (bo'sh — jadval bo'yicha; `hikoya`/`kurs`/`yangilik`/`ai` bilan
majburan bitta tur), `DRY_RUN`

## Holat

`stories_state.json`:

- `last_sent` — oxirgi chiqqan hikoya raqami
- `kurs_oxirgi` — oxirgi chiqqan dars raqami
- `oxirgi_kechki` — oxirgi kechki post turi (`kurs` yoki `yangilik`)

Boshqa joydan boshlash uchun shu raqamlarni o'zgartiring.

## Uslub

Kanal uslubi `bot.py` ning 2-qismida (`STYLE_GUIDE`, `EXAMPLES`,
`IMAGE_STYLE`). Faqat shu matnlarni tahrirlang — qolgan kodga tegish shart emas.
