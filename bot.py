#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AQLUSTAXONA — avtomatik post boti (bitta faylli versiya)

Oqim:
  1-agent  Google'dan mavzu topadi
  2-agent  postni kanal uslubida yozadi
  3-agent  rasm chizadi (Gemini / Nano Banana)
  4-agent  audio yozadi (ElevenLabs)
  5-agent  sifat nazoratidan o'tkazadi
  19:35    adminga preview + "Qayta qilish" tugmasi
  19:45    hech narsa bosilmasa -> 6-agent kanalga chiqaradi
           tugma bosilsa -> hammasi noldan qayta yaratiladi, yangi 10 daqiqa,
           keyin vaqt o'tgan bo'lsa ham chiqadi

Uslubni o'zgartirish uchun pastdagi STYLE_GUIDE / EXAMPLES / IMAGE_STYLE
matnlarini tahrirlang. Boshqa joyga tegish shart emas.
"""

import argparse
import base64
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ══════════════════════════════════════════════════════════════════
#  1-QISM — SOZLAMALAR
# ══════════════════════════════════════════════════════════════════

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
BUILD.mkdir(exist_ok=True)
HISTORY_FILE = ROOT / "history.json"


def _req(name):
    v = os.getenv(name, "").strip()
    if not v:
        raise SystemExit(
            f"\n[XATO] Sozlama topilmadi: {name}\n"
            f"       GitHub -> Settings -> Secrets and variables -> Actions\n"
            f"       bo'limiga '{name}' nomli secret qo'shing.\n"
        )
    return v


def _int(name, default):
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


TELEGRAM_BOT_TOKEN = _req("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL = _req("TELEGRAM_CHANNEL")
TELEGRAM_ADMIN_ID = _req("TELEGRAM_ADMIN_ID")
GEMINI_API_KEY = _req("GEMINI_API_KEY")

GEMINI_TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "").strip() or "gemini-2.5-flash"
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "").strip() or "gemini-2.5-flash-image"

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "").strip() or "JBFqnCBsd6RMkjVDRZzb"
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "").strip() or "eleven_v3"

PUBLISH_TIME = os.getenv("PUBLISH_TIME", "").strip() or "19:45"
PREVIEW_LEAD = _int("PREVIEW_LEAD_MINUTES", 10)
TZ = ZoneInfo(os.getenv("TIMEZONE", "").strip() or "Asia/Tashkent")
RUBRIC = os.getenv("RUBRIC", "").strip() or "Claude maslahatlar"
MAX_REDO = _int("MAX_REDO", 5)
DRY_RUN = os.getenv("DRY_RUN", "0").strip() == "1"

CAPTION_LIMIT = 1024      # Telegram rasm captioni limiti
MESSAGE_LIMIT = 4000      # Telegram oddiy xabar limiti
MIN_CHARS, MAX_CHARS = 450, 850
ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a"}
REDO_DATA = "redo"


# ══════════════════════════════════════════════════════════════════
#  2-QISM — USLUB (shu yerni tahrirlang)
# ══════════════════════════════════════════════════════════════════

STYLE_GUIDE = """
## Auditoriya
MUTLAQ BOSHLOVCHILAR. Claude'ni endi ochgan yoki umuman ochib ko'rmagan odamlar.
Ular "prompt", "kontekst", "token" kabi so'zlarni bilmasligi mumkin.
Hech qanday atamani izohsiz ishlatmang — birinchi marta qavs ichida oddiy tilda
tushuntiring. Masalan: "kontekst (ya'ni Claude eslab turadigan suhbat qismi)".

## Til
- O'zbek tili, lotin yozuvi. To'g'ri apostrof: o', g'
- Suhbat ohangida, jonli. "siz" bilan murojaat
- Rus so'zlari ishlatilmaydi. Ingliz atamalari faqat muqarrar bo'lsa va izoh bilan

## Ohang
- Do'stona, kamtar. O'rgatuvchi emas — ULASHUVCHI
- "Menimcha", "o'ylashimcha", "menga yoqqani" — shaxsiy iboralar yaxshi
- Reklama ohangi yo'q. "Zo'r!", "Ajoyib imkoniyat!" — bular yo'q
- Katta va'dalar yo'q. "10 barobar tezlashasiz" tipidagi gaplar yo'q

## Struktura
1. Sarlavha — 1 qator, 40-70 belgi, nuqtasiz, emojisiz
2. Hook — 1-2 gap, boshlovchi duch keladigan real muammo yoki savol
3. Asosiy qism — 2-4 qisqa abzas yoki 3-5 punktli ro'yxat.
   Har abzas 1-3 gap. Uzun devor matn yo'q
4. Aniq misol — Claude'ga aynan nima yozish kerakligi, <code> tegi ichida
5. Yakun — 1-2 gap, bugun sinab ko'rish mumkin bo'lgan aniq harakat

## Uzunlik
500-950 belgi. Telegram rasm captioni 1024 belgi — undan oshmasin.

