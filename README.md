# AQLUSTAXONA — kunlik post boti

`@aqlustaxonastartap` kanaliga har kuni bitta post chiqaradi. Ikkita rejimi bor.

| Rejim | `POST_SOURCE` | Nima qiladi |
|---|---|---|
| **365 hikoya** (hozirgi) | `stories` | `stories/` papkasidagi tayyor hikoyani navbat bilan chiqaradi |
| AI post | `ai` | Gemini har kuni yangi post yozadi (eski rejim) |

Rejimni almashtirish: **Settings → Secrets and variables → Actions → Variables**
bo'limida `POST_SOURCE` o'zgaruvchisini `stories` yoki `ai` qilib qo'ying.
Hech narsa qo'ymasangiz — `stories` ishlaydi.

## 365 hikoya rejimi

`stories/001.html` … `stories/365.html` — Word fayldan olingan 365 ta tayyor post.
Matn ichida `KUN 001` kabi belgilar **yo'q**, faqat postning o'zi.
Format Telegram HTML (`<b>`, `<i>`, `<a>`), o'rtacha ~1840 belgi.

Kunlik oqim:

1. Navbatdagi hikoya `stories_state.json` dagi `last_sent` bo'yicha olinadi.
2. Gemini shu hikoyaga **rasm** chizadi, ElevenLabs qisqa **audio** o'qiydi.
3. 19:35 da adminga preview keladi.
4. 19:45 da kanalga chiqadi va `last_sent` bittaga oshadi.

**Hikoya matni hech qachon o'zgartirilmaydi** — AI uni qayta yozmaydi, QA
tekshiruvidan o'tkazmaydi. Preview'dagi «🔄 Rasm/audioni qayta qilish» tugmasi
faqat rasm va audioni yangilaydi.

### Foydali amallar

- **Aniq kunni chiqarish:** Actions → *Kunlik post* → **Run workflow** →
  `day` maydoniga raqam yozing (masalan `42`).
- **Qaysi kundan boshlash:** `stories_state.json` dagi `last_sent` ni
  o'zgartiring. `0` — birinchi kundan.
- **Sinab ko'rish:** `preview_only` ni belgilang — post adminga keladi, kanalga
  chiqmaydi.
- **Holatni ko'rish:** `doctor` ni belgilang — nechta hikoya bor, oxirgisi qaysi
  kun edi, keyingisi nima.

Lokal sinov:

```bash
POST_SOURCE=stories python bot.py --day 1 --preview-only --now
```

## Fayllar

| Fayl | Nima |
|---|---|
| `bot.py` | Butun bot — sozlamalar, agentlar, oqim |
| `stories/001.html`…`365.html` | 365 ta tayyor post |
| `stories/index.json` | Ro'yxat: raqam, sarlavha, belgi soni |
| `stories_state.json` | Oxirgi chiqqan kun |
| `history.json` | Chiqqan postlar tarixi |
| `.github/workflows/daily_post.yml` | Kunlik jadval |

## Sozlamalar

**Secrets:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL`, `TELEGRAM_ADMIN_ID`,
`GEMINI_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`

**Variables:** `POST_SOURCE` (`stories`), `PUBLISH_TIME` (`19:45`),
`RUBRIC`, `DRY_RUN`