## Formatlash
Faqat <b>, <i>, <code> teglari. Markdown (**, ##, *) ISHLATILMAYDI.

## Qat'iy taqiqlar
- Hashtag YO'Q
- Emoji eng ko'pi 1 ta, sarlavhada umuman yo'q
- Har abzas boshiga emoji qo'yish — man etiladi
- Reklama, obuna so'rash, "do'stlaringizga ulashing" — yo'q
- To'qib chiqarilgan statistika yoki raqamlar — yo'q
- Claude'ning haqiqiy bo'lmagan imkoniyatlarini va'da qilish — yo'q
- Siyosat, din, sog'liq bo'yicha maslahat — yo'q
"""

EXAMPLES = """
730 ta haqiqiy o'zbek Telegram postini tahlil qilib chiqarilgan ohang namunalari.
Bular MAVZU uchun emas — faqat OHANG, ritm va gap qurilishi uchun.

--- Namuna 1 (savol bilan boshlash) ---
Bilasizlarmi nega judayam ko'p smartfonlarda Google Play Marketi o'rnatilgan bo'ladi?

Buning asosiy sababi, Google smartfon ishlab chiqaruvchilariga ushbu ilova
foydasidan 4% pul to'lab turar ekan. Aynan shuning uchun Play Market haligacha
monopol bo'lishni ta'minlab turibdi deydi mutaxassislar.

O'rganamiz: savol -> sabab -> aniq dalil.

--- Namuna 2 (muammo -> bitta aniq harakat) ---
Zamonaviy muammolar zamonaviy yechimlarni talab qiladi. Agar sizga video
qo'ng'iroq orqali o'zini kimdir deb tanishtirayotgan bo'lsa, buni bilishning
eng oson usuli bor.

Siz suhbatdoshingizdan boshini o'nga-chapga burishini so'rang. Shunda soxta
maska yuz bilan choplanishni boshlaydi.

O'rganamiz: bitta aniq harakat beriladi, o'quvchi darrov qilib ko'ra oladi.

--- Namuna 3 (ro'yxat) ---
Nega 95% odamlar boy bo'lishmaydi:

- Kitob o'qishdan ko'ra, TikTok ko'rish osonroq
- Tongi 6:00 da uyg'onishdan, 11:00 gacha uxlash maroqliroq
- Nimadir yaratishdan, ko'chirib qo'yish qulayroq

Aynan shu kichik narsalar muvaffaqiyatga erishganlar va erishmaganlar
o'rtasidagi farqni keltirib chiqaradi.

O'rganamiz: sarlavha ikki nuqta bilan tugaydi, punktlar parallel qurilgan,
oxirida bitta umumlashtiruvchi gap.

--- Namuna 4 (o'quvchi bilan bir tomonda turish) ---
Atrofimizda zamonaviy bilimlarni egallayotgan yoshlar yetarlicha, lekin hayotda
o'z o'rningizni topishingiz uchun bu bilimlar kamlik qiladi.

Jamiyatda o'z o'rnimizni topishimiz uchun hammamiz hozirdan o'zgarishni
boshlashimiz zarur.

O'rganamiz: "biz", "hammamiz". Yuqoridan o'rgatish ohangi yo'q.

--- Namuna 5 (shaxsiy xulosa) ---
Menga bu yechimda ikkita narsa yoqdi:

- Muammoni oddiy usul bilan hal qilgani
- Hech qanday qo'shimcha dastur talab qilmagani

O'rganamiz: "menga yoqdi" — muallif fikrini bildiradi, lekin haqiqat sifatida
tiqishtirmaydi.

--- Etalon post (aynan shunday chiqishi kerak) ---
Claude javoblari umumiy chiqyaptimi? Sabab bitta

Ko'pchilik Claude'ga "menga marketing rejasi tuzib ber" deb yozadi va javob
quruq chiqqanidan hafsalasi pir bo'ladi.

Muammo Claude'da emas. U sizning kim ekanligingizni bilmaydi.

Bitta gap qo'shsangiz, javob butunlay o'zgaradi — o'zingiz haqingizda kontekst
bering. Ya'ni kim uchun, qanday byudjet bilan, qaysi bozorda.

Buni sinab ko'ring:

<code>Men Toshkentda kichik gulchilik do'koni ochganman. Oyiga 3 mln so'm
reklama byudjetim bor. Mijozlarim asosan 25-40 yoshli ayollar. Shu sharoitga
mos 1 oylik marketing rejasi tuz.</code>

Farqni o'zingiz ko'rasiz. Claude sehrgar emas — u siz tushuntirgan darajada
yordam beradi.
"""

IMAGE_STYLE = """
Two-layer composition:
1. BACKGROUND: realistic, softly blurred environment (desk, laptop, office,
   table by a window, cafe). Natural light, warm tones.
2. FOREGROUND: one glossy 3D icon representing the post's core idea.
   Rounded, smooth, plastic-glossy material, soft shadow. Like a modern app
   icon but volumetric.

COLORS: warm terracotta / coral accent (#D97757) as the highlight.
Supporting: cream, white, light grey, light sand.
Avoid deep blue and neon. No "cyber", "matrix" or "hacker" aesthetic.

FORMAT: 16:9 horizontal. Leave open space in the centre — shift the icon
slightly left or right.

STRICT PROHIBITIONS:
- NO text, letters, numbers or logos anywhere in the image
- No human faces (hands, shoulders are fine)
- No robots, androids, brains, circuit boards or other AI cliches
- No stock-photo artificiality — it must look natural
"""


# ══════════════════════════════════════════════════════════════════
#  3-QISM — YORDAMCHI FUNKSIYALAR
# ══════════════════════════════════════════════════════════════════

def now():
    return datetime.now(TZ)


def log(msg):
    print(f"[{now():%H:%M:%S}] {msg}", flush=True)


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or ""))


def clean(s):
    s = (s or "").strip()
    s = re.sub(r"^```(?:html|json)?\s*|\s*```$", "", s).strip()
    s = s.replace("**", "").replace("’", "'").replace("‘", "'")
    return re.sub(r"\n{3,}", "\n\n", s)


def parse_json(raw):
    txt = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", txt, re.S)
    if fence:
        txt = fence.group(1).strip()
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        for o, c in (("{", "}"), ("[", "]")):
            i, j = txt.find(o), txt.rfind(c)
            if i != -1 and j > i:
                try:
                    return json.loads(txt[i:j + 1])
                except json.JSONDecodeError:
                    continue
        raise RuntimeError(f"JSON o'qib bo'lmadi:\n{(raw or '')[:500]}")


# ── Tarix ────────────────────────────────────────────────────────
def hist_load():
    if not HISTORY_FILE.exists():
        return []
    try:
        d = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def hist_topics(limit=60):
    return [e.get("topic", "") for e in hist_load()[-limit:] if e.get("topic")]


def hist_add(topic, title, extra=None):
    d = hist_load()
    e = {"date": now().strftime("%Y-%m-%d %H:%M"), "topic": topic, "title": title}
    if extra:
        e.update(extra)
    d.append(e)
    HISTORY_FILE.write_text(json.dumps(d[-400:], ensure_ascii=False, indent=2),
                            encoding="utf-8")


# ══════════════════════════════════════════════════════════════════
#  4-QISM — GEMINI
# ══════════════════════════════════════════════════════════════════

GEM_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def gem_post(model, body):
    last = None
    for a in range(4):
        try:
            r = requests.post(f"{GEM_BASE}/{model}:generateContent",
                              params={"key": GEMINI_API_KEY}, json=body, timeout=240)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                time.sleep(5 * (a + 1))
                continue
            # Ba'zi modellar thinkingConfig ni qo'llamaydi — olib tashlab qayta urinamiz
            if (r.status_code == 400 and "thinking" in r.text.lower()
                    and body.get("generationConfig", {}).pop("thinkingConfig", None)):
                print("[gemini] thinkingConfig qo'llanmadi — usiz qayta urinilmoqda")
                continue
            raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:500]}")
        except requests.RequestException as e:
            last = str(e)
            time.sleep(5 * (a + 1))
    raise RuntimeError(f"Gemini so'rovi muvaffaqiyatsiz: {last}")


def gem_text(prompt, search=False, temperature=0.9, json_mode=False,
             max_tokens=16384):
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            # "O'ylash" rejimini o'chiramiz — u chiqish tokenlarini yeb qo'yadi
            # va javob o'rtasida uzilib qolishiga sabab bo'ladi.
            "thinkingConfig": {"thinkingBudget": 0},
        },
        "safetySettings": [{"category": c, "threshold": "BLOCK_ONLY_HIGH"} for c in (
            "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
            "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")],
    }
    if search:
        body["tools"] = [{"google_search": {}}]
    elif json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"

    resp = gem_post(GEMINI_TEXT_MODEL, body)
    cand = (resp.get("candidates") or [{}])[0]
    finish = cand.get("finishReason", "")
    try:
        parts = cand["content"]["parts"]
    except (KeyError, TypeError):
        raise RuntimeError(f"Gemini javobi bo'sh (finishReason={finish}): "
                           f"{json.dumps(resp)[:300]}")
    out = "".join(p.get("text", "") for p in parts).strip()
    if not out:
        raise RuntimeError(f"Gemini bo'sh matn qaytardi (finishReason={finish})")
    if finish == "MAX_TOKENS":
        raise RuntimeError("Gemini javobi token limitiga yetib uzilib qoldi")
    return out


def gem_json(prompt, search=False, temperature=0.9, attempts=3):
    """JSON qaytaradigan so'rov. Javob buzilsa qayta uriniladi."""
    last = None
    for i in range(attempts):
        try:
            return parse_json(gem_text(prompt, search=search,
                                       temperature=temperature,
                                       json_mode=not search))
        except RuntimeError as e:
            last = e
            print(f"[gemini] {i+1}-urinish muvaffaqiyatsiz: {str(e)[:200]}")
            # keyingi urinishda qisqaroq/aniqroq javob so'raymiz
            temperature = max(0.3, temperature - 0.25)
            time.sleep(3)
    raise RuntimeError(f"Gemini JSON qaytara olmadi ({attempts} urinish): {last}")


IMAGE_MODEL_FALLBACKS = [
    "gemini-2.5-flash-image",
    "gemini-2.5-flash-image-preview",
    "gemini-2.0-flash-preview-image-generation",
]


def gem_image(prompt, out_path):
    """Rasm generatsiya qiladi. Bir necha model va sozlamani sinab ko'radi."""
    out_path = Path(out_path)
    models = [GEMINI_IMAGE_MODEL] + [m for m in IMAGE_MODEL_FALLBACKS
                                     if m != GEMINI_IMAGE_MODEL]
    configs = [
        {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "16:9"}},
        {"responseModalities": ["TEXT", "IMAGE"]},
        {},
    ]
    errors = []
    for model in models:
        for cfg in configs:
            body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
            if cfg:
                body["generationConfig"] = cfg
            try:
                resp = gem_post(model, body)
            except RuntimeError as e:
                msg = str(e)[:180]
                errors.append(f"{model}: {msg}")
                # model umuman yo'q bo'lsa — qolgan sozlamalarni sinamaymiz
                if "404" in msg or "not found" in msg.lower():
                    break
                continue
            cand = (resp.get("candidates") or [{}])[0]
            for part in cand.get("content", {}).get("parts", []):
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    out_path.write_bytes(base64.b64decode(inline["data"]))
                    print(f"[3-agent] Model: {model}")
                    return out_path
            errors.append(f"{model}: javobda rasm yo'q "
                          f"(finishReason={cand.get('finishReason')})")
    raise RuntimeError("Rasm generatsiya qilinmadi. Sabablar:\n  - "
                       + "\n  - ".join(dict.fromkeys(errors))[:900])


# ══════════════════════════════════════════════════════════════════
#  5-QISM — ELEVENLABS
# ══════════════════════════════════════════════════════════════════

def tts(text):
    if not ELEVENLABS_API_KEY:
        print("[4-agent] ELEVENLABS_API_KEY yo'q — audio o'tkazib yuborildi")
        return None
    mp3 = BUILD / "voice.mp3"
    models = [ELEVENLABS_MODEL] + [m for m in
              ("eleven_v3", "eleven_multilingual_v2", "eleven_turbo_v2_5")
              if m != ELEVENLABS_MODEL]
    last = None
    for model_id in models:
        for a in range(3):
            try:
                r = requests.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
                    headers={"xi-api-key": ELEVENLABS_API_KEY,
                             "Content-Type": "application/json",
                             "Accept": "audio/mpeg"},
                    params={"output_format": "mp3_44100_128"},
                    json={"text": text, "model_id": model_id,
                          "voice_settings": {"stability": 0.5, "similarity_boost": 0.75,
                                             "style": 0.15, "use_speaker_boost": True}},
                    timeout=180)
                if r.status_code == 200 and r.content:
                    mp3.write_bytes(r.content)
                    print(f"[4-agent] Audio OK — {model_id}, {len(r.content)//1024} KB")
                    return to_ogg(mp3)
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                if r.status_code in (429, 500, 502, 503):
                    time.sleep(5 * (a + 1))
                    continue
                break
            except requests.RequestException as e:
                last = str(e)
                time.sleep(5 * (a + 1))
    print(f"[4-agent] Audio yaratilmadi: {last}")
    return None


def to_ogg(mp3):
    if not shutil.which("ffmpeg"):
        return mp3
    ogg = mp3.with_suffix(".ogg")
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
                        "-c:a", "libopus", "-b:a", "48k", "-ar", "48000", "-ac", "1",
                        str(ogg)], check=True, timeout=180)
        return ogg
    except Exception:
        return mp3


# ══════════════════════════════════════════════════════════════════
#  6-QISM — TELEGRAM
# ══════════════════════════════════════════════════════════════════

TG = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def tg_call(method, data=None, files=None, timeout=90):
    last = None
    for a in range(4):
        try:
            r = requests.post(f"{TG}/{method}", data=data, files=files, timeout=timeout)
            j = r.json()
            if j.get("ok"):
                return j["result"]
            desc = j.get("description", "")
            if "retry after" in desc.lower():
                time.sleep(int(j.get("parameters", {}).get("retry_after", 5)) + 1)
                if files:
                    [f.seek(0) for f in files.values()]
                continue
            raise RuntimeError(f"Telegram {method}: {desc}")
        except requests.RequestException as e:
            last = str(e)
            time.sleep(3 * (a + 1))
            if files:
                [f.seek(0) for f in files.values()]
    raise RuntimeError(f"Telegram {method}: tarmoq xatosi — {last}")


def tg_msg(chat, text, markup=None, reply_to=None):
    d = {"chat_id": chat, "text": text, "parse_mode": "HTML",
         "disable_web_page_preview": True}
    if markup:
        d["reply_markup"] = json.dumps(markup)
    if reply_to:
        d["reply_to_message_id"] = reply_to
    return tg_call("sendMessage", data=d)


def tg_photo(chat, path, caption=None, markup=None):
    d = {"chat_id": chat, "parse_mode": "HTML"}
    if caption:
        d["caption"] = caption
    if markup:
        d["reply_markup"] = json.dumps(markup)
    with open(path, "rb") as f:
        return tg_call("sendPhoto", data=d, files={"photo": f}, timeout=180)


def tg_voice(chat, path, reply_to=None):
    p = Path(path)
    d = {"chat_id": chat}
    if reply_to:
        d["reply_to_message_id"] = reply_to
    method = "sendVoice" if p.suffix.lower() == ".ogg" else "sendAudio"
    key = "voice" if method == "sendVoice" else "audio"
    with open(p, "rb") as f:
        return tg_call(method, data=d, files={key: f}, timeout=180)


def tg_send_post(chat, post):
    """Rasm + matn + audio yuboradi. Matn caption limitidan uzun bo'lsa —
    rasm alohida, matn alohida xabar sifatida ketadi."""
    text = post["text"]
    img = post.get("image_path")
    has_img = bool(img) and Path(img).exists()

    if has_img and len(text) <= CAPTION_LIMIT:
        root = tg_photo(chat, img, caption=text)["message_id"]
    elif has_img:
        tg_photo(chat, img)
        root = tg_msg(chat, text)["message_id"]
        print(f"[tg] matn {len(text)} belgi — alohida xabar sifatida yuborildi")
    else:
        root = tg_msg(chat, text)["message_id"]

    v = post.get("voice_path")
    if v and Path(v).exists():
        try:
            tg_voice(chat, v, reply_to=root)
        except Exception as e:
            log(f"[tg] audio yuborilmadi: {e}")
    return root


def tg_clear_markup(chat, mid):
    try:
        tg_call("editMessageReplyMarkup",
                data={"chat_id": chat, "message_id": mid,
                      "reply_markup": json.dumps({"inline_keyboard": []})})
    except RuntimeError:
        pass


def tg_answer(cb_id, text=None):
    try:
        d = {"callback_query_id": cb_id}
        if text:
            d["text"] = text
        tg_call("answerCallbackQuery", data=d)
    except RuntimeError:
        pass


def tg_drain():
    off = 0
    try:
        for u in tg_call("getUpdates", data={"timeout": 0, "limit": 100}):
            off = max(off, u["update_id"] + 1)
    except RuntimeError as e:
        print(f"[tg] {e}")
    return off


def tg_wait_button(offset, deadline_ts):
    """deadline gacha tugma bosilishini kutadi."""
    while True:
        left = deadline_ts - time.time()
        if left <= 0:
            return None, offset
        lp = max(1, min(25, int(left)))
        try:
            updates = tg_call("getUpdates",
                              data={"offset": offset, "timeout": lp,
                                    "allowed_updates": '["callback_query"]'},
                              timeout=lp + 20)
        except RuntimeError as e:
            print(f"[tg] {e}")
            time.sleep(3)
            continue
        for u in updates:
            offset = max(offset, u["update_id"] + 1)
            cq = u.get("callback_query")
            if not cq:
                continue
            if str(cq.get("from", {}).get("id")) != str(TELEGRAM_ADMIN_ID):
                tg_answer(cq["id"], "Bu tugma siz uchun emas.")
                continue
            if cq.get("data") == REDO_DATA:
                return cq, offset


# ══════════════════════════════════════════════════════════════════
#  7-QISM — AGENTLAR
# ══════════════════════════════════════════════════════════════════

def agent1_topic(avoid):
    used = "\n".join(f"- {t}" for t in (hist_topics() + avoid)) or "(hozircha bo'sh)"
    prompt = f"""Sen "{RUBRIC}" rubrikasi uchun mavzu qidiruvchi tadqiqotchisan.

Vazifa: Google qidiruvidan foydalanib, Claude (Anthropic'ning AI yordamchisi)
bilan ishlash bo'yicha AMALIY, YANGI va MUTLAQ BOSHLOVCHILAR uchun foydali
5 ta mavzu top.

Mavzu quyidagilardan biri bo'lishi mumkin:
- Claude'dan yaxshiroq javob olish usuli
- Claude'ning kam ma'lum, lekin oddiy foydalanuvchiga foydali imkoniyati
- Boshlovchilar qiladigan tipik xato va uni tuzatish
- Kundalik hayotda Claude'ni qo'llashning aniq stsenariysi (ish, o'qish, uy)
- Claude'dagi yangi funksiya (agar so'nggi oylarda chiqqan bo'lsa)

QAT'IY TALABLAR:
- Mavzu bugungi kunda haqiqatan mavjud imkoniyatga asoslansin. To'qima yo'q.
- Dasturchilar uchun texnik mavzular EMAS (API, kod, terminal) — oddiy odam uchun.
- Quyidagilar ALLAQACHON chiqqan, ularni va ularga yaqin mavzularni BERMA:
{used}

Javobni FAQAT shu JSON ko'rinishida ber:
{{"topics": [{{"topic": "mavzu nomi o'zbekcha 4-9 so'z",
 "why": "nega boshlovchiga foydali, 1 gap",
 "key_facts": ["tekshirilgan aniq fakt 1", "fakt 2", "fakt 3"]}}]}}"""

    data = gem_json(prompt, search=True, temperature=1.0)
    topics = data.get("topics") if isinstance(data, dict) else data
    if not topics:
        raise RuntimeError("1-agent: mavzu topilmadi")
    seen = {t.lower().strip() for t in hist_topics() + avoid}
    for t in topics:
        if t.get("topic", "").lower().strip() not in seen:
            print(f"[1-agent] Mavzu: {t['topic']}")
            return t
    print(f"[1-agent] Barchasi takror — birinchisi: {topics[0]['topic']}")
    return topics[0]


def agent2_write(topic, feedback=None):
    facts = "\n".join(f"- {f}" for f in topic.get("key_facts", []))
    fb = (f"\n\nOLDINGI URINISH RAD ETILDI. Sabab:\n{feedback}\n"
          f"Bu xatolarni takrorlama." if feedback else "")

    prompt = f"""Sen "AQLUSTAXONA" Telegram kanalining kopirayterisan.

## KANAL USLUBI (qat'iy amal qil)
{STYLE_GUIDE}

## OHANG NAMUNALARI (mavzuni emas, OHANGNI nusxala)
{EXAMPLES}

## BUGUNGI MAVZU
{topic.get('topic')}

Nega foydali: {topic.get('why', '')}

Tayanch faktlar:
{facts}
{fb}

## VAZIFA
Shu mavzuda bitta Telegram post yoz.

Talablar:
- UZUNLIK: {MIN_CHARS}-{MAX_CHARS} belgi. Bu ENG MUHIM talab.
  "title" va "body" birgalikda {MAX_CHARS} belgidan OSHMASLIGI shart.
  Yozib bo'lgach belgilarni sanab chiq. Uzun bo'lsa — qisqartir.
  Uzun post yaxshi post emas. Qisqa, aniq, keraksiz gapsiz yoz.
- O'zbek tili, lotin yozuvi, to'g'ri apostrof
- Faqat <b>, <i>, <code>. Markdown YO'Q
- Hashtag YO'Q. Emoji eng ko'pi 1 ta
- Ichida Claude'ga yoziladigan aniq namuna matn <code> ichida bo'lsin
- Yakunida bugun qilib ko'rish mumkin bo'lgan aniq harakat

Javobni FAQAT shu JSON ko'rinishida ber:
{{"title": "sarlavha, nuqtasiz, emojisiz",
 "body": "postning qolgan qismi, HTML teglar bilan, abzaslar \\n\\n bilan",
 "audio_script": "audio uchun matn: mazmuni, jonli og'zaki o'zbek tilida, 60-90 so'z, HTML tegsiz, kod namunasisiz",
 "image_idea": "rasm uchun vizual g'oya, ingliz tilida, 1 gap"}}"""

    d = gem_json(prompt, temperature=0.95)
    title, body = clean(d.get("title", "")), clean(d.get("body", ""))
    if not title or not body:
        raise RuntimeError("2-agent: bo'sh post qaytdi")
    post = {"title": title, "body": body,
            "audio_script": strip_tags(d.get("audio_script", "")).strip(),
            "image_idea": d.get("image_idea", topic.get("topic", "")),
            "text": f"<b>{title}</b>\n\n{body}"}
    print(f"[2-agent] Post yozildi — {len(post['text'])} belgi")

    # Caption limitidan oshsa — to'liq qayta yozmasdan faqat qisqartiramiz
    if len(post["text"]) > CAPTION_LIMIT:
        short = _shorten(post["text"])
        if short and len(short) < len(post["text"]):
            post["text"] = short
            m = re.search(r"<b>(.*?)</b>", short, re.S)
            if m:
                post["title"] = strip_tags(m.group(1)).strip()
            print(f"[2-agent] Qisqartirildi — {len(short)} belgi")
    return post


def _shorten(text):
    """Postni mazmunini saqlab qisqartiradi."""
    prompt = f"""Quyidagi Telegram postini {MAX_CHARS} belgidan qisqa qil.

QOIDALAR:
- Mazmun, sarlavha va <code> ichidagi namuna prompt saqlanib qolsin
- Keraksiz sifat va takroriy gaplarni olib tashla
- HTML teglar (<b>, <i>, <code>) o'zgarmasin va yopilgan bo'lsin
- Yangi ma'lumot qo'shma
- Faqat qisqartirilgan postning to'liq matnini qaytar, boshqa hech narsa yozma

POST:
{text}"""
    try:
        out = clean(gem_text(prompt, temperature=0.4))
        return out if not hard_checks(out) else None
    except RuntimeError as e:
        print(f"[2-agent] Qisqartirish xatosi: {e}")
        return None


def agent3_image(post):
    prompt = f"""{IMAGE_STYLE}

---
Generate one image for a Telegram post.
Post subject: {post.get('image_idea')}
Follow the style rules above exactly. Absolutely no text, letters, numbers
or logos anywhere in the image."""
    try:
        p = gem_image(prompt, BUILD / "post.png")
        print(f"[3-agent] Rasm tayyor — {p.stat().st_size // 1024} KB")
        return p
    except Exception as e:
        print(f"[3-agent] XATO: {e}")
        return None


def agent4_voice(post):
    script = re.sub(r"\s+", " ",
                    post.get("audio_script") or strip_tags(post["text"])).strip()
    if len(script) < 40:
        return None
    return tts(script)


def hard_checks(text, image_path=None):
    issues = []
    if not (text or "").strip():
        return ["Post matni bo'sh"]
    if len(text) > MESSAGE_LIMIT:
        issues.append(f"Matn juda uzun: {len(text)} belgi (limit {MESSAGE_LIMIT})")
    if len(text) < 200:
        issues.append(f"Matn juda qisqa: {len(text)} belgi")
    if "#" in text:
        issues.append("Hashtag ishlatilgan")
    if re.search(r"\*\*|^##\s|^\* ", text, re.M):
        issues.append("Markdown belgilari qolgan")
    for tag in re.findall(r"</?([a-zA-Z]+)[^>]*>", text):
        if tag.lower() not in ALLOWED_TAGS:
            issues.append(f"Ruxsat etilmagan HTML teg: <{tag}>")
            break
    stack = []
    ok = True
    for m in re.finditer(r"<(/?)([a-zA-Z]+)[^>]*?(/?)>", text):
        if m.group(3):
            continue
        if m.group(1):
            if not stack or stack.pop() != m.group(2).lower():
                ok = False
                break
        else:
            stack.append(m.group(2).lower())
    if not ok or stack:
        issues.append("HTML teglar yopilmagan")
    if image_path is not None and not Path(image_path).exists():
        issues.append("Rasm fayli topilmadi")
    return issues


def agent5_review(post, topic, image_path):
    hard = hard_checks(post["text"], image_path)
    if hard:
        print(f"[5-agent] Texnik xato: {hard}")
        return {"passed": False, "issues": hard}

    prompt = f"""Sen "AQLUSTAXONA" kanalining qat'iy muharririsan.

## KANAL USLUBI
{STYLE_GUIDE}

## TEKSHIRILAYOTGAN POST
Mavzu: {topic.get('topic')}
---
{post['text']}
---

## VAZIFA
Tekshir:
1. Uslub qo'llanmasiga mos keladimi (struktura, ohang, emoji/hashtag qoidalari)
2. Til to'g'rimi — o'zbek lotin, apostroflar, grammatika, rus so'zlari yo'qmi
3. Mutlaq boshlovchi tushunadimi — izohsiz atama qolmaganmi
4. Faktlar to'g'rimi — Claude'ning haqiqiy imkoniyatlari haqidami
5. Aniq foyda bormi — o'quvchi bugun nimadir qila oladimi
6. Faqat <b>, <i>, <code> ishlatilganmi, Markdown qolmaganmi

Talabchan bo'l, lekin adolatli. Kichik uslubiy nuqson uchun rad etma.

Javobni FAQAT shu JSON ko'rinishida ber:
{{"passed": true yoki false, "score": 1-10 son,
 "issues": ["muammo 1"],
 "fixed_text": "faqat kichik tahrir kerak bo'lsa — to'liq tuzatilgan matn (sarlavha <b> ichida). Aks holda bo'sh satr."}}"""

    try:
        v = gem_json(prompt, temperature=0.3)
    except Exception as e:
        print(f"[5-agent] Tekshiruv xatosi ({e}) — post o'tkazildi")
        return {"passed": True, "issues": []}

    passed = bool(v.get("passed"))
    fixed = clean(v.get("fixed_text") or "")
    if fixed and not hard_checks(fixed, image_path):
        if fixed != post["text"]:
            print("[5-agent] Matn tahrirlandi")
            post["text"] = fixed
            m = re.search(r"<b>(.*?)</b>", fixed, re.S)
            if m:
                post["title"] = strip_tags(m.group(1)).strip()
        passed = True

    print(f"[5-agent] {'O`TDI' if passed else 'RAD ETILDI'} "
          f"(ball: {v.get('score', '-')}) {v.get('issues') or ''}")
    return {"passed": passed, "issues": v.get("issues") or []}


def agent6_publish(post):
    if DRY_RUN:
        log("[6-agent] DRY_RUN=1 — kanalga chiqarilmadi")
        tg_msg(TELEGRAM_ADMIN_ID, "ℹ️ <i>DRY_RUN yoqilgan — post kanalga chiqmadi.</i>")
        return
    mid = tg_send_post(TELEGRAM_CHANNEL, post)
    hist_add(post["topic"].get("topic", ""), post.get("title", ""),
             {"message_id": mid})
    log(f"[6-agent] Kanalga chiqdi — message_id {mid}")


# ══════════════════════════════════════════════════════════════════
#  8-QISM — OQIM
# ══════════════════════════════════════════════════════════════════

def build_post(avoid, label=""):
    log(f"=== Post yaratilmoqda{' (' + label + ')' if label else ''} ===")
    topic = agent1_topic(avoid)
    post, feedback = None, None
    for i in range(1, 4):
        post = agent2_write(topic, feedback)
        img = agent3_image(post)
        v = agent5_review(post, topic, img)
        if v["passed"]:
            post["image_path"] = img
            break
        feedback = "; ".join(v.get("issues", []))
        log(f"[QA] {i}-urinish rad etildi, qayta yozilmoqda...")
    else:
        log("[QA] 3 marta o'tmadi — oxirgi variant ishlatiladi")
        post["image_path"] = agent3_image(post)
    post["voice_path"] = agent4_voice(post)
    post["topic"] = topic
    log(f"=== Tayyor: {post['title']} ===")
    return post


def send_preview(post, deadline, round_no):
    kb = {"inline_keyboard": [[{"text": "🔄 Qayta qilish", "callback_data": REDO_DATA}]]}
    tg_send_post(TELEGRAM_ADMIN_ID, post)
    header = (f"<b>PREVIEW</b> · {RUBRIC}\n"
              f"Chiqish vaqti: <b>{deadline:%H:%M}</b>"
              + (f" · {round_no}-variant" if round_no > 1 else "")
              + "\nHech narsa bosmasangiz — o'sha vaqtda kanalga chiqadi.")
    ctrl = tg_msg(TELEGRAM_ADMIN_ID, header, markup=kb)
    log(f"[preview] yuborildi (deadline {deadline:%H:%M})")
    return ctrl["message_id"]


def run(force_now=False, preview_only=False):
    hh, mm = (int(x) for x in PUBLISH_TIME.split(":"))
    t = now()
    publish_at = t.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if publish_at < t:
        publish_at = t + timedelta(minutes=PREVIEW_LEAD)
        log(f"[jadval] {PUBLISH_TIME} o'tib ketgan — yangi vaqt {publish_at:%H:%M}")
    preview_at = publish_at - timedelta(minutes=PREVIEW_LEAD)
    if force_now:
        preview_at = now()
        publish_at = now() + timedelta(minutes=PREVIEW_LEAD)
    log(f"[jadval] preview {preview_at:%H:%M} · publish {publish_at:%H:%M}")

    offset = tg_drain()
    avoid = []
    post = build_post(avoid)

    wait = (preview_at - now()).total_seconds()
    if wait > 0:
        log(f"[jadval] preview vaqtigacha {int(wait)} soniya kutilmoqda")
        time.sleep(wait)

    round_no, deadline = 1, publish_at
    while True:
        ctrl_id = send_preview(post, deadline, round_no)
        cq, offset = tg_wait_button(offset, deadline.timestamp())
        if cq is None:
            tg_clear_markup(TELEGRAM_ADMIN_ID, ctrl_id)
            break
        tg_answer(cq["id"], "Qabul qilindi. Yangi post tayyorlanmoqda...")
        tg_clear_markup(TELEGRAM_ADMIN_ID, ctrl_id)
        if round_no > MAX_REDO:
            tg_msg(TELEGRAM_ADMIN_ID,
                   f"⚠️ Qayta qilish limiti ({MAX_REDO}) tugadi. "
                   f"Oxirgi variant chiqariladi.")
            break
        avoid.append(post["topic"].get("topic", ""))
        tg_msg(TELEGRAM_ADMIN_ID, "⏳ Yangi post yaratilmoqda — 2-3 daqiqa...")
        round_no += 1
        try:
            post = build_post(avoid, f"{round_no}-variant")
        except Exception as e:
            log(f"[XATO] qayta yaratishda: {e}")
            tg_msg(TELEGRAM_ADMIN_ID,
                   f"❌ Qayta yaratishda xato: <code>{str(e)[:300]}</code>\n"
                   f"Oldingi variant chiqariladi.")
            break
        deadline = now() + timedelta(minutes=PREVIEW_LEAD)

    if preview_only:
        log("[6-agent] --preview-only — kanalga chiqarilmadi")
        return
    agent6_publish(post)


def doctor():
    """Diagnostika: bot kim, kim unga yozgan, xabar bora oladimi."""
    print("=" * 60)
    print("  TELEGRAM DIAGNOSTIKASI")
    print("=" * 60)

    # 1. Bot kim?
    try:
        me = tg_call("getMe")
        print(f"\n1) BOT TOPILDI")
        print(f"   Nomi:     {me.get('first_name')}")
        print(f"   Username: @{me.get('username')}")
        print(f"   >>> Telegramda AYNAN shu botni oching: "
              f"https://t.me/{me.get('username')}")
    except Exception as e:
        print(f"\n1) BOT TOPILMADI: {e}")
        print("   >>> TELEGRAM_BOT_TOKEN xato. @BotFather dan qayta oling.")
        return

    # 2. Kim botga yozgan?
    print(f"\n2) BOTGA KIM YOZGAN?")
    try:
        updates = tg_call("getUpdates", data={"timeout": 0, "limit": 100})
        chats = {}
        for u in updates:
            for key in ("message", "edited_message", "channel_post",
                        "my_chat_member", "callback_query"):
                obj = u.get(key)
                if not obj:
                    continue
                ch = obj.get("chat") or obj.get("from") or {}
                if ch.get("id"):
                    chats[ch["id"]] = (
                        ch.get("type", "user"),
                        ch.get("title") or ch.get("first_name")
                        or ch.get("username") or "?")
        if chats:
            for cid, (ctype, name) in chats.items():
                mark = " <-- ADMIN_ID shu" if str(cid) == str(TELEGRAM_ADMIN_ID) else ""
                print(f"   {cid}   ({ctype})  {name}{mark}")
            # Har biriga test xabar yuborib ko'ramiz
            print("\n   TEST YUBORISH:")
            for cid, (ctype, name) in chats.items():
                try:
                    tg_msg(cid, "✅ Diagnostika: bot shu chatga yoza oladi.")
                    print(f"   {cid}  ISHLAYDI  ({ctype}, {name})")
                    if ctype in ("group", "supergroup", "channel"):
                        print(f"      >>> TELEGRAM_CHANNEL uchun shu raqamni qo'ying: {cid}")
                    else:
                        print(f"      >>> TELEGRAM_ADMIN_ID uchun shu raqamni qo'ying: {cid}")
                except Exception as e:
                    print(f"   {cid}  ISHLAMAYDI — {str(e)[:120]}")
        else:
            print("   Hech kim yozmagan (yoki eski xabarlar tozalangan).")
            print("   >>> Botni oching va /start bosing, keyin qayta ishga tushiring.")
    except Exception as e:
        print(f"   Xato: {e}")

    # 3. Adminga xabar bora oladimi?
    print(f"\n3) ADMINGA TEST XABAR (ID: {TELEGRAM_ADMIN_ID})")
    try:
        tg_msg(TELEGRAM_ADMIN_ID,
               "✅ <b>Diagnostika muvaffaqiyatli</b>\n\n"
               "Bot siz bilan bog'lana oladi. Preview shu chatga keladi.")
        print("   OK — xabar yuborildi. Telegramni tekshiring.")
    except Exception as e:
        print(f"   XATO: {e}")
        if "chat not found" in str(e).lower():
            print("   >>> Sabab: botga hech qachon /start yozmagansiz,")
            print("       YOKI TELEGRAM_ADMIN_ID xato.")
            print("       2-bo'limdagi ro'yxatdan to'g'ri raqamni oling.")

    # 4. Kanal/guruh
    print(f"\n4) KANAL/GURUH TEKSHIRUVI ({TELEGRAM_CHANNEL})")
    try:
        chat = tg_call("getChat", data={"chat_id": TELEGRAM_CHANNEL})
        print(f"   Topildi: {chat.get('title')} (turi: {chat.get('type')}, "
              f"id: {chat.get('id')})")
        try:
            m = tg_call("getChatMember", data={"chat_id": TELEGRAM_CHANNEL,
                                               "user_id": me["id"]})
            st = m.get("status")
            print(f"   Botning maqomi: {st}")
            if st not in ("administrator", "creator"):
                print("   >>> Bot ADMIN emas! Post yubora olmaydi.")
            elif not m.get("can_post_messages", True):
                print("   >>> Botda 'Post Messages' huquqi yo'q!")
            else:
                print("   OK — bot post yubora oladi.")
        except Exception as e:
            print(f"   Maqomni tekshirib bo'lmadi: {e}")
    except Exception as e:
        print(f"   XATO: {e}")
        print("   >>> TELEGRAM_CHANNEL xato yoki bot u yerga qo'shilmagan.")
        print("       Yopiq guruh bo'lsa -100... ko'rinishidagi ID kerak.")

    print("\n" + "=" * 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", action="store_true", help="jadvalni kutmasdan boshlash")
    ap.add_argument("--preview-only", action="store_true", help="kanalga chiqarmaslik")
    ap.add_argument("--doctor", action="store_true", help="faqat diagnostika")
    args = ap.parse_args()

    if args.doctor:
        doctor()
        return 0
    try:
        run(force_now=args.now, preview_only=args.preview_only)
        return 0
    except Exception as e:
        log(f"[FATAL] {e}\n{traceback.format_exc()}")
        try:
            tg_msg(TELEGRAM_ADMIN_ID,
                   f"❌ <b>Bugungi post chiqmadi</b>\n\n<code>{str(e)[:600]}</code>")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
