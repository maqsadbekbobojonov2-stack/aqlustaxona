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
import threading
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
SOZLAMA_FILE = ROOT / "sozlamalar.json"


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
def _kalitlar(nom):
    """NOM, NOM_2, NOM_3 ... ko'rinishidagi kalitlarni yig'adi.
    Nechta bo'lsa — shuncha. Yo'g'i o'tkazib yuboriladi."""
    out = []
    v = os.getenv(nom, "").strip()
    if v:
        out.append(v)
    for i in range(2, 10):
        v = os.getenv(f"{nom}_{i}", "").strip()
        if v and v not in out:
            out.append(v)
    return out


# Gemini kalitlari: asosiysi + zaxiralar. Limit tugasa avtomatik almashadi.
GEMINI_KEYS = _kalitlar("GEMINI_API_KEY")
if not GEMINI_KEYS:
    raise SystemExit(
        "\n[XATO] Sozlama topilmadi: GEMINI_API_KEY\n"
        "       GitHub -> Settings -> Secrets and variables -> Actions\n"
        "       bo'limiga 'GEMINI_API_KEY' nomli secret qo'shing.\n")
GEMINI_API_KEY = GEMINI_KEYS[0]
_KEY_IDX = [0]


def gem_key():
    return GEMINI_KEYS[_KEY_IDX[0]]


def gem_rotate_key():
    """Keyingi kalitga o'tadi — aylanma tartibda.

    Ilgari oxirgi kalitga yetgach to'xtardi va bitta vaqtinchalik
    kvota xatosi butun jarayonni o'lik kalitda qoldirardi. Endi
    kalitlar aylanadi, urinishlar soni gem_post ichida cheklangan.
    """
    if len(GEMINI_KEYS) < 2:
        return False
    _KEY_IDX[0] = (_KEY_IDX[0] + 1) % len(GEMINI_KEYS)
    print(f"[gemini] kalit almashtirildi -> {_KEY_IDX[0] + 1}/{len(GEMINI_KEYS)}")
    return True

GEMINI_TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "").strip() or "gemini-3.6-flash"
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "").strip() or "gemini-3.1-flash-image"

# Google modellarni tez-tez eskirtiradi (masalan gemini-2.5-flash 2026
# avgustda o'chirildi va 404 qaytara boshladi). Shuning uchun matn so'rovi
# 404 (model topilmadi) qaytarsa, ro'yxatdagi keyingi modelga o'tamiz —
# bot hech qachon "eskirgan model" sababli to'xtab qolmaydi.
# 2026-avgust holatiga ko'ra tirik modellar. gemini-2.5-flash va
# gemini-2.0-flash o'chirilgan (404 qaytaradi) — ro'yxatdan olib tashlandi.
TEXT_MODEL_FALLBACKS = [
    "gemini-3.6-flash", "gemini-3.7-flash", "gemini-flash-latest",
    "gemini-3.5-flash", "gemini-3.5-flash-lite",
]

# Grok (xAI) — Gemini'ning HAMMA kaliti va modeli ishlamay qolgan taqdirdagi
# ENG OXIRGI zaxira. Doim emas, faqat Gemini butunlay tugaganda ishlaydi.
GROK_KEYS = _kalitlar("GROK_API_KEY")
GROK_MODEL = os.getenv("GROK_MODEL", "").strip() or "grok-4.6"
GROK_MODEL_FALLBACKS = ["grok-4.6", "grok-4-fast", "grok-4", "grok-3"]
_GROK_IDX = [0]

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "").strip() or "JBFqnCBsd6RMkjVDRZzb"
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "").strip() or "eleven_v3"

PUBLISH_TIME = os.getenv("PUBLISH_TIME", "").strip() or "19:45"
PREVIEW_LEAD = _int("PREVIEW_LEAD_MINUTES", 10)
TZ = ZoneInfo(os.getenv("TIMEZONE", "").strip() or "Asia/Tashkent")
RUBRIC = os.getenv("RUBRIC", "").strip() or "Startap yangiliklari"

# ── Kontent jadvali ──────────────────────────────────────────────
# SLOT:
#   "ertalab"   -> 365 hikoyadan navbatdagisi (har kuni)
#   "kechqurun" -> kurs va yangilik almashib
# POST_SOURCE bilan majburan bitta turni tanlash mumkin:
#   hikoya | kurs | yangilik | ai
SLOT = (os.getenv("SLOT", "").strip().lower() or "kechqurun")
POST_SOURCE = os.getenv("POST_SOURCE", "").strip().lower()

STORIES_DIR = ROOT / "stories"
STORIES_JSON = ROOT / "stories.json"
STORIES_STATE = ROOT / "stories_state.json"
STORIES_TOTAL = 365
KURS_JSON = ROOT / "kurs.json"

# Preview faqat yangilik uchun keladi — hikoya va kurs matni oldindan
# tayyor va haftalik ko'rikdan o'tgan.
PREVIEW_TURLARI = {"yangilik", "ai"}
MAX_REDO = _int("MAX_REDO", 5)
DRY_RUN = os.getenv("DRY_RUN", "0").strip() == "1"

CAPTION_LIMIT = 1024      # Telegram rasm captioni limiti
MESSAGE_LIMIT = 4000      # Telegram oddiy xabar limiti
MIN_CHARS, MAX_CHARS = 450, 850
ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a"}
REDO_DATA = "redo"

# ── GitHub bilan bog'lanish ──────────────────────────────────────
# GitHub Actions ichida ishlaganda GITHUB_TOKEN o'zi beriladi
# (workflow'da: GITHUB_TOKEN: ${{ github.token }}).
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPO = (os.getenv("GITHUB_REPOSITORY", "").strip()
               or os.getenv("GITHUB_REPO", "").strip())
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "").strip() or "main"

# Bot Telegram orqali o'zgartira oladigan sozlamalar.
# Bular sozlamalar.json faylida GitHub'da saqlanadi — ya'ni admin botga
# aytadi, bot GitHub'ga yozadi, keyingi postlar shu sozlama bilan chiqadi.
SOZLAMA_KALITLAR = {
    "ertalab_vaqt":     "Ertalabki post vaqti, masalan 08:45",
    "kechqurun_vaqt":   "Kechqurungi post vaqti, masalan 19:45",
    "kurs_hashtag":     "Kurs postidagi hashtag, masalan #startap_kursi",
    "yangilik_hashtag": "Yangilik postidagi hashtag, masalan #yangilik",
    "kanal_nomi":       "Rasm pastida turadigan yozuv",
    "rasm_qoshimcha":   "Rasm chizilishiga qo'shimcha ko'rsatma",
    "matn_qoshimcha":   "Post matni yozilishiga qo'shimcha ko'rsatma",
    "hikoya_rasm":      "Hikoyalarga rasm chizilsinmi (ha / yo'q)",
    "hikoya_belgi":     "Hikoya tepasidagi yozuv, {N} - kun raqami",
    "audio":            "Postlarga ovozli izoh yozilsinmi (ha / yo'q)",
    "hikoya_tasdiq":    "Hikoya chiqishidan oldin tasdiq so'ralsinmi (ha / yo'q)",
    "ajratuvchi":       "Post ichidagi ajratuvchi chiziq ko'rinishi",
    "rasm_sarlavha":    "Rasm ustiga sarlavha yozilsinmi (ha / yo'q)",
}


_SOZ_KESH = {"mtime": None, "data": {}}


def sozlamalar():
    """Sozlamalar. Fayl o'zgarmaguncha keshdan beriladi — tezlik uchun."""
    if not SOZLAMA_FILE.exists():
        return {}
    try:
        m = SOZLAMA_FILE.stat().st_mtime
        if _SOZ_KESH["mtime"] != m:
            d = json.loads(SOZLAMA_FILE.read_text(encoding="utf-8"))
            _SOZ_KESH["data"] = d if isinstance(d, dict) else {}
            _SOZ_KESH["mtime"] = m
        return _SOZ_KESH["data"]
    except (json.JSONDecodeError, OSError):
        return {}


def _ha(kalit, standart="ha"):
    """Sozlama 'ha/yo'q' turida. Yo'q bo'lsa False qaytaradi."""
    v = (sozlama(kalit, standart) or "").strip().lower()
    return v not in ("yoq", "yo'q", "yo`q", "no", "0", "false", "kerakmas",
                     "kerak emas", "chizilmasin", "bolmasin", "bo'lmasin")


def sozlama(kalit, standart=""):
    return (sozlamalar().get(kalit) or "").strip() or standart


# ══════════════════════════════════════════════════════════════════
#  2-QISM — USLUB (shu yerni tahrirlang)
# ══════════════════════════════════════════════════════════════════

STYLE_GUIDE = """
## Auditoriya
O'zbekistonlik yoshlar: biznes boshlamoqchi, endigina boshlagan yoki startap
mavzusi qiziqtiradiganlar. Ular tajribali tadbirkor EMAS.
Hech qanday atamani izohsiz ishlatmang — birinchi marta qavs ichida oddiy tilda
tushuntiring. Masalan: "MVP (ya'ni eng oddiy ishlaydigan mahsulot)".

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
4. Aniq misol yoki dalil — raqam, holat, yoki qadamlar ketma-ketligi
5. Yakun — 1-2 gap, bugun sinab ko'rish mumkin bo'lgan aniq harakat

## AI vositalari haqida — MUHIM
Postning maqsadi startap qurishni o'rgatish, AI vositalarini
reklama qilish EMAS.
- Har postga "AI'ga shunday yozing" bo'limini QO'SHMA. Bu faqat
  mavzuning o'zi aynan AI vositasi haqida bo'lsagina o'rinli
- Aniq mahsulot nomini (Claude, ChatGPT va boshqalar) faqat
  yangilikning o'zi o'sha mahsulot haqida bo'lsa yoz
- Umumiy mavzularda yechim odamning o'z harakati bo'lsin:
  kim bilan gaplashish, nimani hisoblash, qayerga murojaat qilish

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
- Tasdiqlanmagan xabarni haqiqat sifatida berish — yo'q
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
Birinchi mijozni qayerdan topish kerak

Ko'pchilik reklama byudjeti yig'ishni kutadi va shu bilan oylar o'tadi.

Menimcha xato shundaki, birinchi mijozlar reklamadan kelmaydi. Ular tanish
doiradan keladi — do'stlar, ularning tanishlari, sohaviy guruhlar.

Bugun qilinadigan ish oddiy: ellikta ism yozing va o'ntasiga shaxsan yozing.
Xabar reklama bo'lmasin, oddiy savol bo'lsin.

Bittasi javob bersa — sizda birinchi suhbat bor. Reklama esa keyin, natija
ko'ringandan so'ng.
"""

IMAGE_STYLE = """
A PHOTOREALISTIC 3D RENDER of a real-life scene — the quality of a
high-end architectural / product visualisation. It must look like a real
photograph of a real place, only rendered: full HD detail, physically
correct light, real-world materials and proportions. NOT a cartoon, NOT a
clay or plastic toy world, NOT a flat illustration, NOT a stylised
figurine scene.

MOST IMPORTANT RULE: the picture must EXPLAIN THE IDEA OF THE POST BY
ITSELF. A person who only looks at the image, without reading a single
word, should understand what the post is about. Stage a believable
everyday moment that acts out the idea — hands at a desk, an open
notebook, a workshop bench, a small shop counter, a table by a window.
Show the actual situation the text talks about, not a riddle about it.

Composition:
1. THE SCENE: an ordinary, believable moment, rendered with real-world
   scale and real materials — paper, wood, fabric, brushed metal, glass,
   ceramic, skin. Honest textures with fine detail: paper grain, wood
   pores, fabric weave, slight wear.
2. LIGHT: physically based rendering — natural daylight from a window or
   warm indoor light, global illumination, soft realistic shadows, subtle
   contact shadows and reflections. Nothing plastic or artificially
   glossy.
3. CAMERA: 50mm lens look, sharp focus on the subject, background gently
   out of focus but still readable. Natural colours, true tones, HD, 8K
   texture detail.

Keep it simple and honest: one clear subject, one clear action, 1-3
objects. A real moment reads better than a crowded concept.

COLOURS: warm and natural — terracotta / coral accent (#D97757) as the
highlight, supported by cream, wood, warm grey, light sand. Avoid neon,
avoid heavy blue tint, avoid the "cyber / matrix / hacker" look.

FORMAT: 16:9 horizontal. IMPORTANT — the TOP THIRD of the frame must stay
calm and uncluttered (plain wall, window light, empty background), because
a title may be placed there. Put the main subject in the lower half.

STRICT PROHIBITIONS:
- NO written words, letters, numbers or logos anywhere in the image.
- No cartoon, no clay / plastic / toy materials, no glossy figurines, no
  flat vector illustration, no exaggerated cute proportions.
- No jokey, absurd or surreal scenes. No visual metaphors that only work
  as a pun. If a viewer would ask "what is this supposed to be?", the
  image is wrong. It must match the text plainly.
- No close-up human faces (hands, shoulders, a person seen from behind or
  from the side are fine and encouraged).
- No robots, androids, glowing brains, circuit boards or other AI cliches.
- Not a cheesy corporate stock scene: no handshake over a globe, no people
  in suits pointing at charts.
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


def hist_last():
    """Kanalga eng oxiri chiqqan postning tarix yozuvi."""
    d = hist_load()
    for e in reversed(d):
        if e.get("message_id"):
            return e
    return None


def hist_remove(entry):
    """Bitta tarix yozuvini o'chirib qolganini qaytaradi."""
    d = hist_load()
    for i in range(len(d) - 1, -1, -1):
        if d[i].get("date") == entry.get("date") and \
                d[i].get("message_id") == entry.get("message_id"):
            d.pop(i)
            break
    HISTORY_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    return d


# ── 365 kunlik hikoyalar ─────────────────────────────────────────
def stories_state():
    if not STORIES_STATE.exists():
        return {"last_sent": 0, "sent": []}
    try:
        d = json.loads(STORIES_STATE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {"last_sent": 0, "sent": []}
    except (json.JSONDecodeError, OSError):
        return {"last_sent": 0, "sent": []}


# ── Obunachilar va taklif tizimi ─────────────────────────────────
OBUNA_FILE = ROOT / "obunachilar.json"
SOVGA_FILE = ROOT / "cheklist.pdf"       # bepul PDF (repo ichida)
SOVGA_NOM = "AqlUstaxona — Startapni noldan boshlash.pdf"

_OBUNA = [None]          # xotiradagi nusxa
_OBUNA_KIR = [False]     # o'zgardimi (GitHub'ga yozish kerakmi)
_OBUNA_VAQT = [0.0]      # oxirgi yozilgan vaqt
_SOVGA_ID = [""]         # Telegram file_id — ikkinchi martadan tez ketadi
_BOT_NOM = [""]          # botning @username


def bot_nomi():
    """Botning @username'i — taklif havolasi uchun."""
    if not _BOT_NOM[0]:
        try:
            _BOT_NOM[0] = tg_call("getMe", data={}).get("username", "")
        except Exception as e:
            log(f"[bot] getMe: {e}")
    return _BOT_NOM[0]


def obunachilar():
    """{user_id: {kelgan, ism, sana, olgan}} — xotirada saqlanadi."""
    if _OBUNA[0] is None:
        try:
            d = json.loads(OBUNA_FILE.read_text(encoding="utf-8"))
            _OBUNA[0] = d if isinstance(d, dict) else {}
        except Exception:
            _OBUNA[0] = {}
    return _OBUNA[0]


def obuna_yoz(majburiy=False):
    """O'zgarishlarni GitHub'ga yozadi. Tez-tez emas — 2 daqiqada bir marta."""
    if not _OBUNA_KIR[0]:
        return
    if not majburiy and time.time() - _OBUNA_VAQT[0] < 120:
        return
    d = obunachilar()
    matn = json.dumps(d, ensure_ascii=False, indent=1)
    try:
        OBUNA_FILE.write_text(matn, encoding="utf-8")
    except Exception:
        pass
    try:
        gh_yoz("obunachilar.json", matn,
               f"chore: obunachilar ({len(d)} ta)")
        _OBUNA_KIR[0] = False
        _OBUNA_VAQT[0] = time.time()
    except Exception as e:
        log(f"[obuna] saqlanmadi: {e}")


def taklif_soni(uid):
    """Shu odam nechta odam olib kelgan."""
    uid = str(uid)
    return sum(1 for v in obunachilar().values()
               if str(v.get("kelgan") or "") == uid)


def taklif_havola(uid):
    nom = bot_nomi()
    if not nom:
        return ""
    return f"https://t.me/{nom}?start=r{uid}"


def kanalga_obunami(uid):
    """Odam kanalga obuna bo'lganmi. Xato bo'lsa — True deb hisoblaymiz
    (bot kanalda admin bo'lmasa, odamni to'sib qo'ymaslik uchun)."""
    try:
        r = tg_call("getChatMember",
                    data={"chat_id": TELEGRAM_CHANNEL, "user_id": uid},
                    timeout=30)
        return r.get("status") in ("creator", "administrator", "member")
    except Exception as e:
        log(f"[obuna] getChatMember: {e}")
        return True


def tg_document(chat, path, caption=None, markup=None):
    """PDF yuboradi. Birinchi safar fayl yuklanadi, keyin file_id ishlatiladi."""
    d = {"chat_id": chat, "parse_mode": "HTML"}
    if caption:
        d["caption"] = caption
    if markup:
        d["reply_markup"] = json.dumps(markup)
    if _SOVGA_ID[0]:
        d["document"] = _SOVGA_ID[0]
        try:
            return tg_call("sendDocument", data=d, timeout=60)
        except Exception:
            _SOVGA_ID[0] = ""      # file_id eskirgan — qaytadan yuklaymiz
    with open(path, "rb") as f:
        r = tg_call("sendDocument", data=d,
                    files={"document": (SOVGA_NOM, f)}, timeout=180)
    try:
        _SOVGA_ID[0] = (r.get("document") or {}).get("file_id", "")
    except Exception:
        pass
    return r


# ── Haftalik tasdiq ──────────────────────────────────────────────
TASDIQ_FILE = ROOT / "tasdiq.json"


def _tasdiq_fayl():
    try:
        d = json.loads(TASDIQ_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def tasdiqlar(tur="hikoya"):
    """Admin oldindan tasdiqlagan raqamlar. tur: hikoya yoki dars."""
    try:
        return {int(x) for x in _tasdiq_fayl().get(tur, [])}
    except Exception:
        return set()


def tasdiq_qosh(nlar, tur="hikoya"):
    """Raqamlarni tasdiqlangan deb belgilaydi va GitHub'ga yozadi."""
    nlar = [int(n) for n in nlar]
    if not nlar:
        return ""
    d = _tasdiq_fayl()
    d[tur] = sorted(tasdiqlar(tur) | set(nlar))[-90:]
    d["yangilangan"] = now().strftime("%Y-%m-%d %H:%M")
    matn = json.dumps(d, ensure_ascii=False, indent=1) + "\n"
    try:
        TASDIQ_FILE.write_text(matn, encoding="utf-8")
    except OSError as e:
        log(f"[tasdiq] fayl yozilmadi: {e}")
    try:
        return gh_yoz("tasdiq.json", matn,
                      f"tasdiq: {min(nlar)}-{max(nlar)} {tur}")
    except Exception as e:
        log(f"[tasdiq] GitHub'ga yozilmadi: {e}")
        return ""


def pick_source():
    """Bugungi post turini aniqlaydi."""
    if POST_SOURCE in ("hikoya", "stories"):
        return "hikoya"
    if POST_SOURCE in ("kurs", "curriculum"):
        return "kurs"
    if POST_SOURCE in ("yangilik", "news"):
        return "yangilik"
    if POST_SOURCE == "ai":
        return "ai"
    if SLOT == "ertalab":
        return "hikoya"
    # kechqurun — kurs va yangilik almashadi
    return "yangilik" if stories_state().get("oxirgi_kechki") == "kurs" else "kurs"


def stories_next_day():
    """Navbatdagi kun raqami. FORCE_DAY berilsa — o'sha kun."""
    forced = os.getenv("FORCE_DAY", "").strip()
    if forced:
        return int(forced)
    return stories_state().get("last_sent", 0) + 1


def stories_mark_sent(day, message_id=None):
    st = stories_state()
    st["last_sent"] = max(st.get("last_sent", 0), day)
    st.setdefault("sent", []).append(
        {"day": day, "date": now().strftime("%Y-%m-%d %H:%M"),
         "message_id": message_id})
    st["sent"] = st["sent"][-400:]
    STORIES_STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")


def stories_rollback(day):
    """day-kun hech qachon chiqmagandek qiladi — o'chirilgan post uchun."""
    st = stories_state()
    st["sent"] = [s for s in st.get("sent", []) if s.get("day") != day]
    st["last_sent"] = max([s.get("day", 0) for s in st["sent"]], default=0)
    STORIES_STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")


_STORIES_CACHE = {}


def stories_all():
    """Barcha hikoyalar: {kun_raqami: matn}. stories.json birinchi navbatda."""
    if _STORIES_CACHE:
        return _STORIES_CACHE
    if STORIES_JSON.exists():
        d = json.loads(STORIES_JSON.read_text(encoding="utf-8"))
        for k, v in (d.get("posts") or {}).items():
            _STORIES_CACHE[int(k)] = v.strip()
    elif STORIES_DIR.exists():
        for p in sorted(STORIES_DIR.glob("[0-9][0-9][0-9].html")):
            _STORIES_CACHE[int(p.stem)] = p.read_text(encoding="utf-8").strip()
    if not _STORIES_CACHE:
        raise RuntimeError("Hikoyalar topilmadi: stories.json ham, stories/ ham yo'q")
    return _STORIES_CACHE


_KURS_CACHE = {}


def kurs_all():
    if not _KURS_CACHE:
        if not KURS_JSON.exists():
            raise RuntimeError("kurs.json topilmadi")
        d = json.loads(KURS_JSON.read_text(encoding="utf-8"))
        for k, v in (d.get("posts") or {}).items():
            _KURS_CACHE[int(k)] = v.strip()
    return _KURS_CACHE


def kurs_next():
    forced = os.getenv("FORCE_KURS", "").strip()
    if forced:
        return int(forced)
    return stories_state().get("kurs_oxirgi", 0) + 1


def kurs_mark_sent(n, message_id=None):
    st = stories_state()
    st["kurs_oxirgi"] = max(st.get("kurs_oxirgi", 0), n)
    st["oxirgi_kechki"] = "kurs"
    STORIES_STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")


def yangilik_mark_sent():
    st = stories_state()
    st["oxirgi_kechki"] = "yangilik"
    STORIES_STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")


def kurs_rollback(n):
    """n-dars hech qachon chiqmagandek qiladi — o'chirilgan post uchun."""
    st = stories_state()
    if st.get("kurs_oxirgi") == n:
        st["kurs_oxirgi"] = n - 1
        STORIES_STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")


def kechki_tur_tiklash(qolgan_hist):
    """Post o'chirilgach, ertalab/kechqurun almashinuvini (oxirgi_kechki)
    qolgan tarixdan qayta hisoblaydi — aks holda kurs/yangilik navbati
    buziladi."""
    st = stories_state()
    for e in reversed(qolgan_hist):
        if e.get("tur") in ("kurs", "yangilik"):
            st["oxirgi_kechki"] = e["tur"]
            break
    else:
        st.pop("oxirgi_kechki", None)
    STORIES_STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")


def postni_ochir(entry):
    """Tarix yozuvidagi postni kanaldan o'chiradi va holatni shu post
    hech qachon chiqmagandek qaytaradi (kun/dars raqami, navbat)."""
    mid = entry.get("message_id")
    if mid:
        try:
            tg_delete_message(TELEGRAM_CHANNEL, mid)
        except RuntimeError as e:
            # Telegram'dan o'chmasa ham (masalan allaqachon o'chirilgan) —
            # holatni baribir tozalaymiz, aks holda navbat buzilib qoladi
            log(f"[ochir] Telegram'dan o'chmadi ({e}) — holat baribir tozalanadi")
    tur = entry.get("tur")
    if tur == "stories" and entry.get("day"):
        stories_rollback(entry["day"])
    elif tur == "kurs" and entry.get("dars"):
        kurs_rollback(entry["dars"])
    qolgan = hist_remove(entry)
    if tur in ("kurs", "yangilik"):
        kechki_tur_tiklash(qolgan)
    return entry


def build_kurs_post(n=None):
    """kurs.json dagi navbatdagi darsni post qilib beradi. Matn o'zgarmaydi."""
    all_ = kurs_all()
    n = n or kurs_next()
    if n > len(all_):
        raise RuntimeError(f"Kurs tugadi ({len(all_)} dars). "
                           f"stories_state.json dagi kurs_oxirgi ni 0 qiling.")
    text = all_[n]
    title = strip_tags(text.split("\n", 1)[0]).strip()
    log(f"=== Kurs {n}/{len(all_)} — {title} ===")
    toza = re.sub(r"^[^A-Za-zА-Яа-яЎўҚқҒғҲҳ0-9]+", "", title).strip()
    post = {
        "source": "kurs", "kurs_n": n, "title": title, "text": text,
        "card_title": toza if _ha("rasm_sarlavha", "yo'q") else "",
        "rasm_kerak": True,   # kurs va yangilikda rasm DOIM bo'ladi
        "badge": sozlama("kurs_hashtag", "#startap_kursi"),
        "image_idea": story_image_idea(title, text),
        "audio_script": (story_audio_script(text, title)
                         if _ha("audio") else None),
        "topic": {"topic": f"Startap kursi · {n}-dars · {title}"},
    }
    _attach_media(post)
    return post


YANGILIK_QOIDALARI = """
## YANGILIK POSTI UCHUN QO'SHIMCHA QOIDALAR

Bu post e'tiborni tortishi kerak. Quruq xabar — eng yomon variant.

Sarlavha:
- Ichida raqam, natija yoki kutilmagan fakt bo'lsin
- Yomon: "Kompaniya yangi model chiqardi"
- Yaxshi: "Yangi model kod yozishni ikki barobar arzonlashtirdi"

Birinchi gap:
- Eng kuchli fakt yoki savol bilan boshlansin
- "Kecha ma'lum bo'ldiki" kabi sust boshlanish yo'q

Ichida:
- Nima bo'lgani — 1-2 gap, aniq. Kim, nima, qachon
- Nega muhim — 1-2 gap
- <b>Sizga nima beradi</b> — bu qism majburiy, amaliy bo'lsin
- Faqat tekshirilgan raqam. Bo'lmasa — raqam yozma
- Berilgan faktlardagi manba nomini matn ichida tilga ol
  (masalan: "TechCrunch xabar berishicha...")

QAT'IY: bu YANGILIK posti, AI vositasining reklamasi emas.
"Claude'ga shunday yozing" kabi bo'lim QO'SHMA — yangilikning o'zi
aynan o'sha vosita haqida bo'lsagina bundan mustasno.

Yakun:
- Bugun sinab ko'rish mumkin bo'lgan aniq harakat yoki bitta savol
- Manba havolasi va kanal nomi AVTOMATIK qo'shiladi — ularni yozma

Uzunlik 500-900 belgi. Hashtag yo'q.

## TELEGRAMDA KO'RINISHI (SMM)

Post telefonda o'qiladi. Devor bo'lib turgan matnni hech kim o'qimaydi.

- Har bir abzats 1-3 gapdan oshmasin, orasida bo'sh qator bo'lsin
- Eng muhim raqam va natijani <b>qalin</b> qil — ko'z o'sha yerga tushsin
- Sarlavhadan keyin bitta bo'sh qator, keyin zarba beruvchi birinchi gap
- Xulosa qismida 3-4 ta qisqa qator bo'lsin, har biri bitta fikr,
  boshida mos emoji (🎯 ⏱ 🤔 📆 kabi) — bu qism skanerlab o'qiladi
- Oxirida bitta savol yoki aniq harakat, keyin kanal nomi
- CAPS LOCK bilan baqirma, ko'p undov belgisi qo'yma
- Ajratuvchi chiziq kerak bo'lsa faqat "━━━ ✦ ━━━" — uzun chizma
"""


# Haqiqiy yangilik manbalari. Model o'zidan to'qib chiqarmasligi uchun
# avval shu tasmalardan chinakam xabarlar olinadi, keyin model faqat
# shulardan tanlaydi va o'zbekchada tushuntiradi.
RSS_MANBALAR = [
    # O'zbekiston
    "https://www.spot.uz/uz/rss/",
    "https://www.gazeta.uz/uz/rss/",
    "https://kun.uz/uz/news/rss",
    # Jahon — startap va texnologiya
    "https://techcrunch.com/feed/",
    "https://news.ycombinator.com/rss",
    "https://www.theverge.com/rss/index.xml",
    # Google News qidiruvlari
    "https://news.google.com/rss/search?q=startup+funding+when:7d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=O%CA%BBzbekiston+startap+when:7d&hl=uz&gl=UZ&ceid=UZ:uz",
    "https://news.google.com/rss/search?q=IT+Park+Uzbekistan+when:7d&hl=uz&gl=UZ&ceid=UZ:uz",
    "https://news.google.com/rss/search?q=AI+tools+for+small+business+when:7d&hl=en&gl=US&ceid=US:en",
]


def _rss_oqi(url, cheklov=8):
    """Bitta RSS tasmasidan so'nggi xabarlarni oladi."""
    import xml.etree.ElementTree as ET
    r = requests.get(url, timeout=25,
                     headers={"User-Agent": "Mozilla/5.0 (compatible; AqlUstaxonaBot)"})
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    root = ET.fromstring(r.content)
    chiq = []
    # RSS (item) va Atom (entry) — ikkalasini ham qo'llab-quvvatlaymiz
    tugunlar = root.findall(".//item") or root.findall(
        ".//{http://www.w3.org/2005/Atom}entry")
    for it in tugunlar[:cheklov]:
        def olish(*nomlar):
            for n in nomlar:
                v = it.findtext(n)
                if v:
                    return v.strip()
                el = it.find("{http://www.w3.org/2005/Atom}" + n)
                if el is not None:
                    return (el.get("href") or el.text or "").strip()
            return ""
        sarlavha = olish("title")
        if not sarlavha:
            continue
        manba = ""
        s = it.find("source")
        if s is not None and s.text:
            manba = s.text.strip()
        chiq.append({
            "title": re.sub(r"\s+", " ", sarlavha)[:200],
            "date": olish("pubDate", "updated", "published")[:25],
            "source": manba,
            "link": olish("link")[:300],
        })
    return chiq


def yangilik_manbalar(cheklov=45):
    """Barcha tasmalardan haqiqiy xabarlarni yig'adi."""
    yigilgan, korilgan = [], set()
    for url in RSS_MANBALAR:
        try:
            for x in _rss_oqi(url):
                kalit = x["title"].lower()[:80]
                if kalit in korilgan:
                    continue
                korilgan.add(kalit)
                yigilgan.append(x)
        except Exception as e:
            log(f"[rss] {url.split('/')[2]}: {str(e)[:90]}")
    log(f"[rss] jami {len(yigilgan)} ta haqiqiy xabar yig'ildi")
    return yigilgan[:cheklov]


def topic_yangilik(avoid, hammasi=False):
    """Haqiqiy yangilik tasmalaridan mavzu tanlaydi.

    hammasi=True bo'lsa — bitta emas, butun ro'yxatni qaytaradi
    (haftalik taklif uchun kerak).
    """
    used = "\n".join(f"- {t}" for t in (hist_topics(30) + avoid)) or "(bo'sh)"
    bugun = now().strftime("%Y-%m-%d")

    xabarlar = []
    try:
        xabarlar = yangilik_manbalar()
    except Exception as e:
        log(f"[rss] umuman ishlamadi: {e}")

    if xabarlar:
        royxat = "\n".join(
            f"{i}. {x['title']}"
            + (f" | manba: {x['source']}" if x.get("source") else "")
            + (f" | sana: {x['date']}" if x.get("date") else "")
            + (f" | {x['link']}" if x.get("link") else "")
            for i, x in enumerate(xabarlar, 1))
        prompt = f"""Sen startap yangiliklari muharririsan. Bugungi sana: {bugun}.

Quyida internetdagi haqiqiy yangilik tasmalaridan olingan xabarlar
ro'yxati bor. Faqat SHU RO'YXATDAN tanla — o'zingdan hech narsa
qo'shma va to'qima.

XABARLAR:
{royxat}

VAZIFA: shulardan O'zbekistonlik boshlovchi tadbirkorga eng foydali
5 tasini tanla va har birini o'zbekchada tushuntir.

TANLASH MEZONI:
- Tadbirkorga amaliy foydasi bor (yangi imkoniyat, vosita, pul, qonun,
  bozor o'zgarishi)
- Siyosat, urush, mojaro, jinoyat, sport, mashhurlar — TANLAMA
- Bir xil mavzudagi ikkita xabarni tanlama
- Quyidagilar allaqachon chiqqan, ularni BERMA:
{used}

HAR BIR TANLOV UCHUN:
- topic: o'zbekcha sarlavha, 4-9 so'z, aniq voqeani bildirsin
  ("SI vositalari rivojlanmoqda" kabi umumiy gap EMAS)
- why: bu tadbirkorga aniq nima beradi, 1 gap
- key_facts: ro'yxatdagi xabardan olingan 2-3 ta ANIQ fakt.
  Har birida manba nomi bo'lsin. Raqam bo'lsa — raqamni yoz.

Javobni FAQAT shu JSON ko'rinishida ber:
{{"topics": [{{"topic": "...", "why": "...",
 "key_facts": ["fakt (manba)", "fakt 2", "fakt 3"]}}]}}"""
        data = gem_json(prompt, search=False, temperature=0.6)
    else:
        # Tasmalar ishlamasa — qidiruv bilan urinamiz (eski yo'l)
        log("[yangilik] tasmalar bo'sh — qidiruvga o'tilmoqda")
        prompt = f"""Sen startap va texnologiya yangiliklari muharririsan.
Bugungi sana: {bugun}.

Google qidiruvidan foydalanib, SO'NGGI 7 KUN ichida yuz bergan va
O'zbekistonlik boshlovchi tadbirkorga foydali bo'ladigan 5 ta yangilik top.

Mavzular: startap sarmoyalari, yangi mahsulot va imkoniyatlar,
sun'iy intellekt vositalari, O'zbekistondagi biznes va IT yangiliklari,
jahon bozoridagi muhim o'zgarishlar, tadbirkorga tegishli qonun
o'zgarishlari.

QAT'IY TALABLAR:
- Faqat HAQIQIY, tekshirilgan xabar. To'qima yoki taxmin YO'Q
- Umumiy maslahat EMAS — aniq voqea bo'lsin (kim, nima, qachon)
- Sana so'nggi 7 kun ichida bo'lsin
- Har bir yangilikda manba nomi va sana bo'lsin
- Siyosat, urush, mojaro mavzulari YO'Q
- Quyidagilar allaqachon chiqqan, ularni BERMA:
{used}

Javobni FAQAT shu JSON ko'rinishida ber:
{{"topics": [{{"topic": "yangilik sarlavhasi o'zbekcha 4-9 so'z",
 "why": "tadbirkorga nima beradi, 1 gap",
 "key_facts": ["fakt (manba, sana)", "fakt 2", "fakt 3"]}}]}}"""
        data = gem_json(prompt, search=True, temperature=0.9)
    topics = data.get("topics") if isinstance(data, dict) else data
    if not topics:
        raise RuntimeError("Yangilik topilmadi")

    # Tanlangan mavzuga mos haqiqiy xabarlarni topib, havolasini biriktiramiz
    for t in topics:
        if isinstance(t, dict):
            t["manbalar"] = _mos_manbalar(t, xabarlar)

    if hammasi:
        return topics
    seen = {t.lower().strip() for t in hist_topics(30) + avoid}
    for t in topics:
        if t.get("topic", "").lower().strip() not in seen:
            log(f"[yangilik] {t['topic']} "
                f"({len(t.get('manbalar') or [])} ta manba)")
            return t
    return topics[0]


_SOZ_NAQSH = re.compile(r"[a-z0-9']{4,}", re.I)


def _mos_manbalar(mavzu, xabarlar, cheklov=3):
    """Model tanlagan mavzuga eng mos keladigan haqiqiy xabarlarni topadi.

    Sarlavha va faktlardagi so'zlar bilan xabar sarlavhasidagi so'zlarni
    solishtiramiz — eng ko'p mos kelgani manba bo'ladi.
    """
    if not xabarlar:
        return []
    matn = " ".join([mavzu.get("topic", ""), mavzu.get("why", "")]
                    + list(mavzu.get("key_facts") or []))
    sozlar = {w.lower() for w in _SOZ_NAQSH.findall(matn)}
    if not sozlar:
        return []
    ballar = []
    for x in xabarlar:
        if not (x.get("link") or "").startswith("http"):
            continue
        xs = {w.lower() for w in _SOZ_NAQSH.findall(x.get("title", ""))}
        umumiy = len(sozlar & xs)
        # Manba nomi faktlarda tilga olingan bo'lsa — qo'shimcha ball
        nom = (x.get("source") or "").lower()
        if nom and nom in matn.lower():
            umumiy += 2
        if umumiy >= 2:
            ballar.append((umumiy, x))
    ballar.sort(key=lambda p: -p[0])
    return [x for _, x in ballar[:cheklov]]


FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def _font(size):
    from PIL import ImageFont
    for fp in FONT_PATHS:
        if Path(fp).exists():
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def make_card(title, badge, bg_path=None, out=None):
    """Post sarlavhasi yozilgan rasm tayyorlaydi.

    bg_path bo'lsa — Gemini chizgan rasm fon bo'ladi, ustiga matn tushadi.
    Bo'lmasa — brend rangidagi gradient fon chiziladi.
    """
    from PIL import Image, ImageDraw, ImageFilter
    # 1920x1080 — Telegram siqqanidan keyin ham tiniq qolsin.
    W, H = 1920, 1080
    S = W / 1280.0          # eski o'lchamlarga nisbatan koeffitsient
    out = Path(out or (BUILD / "card.png"))

    if bg_path and Path(bg_path).exists():
        img = Image.open(bg_path).convert("RGB")
        # markazdan kesib 16:9 ga keltiramiz
        r = max(W / img.width, H / img.height)
        img = img.resize((int(img.width * r) + 1, int(img.height * r) + 1),
                         Image.LANCZOS)
        x = (img.width - W) // 2
        y = (img.height - H) // 2
        img = img.crop((x, y, x + W, y + H))
        # Kattalashtirgandan keyin yumshab qolgan detallarni qaytaramiz.
        if r > 1.02:
            img = img.filter(ImageFilter.UnsharpMask(
                radius=2.0,
                percent=int(min(120, 50 + 80 * (r - 1.0))),
                threshold=3))
    else:
        img = Image.new("RGB", (W, H), (32, 28, 26))
        d0 = ImageDraw.Draw(img)
        for i in range(H):
            k = i / H
            d0.line([(0, i), (W, i)],
                    fill=(int(46 + 171 * k * 0.55),
                          int(38 + 80 * k * 0.55),
                          int(34 + 52 * k * 0.55)))

    # Sarlavha TEPADA turadi — shuning uchun yuqoridan pastga qorayish.
    # Pastda ham yengil soya: kanal nomi o'qilishi uchun.
    # Asosiysi — RASM ko'rinsin. Shuning uchun soya yengil va tor.
    veil = Image.new("L", (1, H))
    for i in range(H):
        k = i / H
        tepa = max(0.0, (0.30 - k) / 0.30) ** 1.2 * (0.72 if title else 0.42)
        past = max(0.0, (k - 0.88) / 0.12) ** 1.4 * 0.50
        veil.putpixel((0, i), int(255 * min(1.0, max(tepa, past))))
    veil = veil.resize((W, H))
    img = Image.composite(Image.new("RGB", (W, H), (18, 15, 14)), img, veil)

    d = ImageDraw.Draw(img)
    M = int(78 * S)
    sh = max(2, int(2 * S))          # yozuv soyasi

    # 1) Tepada hashtag
    y = int(56 * S)
    if badge:
        bf = _font(int(34 * S))
        d.text((M + sh, y + sh), badge, font=bf, fill=(0, 0, 0))
        d.text((M, y), badge, font=bf, fill=(232, 145, 112))
        y += int(56 * S)

    # 2) Uning tagida sarlavha — sig'maguncha kichraytiramiz
    size = int(76 * S)
    kichik = int(40 * S)
    while size >= kichik:
        f = _font(size)
        lines = _wrap(d, title, f, W - 2 * M)
        lh = int(size * 1.20)
        if len(lines) * lh <= int(300 * S):
            break
        size -= max(1, int(5 * S))
    for ln in lines:
        d.text((M + sh, y + sh), ln, font=f, fill=(0, 0, 0))
        d.text((M, y), ln, font=f, fill=(255, 253, 251))
        y += lh

    # 3) Pastki chap burchakda kanal nomi
    d.rectangle([M, H - int(62 * S), M + int(92 * S), H - int(56 * S)],
                fill=(217, 119, 87))
    cf = _font(int(26 * S))
    d.text((M + int(112 * S), H - int(68 * S)),
           sozlama("kanal_nomi", "@aqlustaxonastartap"),
           font=cf,
           fill=(228, 222, 216))

    img.save(out, "PNG", optimize=True)
    return out


def _attach_media(post):
    if post.get("rasm_kerak") is False:
        log("[rasm] bu post uchun rasm so'ralmagan — o'tkazib yuborildi")
        post["image_path"] = None
    else:
        try:
            post["image_path"] = agent3_image(post)
        except Exception as e:
            log(f"[rasm] chizilmadi: {e}")
            post["image_path"] = None

    # Kurs va yangilik postlarida sarlavha rasm ustiga yoziladi
    if post.get("source") in ("kurs", "yangilik"):
        try:
            post["image_path"] = make_card(
                post.get("card_title", ""),
                post.get("badge"), post.get("image_path"))
            log("[rasm] kartochka tayyor")
        except Exception as e:
            log(f"[rasm] kartochka yasalmadi: {e}")

    if not _ha("audio") or not post.get("audio_script"):
        post["voice_path"] = None
        return post
    try:
        post["voice_path"] = agent4_voice(post)
    except Exception as e:
        log(f"[audio] yozilmadi: {e}")
        post["voice_path"] = None
    return post


# Faqat ajratuvchi belgilardan iborat qator
_NAQSH_QATOR = re.compile(r"^[\s━─—▬=_✦✧✩◆◇●•·~*+]{5,}$")


def _qisqa_naqsh(matn):
    """Ajratuvchi chiziqni qisqa qilib, o'rtaroqqa suradi.

    Telefonda "━━━━━━━━━━━━━ ✦ ━━━━━━━━━━━━━" ikki qatorga bo'linib
    ketadi. Endi qisqa bo'ladi va oldiga keng bo'shliq qo'yilib
    matnning o'rtasiga yaqin turadi — kanalda chiroyli ko'rinadi.
    """
    belgi = sozlama("ajratuvchi", "") or "\u2003\u2003\u2003\u2003\u2003━━━ ✦ ━━━"
    qatorlar = []
    for q in (matn or "").split("\n"):
        t = q.strip()
        if t and _NAQSH_QATOR.match(t):
            qatorlar.append(belgi)
        else:
            qatorlar.append(q)
    return "\n".join(qatorlar)


def story_read(day):
    all_ = stories_all()
    if day not in all_:
        raise RuntimeError(f"{day}-kun hikoyasi topilmadi")
    return _qisqa_naqsh(all_[day])


def story_audio_script(text, title):
    """Hikoyadan qisqa audio matn tayyorlaydi (ElevenLabs uchun)."""
    plain = strip_tags(text)
    prompt = f"""Quyidagi hikoyani audio uchun qisqacha so'zlab ber.

QOIDALAR:
- O'zbek tili, lotin yozuvi, jonli og'zaki ohang
- 70-100 so'z
- HTML teg, emoji, havola, hashtag YO'Q
- Hikoyaning asosiy g'oyasi va yakuniy darsi qolsin
- Faqat matnning o'zini qaytar, boshqa hech narsa yozma

HIKOYA:
{plain[:3000]}"""
    try:
        return strip_tags(gem_text(prompt, temperature=0.6)).strip()
    except Exception as e:
        log(f"[audio] matn tayyorlanmadi: {e}")
        return " ".join(plain.split()[:90])


def story_image_idea(title, text):
    """Rasm uchun ingliz tilida qisqa vizual g'oya.

    Eng muhimi: rasm postning MA'NOSINI tushuntirsin — odam faqat rasmga
    qarab, bitta so'z o'qimasdan, post nima haqidaligini anglasin."""
    prompt = f"""Read this Uzbek startup post and describe ONE 3D scene that
EXPLAINS ITS MAIN IDEA visually.

Requirements:
- A person who only sees the picture, without reading any words, must
  understand what the post is teaching.
- Stage the idea: show the situation, the contrast, the before/after,
  or the cause and effect. Not a generic "business" object.
- 1-3 objects maximum, one clear focal point.
- Wordless symbols (arrow, tick, cross, coin, rising line, question mark)
  are allowed and helpful. No written words or letters.
- English, ONE sentence, concrete and visual. Return only the sentence.

TITLE: {title}
POST: {strip_tags(text)[:1500]}"""
    try:
        out = clean(gem_text(prompt, temperature=0.8)).strip()
        return out.split("\n")[0][:300] or title
    except Exception as e:
        log(f"[rasm] g'oya olinmadi: {e}")
        return title


def build_story_post(day=None):
    """stories/ papkasidan navbatdagi tayyor hikoyani post qilib beradi.

    Hikoya matni O'ZGARTIRILMAYDI — QA/qayta yozish bosqichi yo'q.
    Faqat rasm va audio qaytadan yaratiladi.
    """
    day = day or stories_next_day()
    if day > STORIES_TOTAL:
        raise RuntimeError(f"Barcha {STORIES_TOTAL} ta hikoya chiqib bo'lgan. "
                           f"stories_state.json dagi last_sent ni 0 qiling.")
    text = story_read(day)
    first = text.split("\n", 1)[0]
    title = strip_tags(first).strip()
    log(f"=== Hikoya {day}/{STORIES_TOTAL} — {title} ({len(text)} belgi) ===")

    # Hikoya tepasiga [N-hikoya] kabi belgi qo'yiladi
    belgi = sozlama("hikoya_belgi", "[{N}-hikoya]")
    if belgi:
        text = f"<b>{belgi.replace('{N}', str(day))}</b>\n\n" + text

    # Admin xohlasa hikoyalarga rasm chizilmaydi — bu ancha tez ishlaydi
    rasm_kerak = _ha("hikoya_rasm", "yo'q")
    post = {
        "source": "stories",
        "day": day,
        "title": title,
        "text": text,
        "rasm_kerak": rasm_kerak,
        "image_idea": story_image_idea(title, text) if rasm_kerak else None,
        "audio_script": (story_audio_script(text, title)
                         if _ha("audio") else None),
        "topic": {"topic": f"365-hikoya · {day}-kun · {title}"},
    }
    _attach_media(post)
    return post


# ══════════════════════════════════════════════════════════════════
#  3.5-QISM — GITHUB (bot repo'ni o'zi o'zgartiradi)
# ══════════════════════════════════════════════════════════════════

GH_API = "https://api.github.com"


def gh_bor():
    """GitHub'ga yozish imkoni bormi."""
    return bool(GITHUB_TOKEN and GITHUB_REPO)


def _gh_sarlavha():
    return {"Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def gh_oqi(yol):
    """Repodagi faylni o'qiydi. Qaytaradi (matn, sha). Yo'q bo'lsa (None, None)."""
    r = requests.get(f"{GH_API}/repos/{GITHUB_REPO}/contents/{yol}",
                     headers=_gh_sarlavha(), params={"ref": GITHUB_BRANCH},
                     timeout=60)
    if r.status_code == 404:
        return None, None
    if r.status_code != 200:
        raise RuntimeError(f"GitHub o'qish xatosi {r.status_code}: {r.text[:200]}")
    j = r.json()
    return base64.b64decode(j.get("content", "")).decode("utf-8", "replace"), j.get("sha")


def gh_yoz(yol, matn, izoh):
    """Repodagi faylni yozadi/yangilaydi. Commit havolasini qaytaradi."""
    if not gh_bor():
        raise RuntimeError("GitHub ulanmagan (GITHUB_TOKEN yo'q)")
    if yol.startswith(".github/workflows/"):
        # Actions tokeni workflow fayllarini o'zgartira olmaydi.
        raise RuntimeError("Workflow fayllarini bot o'zgartira olmaydi")
    _, sha = gh_oqi(yol)
    body = {"message": izoh, "branch": GITHUB_BRANCH,
            "content": base64.b64encode(matn.encode("utf-8")).decode("ascii")}
    if sha:
        body["sha"] = sha
    r = requests.put(f"{GH_API}/repos/{GITHUB_REPO}/contents/{yol}",
                     headers=_gh_sarlavha(), json=body, timeout=90)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub yozish xatosi {r.status_code}: {r.text[:250]}")
    j = r.json()
    return (j.get("commit") or {}).get("html_url", "")


def gh_suhbat_yoz(kim, matn):
    """Admin bilan bo'lgan har bir gapni GitHub'dagi kundalikka yozadi.

    Fayl: suhbat/YYYY-MM.md — oyiga bitta. Xato bo'lsa jim o'tadi,
    chunki bu botning asosiy ishiga xalaqit bermasligi kerak."""
    if not gh_bor():
        return None
    try:
        yol = f"suhbat/{now():%Y-%m}.md"
        eski, _ = gh_oqi(yol)
        qator = f"- **{now():%d.%m %H:%M}** · {kim}: {matn.strip()}\n"
        yangi = (eski or f"# Suhbat kundaligi — {now():%Y-%m}\n\n") + qator
        return gh_yoz(yol, yangi, f"suhbat: {matn.strip()[:60]}")
    except Exception as e:
        print(f"[github] suhbat yozilmadi: {e}")
        return None


# GitHub yozuvlari navbat orqali, alohida oqimda ketadi — shunda
# admin javobni kutib o'tirmaydi. Navbat ketma-ket bajarilgani uchun
# ikkita yozuv bir-birini urib ketmaydi.
_GH_NAVBAT = []
_GH_LOCK = threading.Lock()
_GH_ISHCHI = [None]


def _gh_ishla():
    while True:
        with _GH_LOCK:
            if not _GH_NAVBAT:
                _GH_ISHCHI[0] = None
                return
            kim, matn = _GH_NAVBAT.pop(0)
        try:
            gh_suhbat_yoz(kim, matn)
        except Exception as e:
            print(f"[github] fon xatosi: {e}")


def gh_suhbat_fonda(kim, matn):
    """Suhbatni GitHub'ga fonda yozadi. Darhol qaytadi."""
    if not gh_bor():
        return False
    with _GH_LOCK:
        _GH_NAVBAT.append((kim, matn))
        if _GH_ISHCHI[0] is None:
            _GH_ISHCHI[0] = threading.Thread(target=_gh_ishla, daemon=True)
            _GH_ISHCHI[0].start()
    return True


def gh_sozlama_yoz(kalit, qiymat):
    """Bitta sozlamani o'zgartirib, GitHub'ga commit qiladi."""
    d = sozlamalar()
    d[kalit] = qiymat
    matn = json.dumps(d, ensure_ascii=False, indent=2) + "\n"
    SOZLAMA_FILE.write_text(matn, encoding="utf-8")
    if not gh_bor():
        return None
    return gh_yoz("sozlamalar.json", matn, f"sozlama: {kalit} = {qiymat}"[:70])


# ══════════════════════════════════════════════════════════════════
#  4-QISM — GEMINI
# ══════════════════════════════════════════════════════════════════

GEM_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


# Qaysi model nimani qabul qilmasligini bir marta o'rganib, keyin
# o'sha modelga umuman yubormaymiz — har safar 400 olib vaqt yo'qotmaymiz.
_THINKING_YOQ = set()
_JSON_YOQ = set()


def gem_post(model, body):
    last = None
    aylanish = [0]
    # Har bir kalitga kamida bittadan imkon beramiz
    for a in range(max(4, len(GEMINI_KEYS) + 2)):
        try:
            r = requests.post(f"{GEM_BASE}/{model}:generateContent",
                              params={"key": gem_key()}, json=body, timeout=240)
            if r.status_code == 200:
                return r.json()
            # Kvota yoki kalit muammosi — zaxira kalitga o'tamiz.
            # Barcha kalitlar bir marta sinalgach — biroz kutamiz,
            # chunki kalitlarni tez-tez almashtirish kvotani tiklamaydi.
            if r.status_code in (429, 403):
                aylanish[0] += 1
                # Barcha kalitlar bir marta sinalgan bo'lsa — kvota
                # butun loyihada tugagan. Yana aylantirish foydasiz,
                # faqat vaqt yeydi. Darhol to'xtaymiz.
                if aylanish[0] >= len(GEMINI_KEYS) or not gem_rotate_key():
                    raise RuntimeError(
                        f"Gemini kvotasi tugagan (HTTP {r.status_code}, "
                        f"{len(GEMINI_KEYS)} ta kalitda ham). "
                        f"Javob: {r.text[:200]}")
                last = f"HTTP {r.status_code} (kalit almashtirildi)"
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                time.sleep(5 * (a + 1))
                continue
            # 400 = so'rov ichida modelga yoqmagan narsa bor.
            # Gemini 3 modellari thinkingBudget ni QABUL QILMAYDI (ular
            # thinkingLevel ishlatadi) va xato matnida "thinking" so'zi
            # umuman yo'q — shuning uchun matnga qaramay olib tashlaymiz.
            if r.status_code == 400:
                gc = body.get("generationConfig", {})
                if gc.pop("thinkingConfig", None) is not None:
                    print(f"[gemini] {model}: thinkingConfig qo'llanmadi — "
                          f"usiz qayta urinilmoqda")
                    _THINKING_YOQ.add(model)
                    continue
                if gc.pop("responseMimeType", None) is not None:
                    print(f"[gemini] {model}: JSON rejimi qo'llanmadi — "
                          f"usiz qayta urinilmoqda")
                    _JSON_YOQ.add(model)
                    continue
            raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:500]}")
        except requests.RequestException as e:
            last = str(e)
            time.sleep(5 * (a + 1))
    raise RuntimeError(f"Gemini so'rovi muvaffaqiyatsiz: {last}")


def gem_text(prompt, search=False, temperature=0.9, json_mode=False,
             max_tokens=16384):
    def _tana(model):
        """Har bir model uchun so'rovni alohida yig'amiz — chunki
        modellar bir xil sozlamani qabul qilmaydi."""
        gc = {"temperature": temperature, "maxOutputTokens": max_tokens}
        # "O'ylash" rejimini o'chiramiz — u chiqish tokenlarini yeb qo'yadi.
        # Lekin Gemini 3 buni qabul qilmaydi, shuning uchun bir marta
        # o'rgangandan keyin qayta yubormaymiz.
        if model not in _THINKING_YOQ:
            gc["thinkingConfig"] = {"thinkingBudget": 0}
        b = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": gc,
            "safetySettings": [
                {"category": c, "threshold": "BLOCK_ONLY_HIGH"} for c in (
                    "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "HARM_CATEGORY_DANGEROUS_CONTENT")],
        }
        if search:
            b["tools"] = [{"google_search": {}}]
        elif json_mode and model not in _JSON_YOQ:
            gc["responseMimeType"] = "application/json"
        return b

    models = [GEMINI_TEXT_MODEL] + [m for m in TEXT_MODEL_FALLBACKS
                                     if m != GEMINI_TEXT_MODEL]
    resp, last_err = None, None
    for model in models:
        try:
            resp = gem_post(model, _tana(model))
        except RuntimeError as e:
            last_err = e
            print(f"[gemini] {model}: {str(e)[:150]} — keyingi modelga o'tilmoqda")
            continue
        if model != GEMINI_TEXT_MODEL:
            print(f"[gemini] matn modeli: {model}")
        break
    if resp is None:
        # 1-zaxira: Grok (agar hisobda kredit bo'lsa)
        if GROK_KEYS and not _GROK_OLDI[0]:
            try:
                print("[gemini] hammasi ishlamadi — Grok'ga o'tilmoqda")
                return grok_text(prompt, json_mode=json_mode or not search,
                                 max_tokens=min(max_tokens, 8192))
            except Exception as e:
                print(f"[grok] ishlamadi: {str(e)[:150]}")
                last_err = e
        # 2-zaxila: Cloudflare Workers AI — bepul, kunlik limiti alohida
        if CF_ACCOUNT and CF_TOKEN:
            print("[gemini] Cloudflare matn modeliga o'tilmoqda (bepul zaxira)")
            return cf_text(prompt, json_mode=json_mode or not search,
                           temperature=temperature,
                           max_tokens=min(max_tokens, 8192))
        raise RuntimeError(f"Gemini so'rovi muvaffaqiyatsiz (barcha modellar): {last_err}")

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


# Cloudflare Workers AI — matn uchun bepul zaxira. Kunlik limiti
# Gemini'nikidan alohida, shuning uchun Gemini kvotasi tugaganda ham
# post chiqadi.
CF_MATN_MODELLAR = [
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    "@cf/meta/llama-3.1-8b-instruct",
    "@cf/qwen/qwen1.5-14b-chat-awq",
]


def _cf_matn(j):
    """Cloudflare javobidan matnni ajratadi.

    Model turiga qarab javob har xil ko'rinishda keladi: oddiy satr,
    {"response": "..."}, {"response": {"content": "..."}} yoki
    OpenAI uslubidagi choices ro'yxati. Hammasini qamrab olamiz.
    """
    def matn(v, chuqur=0):
        if v is None or chuqur > 4:
            return ""
        if isinstance(v, str):
            return v
        if isinstance(v, list):
            return "".join(matn(x, chuqur + 1) for x in v)
        if isinstance(v, dict):
            for k in ("response", "content", "text", "output", "result",
                      "message", "answer"):
                if k in v:
                    s = matn(v[k], chuqur + 1)
                    if s:
                        return s
            ch = v.get("choices")
            if isinstance(ch, list) and ch:
                return matn(ch[0], chuqur + 1)
        return ""
    return matn(j.get("result") if isinstance(j, dict) else j).strip()


def cf_text(prompt, json_mode=False, temperature=0.7, max_tokens=4096):
    """Cloudflare Workers AI orqali matn yozadi."""
    if not (CF_ACCOUNT and CF_TOKEN):
        raise RuntimeError("Cloudflare kaliti yo'q")
    qoshimcha = ("\n\nJavobni FAQAT to'g'ri JSON ko'rinishida ber. "
                 "Hech qanday izoh, sarlavha yoki ``` belgisi qo'shma."
                 if json_mode else "")
    oxirgi = ""
    for model in CF_MATN_MODELLAR:
        try:
            r = requests.post(
                f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}"
                f"/ai/run/{model}",
                headers={"Authorization": f"Bearer {CF_TOKEN}"},
                json={"messages": [{"role": "user",
                                    "content": prompt + qoshimcha}],
                      "temperature": max(0.1, min(1.0, temperature)),
                      "max_tokens": min(max_tokens, 4096)},
                timeout=180)
        except requests.RequestException as e:
            oxirgi = f"{model}: tarmoq — {str(e)[:120]}"
            continue
        if r.status_code != 200:
            oxirgi = f"{model}: HTTP {r.status_code} {r.text[:180]}"
            print(f"[cloudflare] {oxirgi}")
            continue
        try:
            out = _cf_matn(r.json())
        except Exception as e:
            oxirgi = f"{model}: javobni o'qib bo'lmadi — {str(e)[:120]}"
            print(f"[cloudflare] {oxirgi}")
            continue
        if not out:
            oxirgi = f"{model}: bo'sh javob"
            continue
        if json_mode:
            out = re.sub(r"^```(?:json)?|```$", "", out.strip(),
                         flags=re.M).strip()
        print(f"[cloudflare] matn tayyor — {model}")
        return out
    raise RuntimeError(f"Cloudflare matn modellari ishlamadi: {oxirgi}")


# Grok hisobida kredit bo'lmasa har safar 403 qaytaradi va bekorga
# vaqt ketadi. Bir marta bilib olamiz-u, boshqa bezovta qilmaymiz.
_GROK_OLDI = [""]


def grok_text(prompt, json_mode=False, temperature=0.7, max_tokens=8192):
    """Gemini butunlay ishlamay qolganda ishlatiladigan ENG OXIRGI zaxira."""
    if not GROK_KEYS:
        raise RuntimeError("GROK_API_KEY yo'q — zaxira ishlamaydi")
    if _GROK_OLDI[0]:
        raise RuntimeError(_GROK_OLDI[0])
    models = [GROK_MODEL] + [m for m in GROK_MODEL_FALLBACKS if m != GROK_MODEL]
    last = None
    for model in models:
        model_died = False
        for key in GROK_KEYS:
            body = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            try:
                r = requests.post(
                    "https://api.x.ai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    json=body, timeout=120)
            except requests.RequestException as e:
                last = str(e)
                continue
            if r.status_code == 200:
                out = (r.json().get("choices") or [{}])[0].get(
                    "message", {}).get("content", "").strip()
                if out:
                    print(f"[grok] javob oldi — model {model}")
                    return out
                last = "Grok bo'sh javob qaytardi"
                continue
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code in (401, 403):
                # Kalit yaroqsiz yoki hisobda kredit yo'q — bu model
                # almashtirish bilan tuzalmaydi, butun Grokni o'chiramiz.
                sabab = ("Grok hisobida kredit yo'q (xAI konsolida "
                         "to'ldirish kerak)" if "credit" in r.text.lower()
                         or "licen" in r.text.lower()
                         else "Grok kaliti qabul qilinmadi")
                _GROK_OLDI[0] = sabab
                raise RuntimeError(sabab)
            if r.status_code == 404:
                model_died = True
        if model_died:
            continue
    raise RuntimeError(f"Grok ham javob bermadi: {last}")


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
    "gemini-3.1-flash-image",
    "gemini-3-pro-image",
    "gemini-3.1-flash-lite-image",
    "gemini-2.5-flash-image",
]


# ── BEPUL RASM ZAXIRASI ───────────────────────────────────────────
# Google nano banana'ni API orqali BEPUL bermaydi (free tier limit: 0).
# Shuning uchun Gemini rasm chizolmasa — bepul xizmatlarga o'tamiz.
CF_ACCOUNT = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CF_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()


# Flux (Cloudflare/Pollinations) uzun ko'rsatma-matnni tushunmaydi —
# unga QISQA, zich, tasviriy jumla kerak. Gemini uchun yozilgan uzun
# IMAGE_STYLE'ni o'sha ko'yi yuborsak, rasm tushunarsiz chiqadi.
# Shuning uchun bepul xizmatlarga alohida qisqa prompt yasaymiz.
FLUX_USLUB = (
    "photorealistic 3D render of a real life scene, hyperrealistic CGI, "
    "physically based rendering, real world materials — wood, paper, "
    "fabric, brushed metal, glass, ceramic, real skin, fine surface "
    "texture detail, natural window daylight, global illumination, soft "
    "realistic contact shadows, warm terracotta and cream natural palette, "
    "50mm lens, razor sharp focus on the subject, crisp clean edges, "
    "ultra detailed, 8K, high resolution octane render, believable "
    "everyday moment, clean uncluttered composition, single clear focal "
    "point, 16:9 wide")

FLUX_TAQIQ = ("no text, no words, no letters, no numbers, no logo, "
              "no watermark, no close-up human face, no robot, "
              "no circuit board, not cartoon, not clay, not plastic toy, "
              "no figurine, not a flat illustration, not stylized, "
              "not cute, not surreal, not absurd, not a joke, "
              "not cluttered, not blurry, no heavy depth of field blur, "
              "no noise, no grain, no motion blur, not low resolution")


def flux_prompt(gap):
    """Uzun ko'rsatmani Flux tushunadigan qisqa tasvirga aylantiradi."""
    gap = (gap or "").strip()
    # Agar uzun ko'rsatma kelib qolsa — faqat sahna tavsifini ajratamiz.
    m = re.search(r"The scene to build:\s*(.+?)(?:\n\s*\n|$)", gap, re.S)
    if m:
        gap = m.group(1)
    gap = re.sub(r"\s+", " ", gap).strip()[:600]
    return f"{gap}. {FLUX_USLUB}. {FLUX_TAQIQ}"


def _pollinations(prompt, out_path):
    """Pollinations — mutlaqo bepul, kalit ham, ro'yxatdan o'tish ham
    kerak emas. Ichida Flux modeli ishlaydi."""
    from urllib.parse import quote
    p = flux_prompt(prompt)
    url = ("https://image.pollinations.ai/prompt/"
           + quote(p, safe="")
           + "?width=1920&height=1080&nologo=true&model=flux&enhance=true")
    r = requests.get(url, timeout=180)
    if r.status_code != 200 or len(r.content) < 5000:
        raise RuntimeError(f"Pollinations HTTP {r.status_code}, "
                           f"{len(r.content)} bayt")
    Path(out_path).write_bytes(r.content)
    return Path(out_path)


def _cloudflare(prompt, out_path):
    """Cloudflare Workers AI — bepul tarifda kuniga ancha rasm beradi.
    CLOUDFLARE_ACCOUNT_ID va CLOUDFLARE_API_TOKEN kerak."""
    if not (CF_ACCOUNT and CF_TOKEN):
        raise RuntimeError("Cloudflare kaliti yo'q")
    p = flux_prompt(prompt)
    oxirgi = ""
    # steps=8 — flux-1-schnell uchun eng yuqori sifat.
    for tana in ({"prompt": p[:2000], "steps": 8},
                 {"prompt": p[:2000], "steps": 4}):
        r = requests.post(
            f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}"
            f"/ai/run/@cf/black-forest-labs/flux-1-schnell",
            headers={"Authorization": f"Bearer {CF_TOKEN}"},
            json=tana, timeout=180)
        if r.status_code != 200:
            oxirgi = f"Cloudflare HTTP {r.status_code}: {r.text[:200]}"
            continue
        b64 = (r.json().get("result") or {}).get("image", "")
        if not b64:
            oxirgi = "Cloudflare rasm qaytarmadi"
            continue
        Path(out_path).write_bytes(base64.b64decode(b64))
        return Path(out_path)
    raise RuntimeError(oxirgi or "Cloudflare noma'lum xato")


def bepul_rasm(prompt, out_path):
    """Gemini ishlamaganda ishlatiladigan BEPUL rasm chizuvchilar."""
    sabablar = []
    for nom, f in (("cloudflare", _cloudflare), ("pollinations", _pollinations)):
        try:
            p = f(prompt, out_path)
            print(f"[rasm] bepul zaxira ishladi — {nom}")
            return p
        except Exception as e:
            sabablar.append(f"{nom}: {str(e)[:150]}")
    raise RuntimeError("Bepul zaxira ham ishlamadi:\n- "
                       + "\n- ".join(sabablar))


_RASM_YOQ = [False]


def gem_image(prompt, out_path):
    """Rasm generatsiya qiladi. Bir necha model va sozlamani sinab ko'radi."""
    if _RASM_YOQ[0]:
        raise RuntimeError("Gemini rasm bu hisobda mavjud emas (limit: 0)")
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


def tg_typing(chat):
    """Telegramda "yozyapti..." belgisini ko'rsatadi — admin javob
    kelayotganini darhol biladi."""
    try:
        tg_call("sendChatAction", data={"chat_id": chat, "action": "typing"},
                timeout=10)
    except Exception:
        pass


def tg_delete_message(chat, message_id):
    return tg_call("deleteMessage", data={"chat_id": chat, "message_id": message_id})


def imzo_qosh(text, post=None):
    """Har bir post oxiriga manba havolalari va kanal imzosini qo'shadi."""
    kanal = sozlama("kanal_nomi", "@aqlustaxonastartap")
    text = (text or "").rstrip()

    # 1) Yangilik bo'lsa — manba havolalari
    if post and post.get("source") == "yangilik":
        havolalar = []
        for h in (post.get("manbalar") or [])[:3]:
            url = (h.get("link") or "").strip()
            nom = (h.get("source") or "").strip() or "Manba"
            if url.startswith("http") and url not in [x[1] for x in havolalar]:
                havolalar.append((nom, url))
        if havolalar and "Manba:" not in text:
            qator = " · ".join(
                f'<a href="{html.escape(u, quote=True)}">{html.escape(n)}</a>'
                for n, u in havolalar)
            text += f"\n\n🔗 <b>Manba:</b> {qator}"

    # 2) Kanal imzosi — hamma postda
    if kanal and kanal not in text:
        text += f"\n\n👉 {kanal}"
    return text


def tg_send_post(chat, post):
    """Rasm + matn + audio yuboradi. Matn caption limitidan uzun bo'lsa —
    rasm alohida, matn alohida xabar sifatida ketadi."""
    text = imzo_qosh(_qisqa_naqsh(post["text"]), post)
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


def tg_answer(cb_id, text=None, alert=False):
    try:
        d = {"callback_query_id": cb_id}
        if text:
            d["text"] = text
        if alert:
            d["show_alert"] = "true"
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


def tg_wait_button(offset, deadline_ts, qabul=(REDO_DATA,)):
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
            if cq.get("data") in qabul:
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


def agent2_write(topic, feedback=None, extra=""):
    facts = "\n".join(f"- {f}" for f in topic.get("key_facts", []))
    fb = (f"\n\nOLDINGI URINISH RAD ETILDI. Sabab:\n{feedback}\n"
          f"Bu xatolarni takrorlama." if feedback else "")
    # Admin Telegram orqali qo'shgan ko'rsatma (sozlamalar.json)
    qosh = sozlama("matn_qoshimcha")
    if qosh:
        extra = (extra + f"\n\n## ADMIN KO'RSATMASI (albatta bajar)\n{qosh}\n")

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

{extra}

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
- Ichida aniq misol, raqam yoki qadam bo'lsin (umumiy gap emas)
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
    qosh = sozlama("rasm_qoshimcha")
    prompt = f"""{IMAGE_STYLE}
{('ADMIN NOTE (follow this too): ' + qosh) if qosh else ''}
---
Generate one image for a Telegram post.

The scene to build: {post.get('image_idea')}

The picture must explain this idea on its own — someone who only looks at
it, without reading anything, should understand what the post is about.
Follow the style rules above exactly. No written words or letters."""
    try:
        p = gem_image(prompt, BUILD / "post.png")
        print(f"[3-agent] Rasm tayyor (nano banana) — "
              f"{p.stat().st_size // 1024} KB")
        return p
    except Exception as e:
        xato = str(e)
        print(f"[3-agent] Gemini rasm chizmadi: {xato[:300]}")
        # "limit: 0" — bu hisobda rasm umuman berilmagan. Keyingi
        # postlarda bekorga urinib, 40 soniya yo'qotmaymiz.
        if "limit: 0" in xato and not _RASM_YOQ[0]:
            _RASM_YOQ[0] = True
            print("[3-agent] Gemini rasm o'chirildi — to'g'ridan-to'g'ri "
                  "bepul zaxira ishlatiladi")
    # Nano banana pullik bo'lgani uchun ishlamasa — bepul zaxira
    try:
        p = bepul_rasm(prompt, BUILD / "post.png")
        print(f"[3-agent] Rasm tayyor (bepul zaxira) — "
              f"{p.stat().st_size // 1024} KB")
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
3. Boshlovchi tushunadimi — izohsiz atama qolmaganmi
4. Faktlar tekshirilganmi — to'qib chiqarilgan raqam yo'qmi
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
    extra = {"message_id": mid, "tur": post.get("source", "ai")}
    if post.get("source") == "stories":
        extra["day"] = post["day"]
        stories_mark_sent(post["day"], mid)
    elif post.get("source") == "kurs":
        extra["dars"] = post["kurs_n"]
        kurs_mark_sent(post["kurs_n"], mid)
    elif post.get("source") == "yangilik":
        yangilik_mark_sent()
    hist_add(post["topic"].get("topic", ""), post.get("title", ""), extra)
    log(f"[6-agent] Kanalga chiqdi — message_id {mid}")


# ══════════════════════════════════════════════════════════════════
#  8-QISM — OQIM
# ══════════════════════════════════════════════════════════════════

def build_post(avoid, label="", src=None):
    src = src or pick_source()
    if src == "hikoya":
        return build_story_post()
    if src == "kurs":
        return build_kurs_post()
    log(f"=== Post yaratilmoqda{' (' + label + ')' if label else ''} ===")
    topic = topic_yangilik(avoid) if src == "yangilik" else agent1_topic(avoid)
    post, feedback = None, None
    for i in range(1, 4):
        post = agent2_write(topic, feedback,
                            YANGILIK_QOIDALARI if src == "yangilik" else "")
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
    post["voice_path"] = agent4_voice(post) if _ha("audio") else None
    post["topic"] = topic
    post["source"] = src
    post["rasm_kerak"] = True
    if src == "yangilik":
        post["manbalar"] = topic.get("manbalar") or []
        post["card_title"] = (strip_tags(post["title"]).strip()
                              if _ha("rasm_sarlavha", "yo'q") else "")
        post["badge"] = sozlama("yangilik_hashtag", "#yangilik")
        # Sarlavha + hashtag + kanal nomi rasm ustiga yoziladi.
        # (Avval bu bosqich tushib qolgan edi — yangilik rasmi bo'sh chiqardi.)
        try:
            post["image_path"] = make_card(
                post["card_title"], post["badge"], post.get("image_path"))
            log("[rasm] yangilik kartochkasi tayyor")
        except Exception as e:
            log(f"[rasm] kartochka yasalmadi: {e}")
    log(f"=== Tayyor: {post['title']} ===")
    return post


def send_preview(post, deadline, round_no):
    """Yangilik postini rasmi bilan adminga taklif qiladi."""
    tg_send_post(TELEGRAM_ADMIN_ID, post)
    label = (f"365 hikoya · {post['day']}-kun" if post.get("source") == "stories"
             else RUBRIC)
    kb = {"inline_keyboard": [
        [{"text": "✅ Chiqar", "callback_data": "news:pub"}],
        [{"text": "\U0001F504 Boshqa mavzu", "callback_data": "news:redo"},
         {"text": "❌ Bugun kerak emas", "callback_data": "news:skip"}],
    ]}
    mavzu = ((post.get("topic") or {}).get("topic") or "").strip()
    rasm = "bor" if post.get("image_path") else "yo'q"
    header = ("\U0001F4F0 <b>Yangilik taklifi</b> · " + label
              + (f" · {round_no}-variant" if round_no > 1 else "")
              + (f"\nMavzu: <i>{html.escape(mavzu[:120])}</i>" if mavzu else "")
              + f"\nRasm: <b>{rasm}</b>"
              + f"\nRejadagi chiqish vaqti: <b>{deadline:%H:%M}</b>\n\n"
              + "Tugmani bosing yoki shunchaki yozing: "
              + "<b>chiqar</b> / <b>boshqa</b> / <b>yo'q</b>.\n"
              + "<i>Javob bo'lmasa — belgilangan vaqtda o'zi chiqadi.</i>")
    ctrl = tg_msg(TELEGRAM_ADMIN_ID, header, markup=kb)
    log(f"[preview] yangilik taklifi yuborildi (deadline {deadline:%H:%M})")
    return ctrl["message_id"]


_BOSHQA_NAQSH = re.compile(
    r"\b(boshqa|qayta|almashtir|yoqmadi|yangisi|boshqasi|redo)\b", re.I)


def yangilik_javobi(offset, deadline_ts, boshlandi=None):
    """Yangilik taklifiga javob kutadi: tugma ham, oddiy gap ham.

    boshlandi — taklif yuborilgan vaqt. Undan OLDIN yozilgan xabarlar
    javob deb hisoblanmaydi (eski gap yangi savolga javob bo'lolmaydi).

    Qaytaradi: ("pub" | "redo" | "skip" | "kutildi", offset)
    """
    boshlandi = boshlandi or time.time()
    while True:
        left = deadline_ts - time.time()
        if left <= 0:
            return "kutildi", offset
        lp = max(1, min(25, int(left)))
        try:
            ups = tg_call("getUpdates",
                          data={"offset": offset, "timeout": lp,
                                "allowed_updates": json.dumps(
                                    ["message", "callback_query"])},
                          timeout=lp + 20)
        except Exception as e:
            xato = str(e).lower()
            if "conflict" in xato or "terminated by other" in xato:
                time.sleep(20)
                continue
            log(f"[yangilik] getUpdates: {e}")
            time.sleep(3)
            continue
        for u in ups or []:
            offset = max(offset, u["update_id"] + 1)
            cq = u.get("callback_query")
            if cq:
                if str((cq.get("from") or {}).get("id")) != str(TELEGRAM_ADMIN_ID):
                    continue
                d = cq.get("data") or ""
                if d.startswith("news:"):
                    tg_answer(cq["id"], "Qabul qilindi")
                    return d.split(":", 1)[1], offset
                continue
            msg = u.get("message") or {}
            if str((msg.get("from") or {}).get("id")) != str(TELEGRAM_ADMIN_ID):
                continue
            # Taklifdan oldin yozilgan gap javob emas
            if (msg.get("date") or 0) < boshlandi - 5:
                log("[yangilik] eski xabar e'tiborga olinmadi")
                continue
            matn = (msg.get("text") or "").strip()
            if not matn:
                continue
            if _BOSHQA_NAQSH.search(matn):
                return "redo", offset
            j = _javob_turi(matn)
            if j == "ha":
                return "pub", offset
            if j == "yoq":
                return "skip", offset


def tasdiq_sora(post, kutish=10):
    """Tasdiqlanmagan post uchun adminda ruxsat so'raydi.

    Tugmani ham, oddiy gapni ham tushunadi ("chiqar", "ha", "yo'q").
    Javob bo'lmasa — kanal jim qolmasligi uchun baribir chiqaradi.
    """
    tur = "dars" if post.get("source") == "kurs" else "hikoya"
    kun = post.get("kurs_n") if tur == "dars" else post.get("day")
    try:
        offset = tg_drain()
        tg_send_post(TELEGRAM_ADMIN_ID, post)
        kb = {"inline_keyboard": [[
            {"text": "\u2705 Chiqaraver", "callback_data": "haftapub"},
            {"text": "\u23ed Bugun o'tkaz", "callback_data": "haftaskip"},
        ]]}
        ctrl = tg_msg(TELEGRAM_ADMIN_ID,
                      f"{kun}-{tur} haftalik ro'yxatda tasdiqlanmagan edi.\n"
                      f"Tugmani bosing yoki shunchaki <b>\"chiqar\"</b> deb "
                      f"yozing.\n"
                      f"{kutish} daqiqa kutaman \u2014 javob bo'lmasa "
                      f"o'zim chiqaraman.", markup=kb)
        oxir_vaqt = time.time() + kutish * 60
        qaror = True
        while time.time() < oxir_vaqt:
            qolgan = max(1, min(30, int(oxir_vaqt - time.time())))
            try:
                ups = tg_call("getUpdates",
                              data={"offset": offset, "timeout": qolgan,
                                    "allowed_updates": json.dumps(
                                        ["message", "callback_query"])},
                              timeout=qolgan + 20)
            except RuntimeError as e:
                log(f"[tasdiq] getUpdates: {e}")
                time.sleep(3)
                continue
            javob = None
            for u in ups or []:
                offset = u["update_id"] + 1
                cq = u.get("callback_query")
                if cq:
                    if str((cq.get("from") or {}).get("id")) != str(TELEGRAM_ADMIN_ID):
                        continue
                    tg_answer(cq["id"], "Qabul qilindi")
                    javob = "yoq" if cq.get("data") == "haftaskip" else "ha"
                    continue
                msg = u.get("message") or {}
                if str((msg.get("from") or {}).get("id")) != str(TELEGRAM_ADMIN_ID):
                    continue
                j = _javob_turi(msg.get("text", ""))
                if j:
                    javob = j
                else:
                    tg_msg(TELEGRAM_ADMIN_ID,
                           f"{kun}-{tur}ni hozir chiqaraymi? "
                           f"<b>chiqar</b> yoki <b>yo'q</b> deb yozing.")
            if javob:
                qaror = (javob == "ha")
                break
        try:
            tg_clear_markup(TELEGRAM_ADMIN_ID, ctrl["message_id"])
        except Exception:
            pass
        if qaror and kun:
            # Ikkinchi marta so'ramaslik uchun tasdiqlangan deb belgilaymiz
            tasdiq_qosh([kun], tur)
            tg_msg(TELEGRAM_ADMIN_ID, f"\u2705 {kun}-{tur} chiqarilmoqda...")
        elif kun:
            tg_msg(TELEGRAM_ADMIN_ID,
                   f"\u23ed {kun}-{tur} bugun chiqmadi. Xohlagan paytingizda "
                   f"\"{kun}-{tur}ni chiqar\" desangiz \u2014 chiqaraman.")
        return qaror
    except Exception as e:
        log(f"[tasdiq] so'rash xatosi: {e} \u2014 post baribir chiqadi")
        return True


def run(force_now=False, preview_only=False):
    src = pick_source()
    log(f"[jadval] slot={SLOT} · tur={src}")

    hh, mm = (int(x) for x in PUBLISH_TIME.split(":"))
    t = now()
    publish_at = t.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if publish_at < t:
        publish_at = t + timedelta(minutes=PREVIEW_LEAD)
        log(f"[jadval] {PUBLISH_TIME} o'tib ketgan — yangi vaqt {publish_at:%H:%M}")
    if force_now:
        # Preview kerak bo'lmagan turlar darhol chiqadi, kutmaydi
        publish_at = (now() if src not in PREVIEW_TURLARI
                      else now() + timedelta(minutes=PREVIEW_LEAD))

    # ── Preview kerak bo'lmagan turlar: hikoya va kurs ──────────
    if src not in PREVIEW_TURLARI:
        post = build_post([])
        if preview_only:
            log("[preview-only] kanalga chiqarilmadi — adminga yuborilmoqda")
            tg_send_post(TELEGRAM_ADMIN_ID, post)
            return

        # Tasdiqlanmagan hikoya bo'lsa — chiqish vaqtidan OLDIN so'raladi,
        # shunda post baribir o'z vaqtida chiqadi, kechikmaydi.
        _tur = post.get("source")
        _raqam = post.get("kurs_n") if _tur == "kurs" else post.get("day")
        _kalit = "dars" if _tur == "kurs" else "hikoya"
        if (_tur in ("stories", "kurs") and _ha("hikoya_tasdiq")
                and _raqam not in tasdiqlar(_kalit)):
            qoldi = (publish_at - now()).total_seconds() / 60
            kutish = int(max(1, min(PREVIEW_LEAD, qoldi - 1)))
            if not tasdiq_sora(post, kutish=kutish):
                log("[tasdiq] admin bugun o'tkazib yuborishni tanladi")
                return

        wait = (publish_at - now()).total_seconds()
        if wait > 0:
            log(f"[jadval] chiqish vaqtigacha {int(wait)} soniya kutilmoqda")
            time.sleep(wait)
        agent6_publish(post)
        return

    # ── Yangilik: preview + qayta qilish tugmasi ────────────────
    preview_at = publish_at - timedelta(minutes=PREVIEW_LEAD)
    if force_now:
        preview_at = now()
    log(f"[jadval] preview {preview_at:%H:%M} · publish {publish_at:%H:%M}")

    offset = tg_drain()
    avoid = []
    post = build_post(avoid)

    wait = (preview_at - now()).total_seconds()
    if wait > 0:
        log(f"[jadval] preview vaqtigacha {int(wait)} soniya kutilmoqda")
        time.sleep(wait)

    round_no, deadline = 1, publish_at
    darhol = False
    while True:
        offset = tg_drain()          # eski xabarlar javob deb o'qilmasin
        yuborildi = time.time()
        ctrl_id = send_preview(post, deadline, round_no)
        javob, offset = yangilik_javobi(offset, deadline.timestamp(),
                                        boshlandi=yuborildi)
        try:
            tg_clear_markup(TELEGRAM_ADMIN_ID, ctrl_id)
        except Exception:
            pass

        if javob == "skip":
            tg_msg(TELEGRAM_ADMIN_ID,
                   "⏭ Yangilik bugun chiqmadi. Xohlagan paytingizda "
                   "<b>\"yangilik tayyorla\"</b> desangiz — yangisini "
                   "tayyorlayman.")
            log("[yangilik] admin bekor qildi")
            return

        if javob == "pub":
            darhol = True
            break

        if javob == "kutildi":       # javob bo'lmadi — reja bo'yicha chiqadi
            break

        # javob == "redo"
        if round_no > MAX_REDO:
            tg_msg(TELEGRAM_ADMIN_ID,
                   f"⚠️ Qayta qilish limiti ({MAX_REDO}) tugadi. "
                   f"Oxirgi variant chiqariladi.")
            break
        avoid.append(post.get("topic", {}).get("topic", ""))
        tg_msg(TELEGRAM_ADMIN_ID,
               "⏳ Boshqa mavzuda yangi post tayyorlanmoqda — 2-3 daqiqa...")
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
    if not darhol:
        kut = (publish_at - now()).total_seconds()
        if kut > 0:
            log(f"[jadval] chiqish vaqtigacha {int(kut)} soniya kutilmoqda")
            time.sleep(kut)
    agent6_publish(post)


# ══════════════════════════════════════════════════════════════════
#  9-QISM — BOT BOSHQARUVI (--listen)
# ══════════════════════════════════════════════════════════════════

YORDAM = """<b>AqlUstaxona — men shu kanalning yordamchisiman</b>

Menga oddiy gap bilan ayting. Hech qanday buyruq yodlash shart emas.
Tushunmasam — o'zim qayta so'rayman.

Masalan shunday deyishingiz mumkin:

"bugungi hikoyani ko'ray"
"12-darsni chiqar"
"yangilik tayyorla"
"kelasi haftada nima chiqadi"
"qaysi kunda turibmiz"

Kanalga chiqib ketgan post yoqmasa:

"bu post xato bo'libdi, o'chir"
"rasmga matn sig'mabdi, boshqasini qo'y"
"oxirgi postni qayta qil"

Doimiy o'zgarish kiritmoqchi bo'lsangiz — shunchaki ayting,
men GitHub'ga saqlab qo'yaman:

"rasm pastidagi yozuvni @aqlustaxona qil"
"ertalabki postni 09:00 da chiqar"
"rasmlar yorqinroq bo'lsin"
"postlar qisqaroq yozilsin"

Post tayyor bo'lgach, sizga ko'rsataman va tugmalar bilan
so'rayman: kanalga chiqaraymi, rasmni qayta chizaymi, yoki bekormi.

Har bir yozganingiz GitHub'dagi kundalikka tushib boradi.
"""

_PENDING = {}      # {token: post}
_TOK = [0]


def _tok():
    _TOK[0] += 1
    return f"p{_TOK[0]}"


def _kb(tok):
    return {"inline_keyboard": [[
        {"text": "✅ Kanalga chiqarish", "callback_data": f"pub:{tok}"},
        {"text": "🔄 Rasmni qayta", "callback_data": f"img:{tok}"},
    ], [
        {"text": "❌ Bekor qilish", "callback_data": f"del:{tok}"},
    ]]}


def _yubor_preview(post, izoh=""):
    tok = _tok()
    _PENDING[tok] = post
    tg_send_post(TELEGRAM_ADMIN_ID, post)
    tg_msg(TELEGRAM_ADMIN_ID,
           (izoh or "Tayyor. Nima qilay?"), markup=_kb(tok))
    return tok


def _tur_nomi(post):
    return {"stories": "hikoya", "kurs": "dars",
            "yangilik": "yangilik"}.get(post.get("source"), "post")


def cmd_holat():
    st = stories_state()
    try:
        h = len(stories_all())
    except Exception:
        h = 0
    try:
        k = len(kurs_all())
    except Exception:
        k = 0
    return (f"<b>Holat</b>\n\n"
            f"Hikoyalar: {st.get('last_sent', 0)}/{h} chiqdi · "
            f"keyingisi {stories_next_day()}-kun\n"
            f"Kurs: {st.get('kurs_oxirgi', 0)}/{k} chiqdi · "
            f"keyingisi {kurs_next()}-dars\n"
            f"Oxirgi kechki post: {st.get('oxirgi_kechki') or '—'}\n\n"
            f"Ertalab {os.getenv('ERTALAB_VAQT', '08:45')} — hikoya\n"
            f"Kechqurun {os.getenv('KECHQURUN_VAQT', '19:45')} — "
            f"kurs va yangilik almashib\n"
            f"Gemini kalitlari: {len(GEMINI_KEYS)} ta"
            + (f" · Grok zaxira: bor ({len(GROK_KEYS)} ta)"
               if GROK_KEYS else " · Grok zaxira: yo'q")
            + (f"\nGitHub: ulangan ({GITHUB_REPO})" if gh_bor()
               else "\nGitHub: ulanmagan")
            + (("\n\n<b>Sozlamalar</b>\n"
                + "\n".join(f"· {k}: {v}"
                             for k, v in sozlamalar().items() if v))
               if sozlamalar() else ""))


BOT_KONTEKST = """
Kanal: @aqlustaxonastartap — startap va biznes haqida o'zbek tilidagi kanal.

Kunlik jadval:
- 08:45 — 365 kunlik startap hikoyalaridan navbatdagisi
- 19:45 — startap kursi va yangilik almashib chiqadi
- yakshanba 20:30 — kelgusi 7 kunlik postlar adminga yuboriladi

Kontent:
- 365 ta tayyor hikoya (stories.json) — matni o'zgarmaydi
- 96 ta startap kursi darsi (kurs.json) — matni o'zgarmaydi, 12 blok:
  g'oya, mijoz, MVP, sotuv, narx, marketing, mahsulot, raqamlar,
  huquq va soliq, jamoa, sarmoya, operatsiya
- yangilik — har safar Google qidiruvi orqali yangi topiladi

Rasmlar: Gemini (Nano Banana) chizadi. Kurs va yangilik postlarida rasm
ustiga sarlavha va hashtag (#startap_kursi yoki #yangilik) yoziladi,
pastida kanal nomi turadi. Hikoyalarda toza rasm.

Audio: ElevenLabs har postga qisqa ovozli izoh yozadi.

Bot nima qila oladi: post tayyorlash va ko'rsatish, kanalga chiqarish,
rasmni qayta chizdirish, haftalik ro'yxat berish, holatni aytish,
KANALDA ALLAQACHON CHIQQAN oxirgi postni o'chirish yoki uni o'chirib
yangi rasm/matn bilan qayta tayyorlash (tahrirlash).

Bot GitHub bilan bog'langan: admin bilan bo'lgan har bir gap repodagi
suhbat/ papkasiga yozib boriladi, va admin aytgan doimiy sozlamalar
sozlamalar.json fayliga saqlanadi. Ya'ni admin Telegram orqali GitHub'ni
o'zgartira oladi — dasturchi kerak emas.

Bot nima qila olmaydi: bot.py kodining o'zini va .github/workflows/
fayllarini o'zgartirish (GitHub bunga ruxsat bermaydi). Faqat ENG OXIRGI
chiqqan postni o'chira/tahrirlay oladi — undan oldingilarini emas.
"""


AMALLAR = ("hikoya", "dars", "yangilik", "haftalik", "holat",
           "yordam", "ochir", "tahrirla", "sozla", "javob")

# ── Suhbat xotirasi ──────────────────────────────────────────────
# Bot oxirgi gaplarni eslab turadi — shuning uchun "ha", "yo'q",
# "o'shani qil" kabi kalta javoblar ham tushunarli bo'ladi.
_TARIX = []            # [(kim, matn)] — oxirgi 10 ta
_KUTILMOQDA = [None]   # {"amal", "raqam", "savol"} — so'ralgan aniqlik


def _tarix_qosh(kim, matn):
    _TARIX.append((kim, (matn or "").strip()[:400]))
    del _TARIX[:-10]


def _tarix_matn():
    if not _TARIX:
        return "(hali suhbat bo'lmagan)"
    return "\n".join(f"{'Admin' if k == 'admin' else 'Sen'}: {m}"
                     for k, m in _TARIX)


def _javob_ber(matn):
    """Adminga javob yozadi va uni suhbat xotirasiga qo'shadi."""
    tg_msg(TELEGRAM_ADMIN_ID, matn)
    _tarix_qosh("bot", strip_tags(matn))


def suhbat(matn):
    """Admin gapini tushunadi.

    Qaytaradi: {"amal", "raqam", "ishonch", "javob", "savol"}
    Ishonch past bo'lsa — "savol" maydonida bot o'z tili bilan
    qayta so'raydigan aniqlashtiruvchi savol keladi.
    """
    st = stories_state()
    holat = (f"Hozir: {stories_next_day()}-hikoya va {kurs_next()}-dars "
             f"navbatda. Oxirgi kechki post: {st.get('oxirgi_kechki') or 'yo`q'}.")
    oxirgi = hist_last()
    oxirgi_post = ("Kanalga hali hech narsa chiqmagan." if not oxirgi else
                   f"Kanalga eng oxiri chiqqan post: \"{oxirgi.get('title', '')}\" "
                   f"({oxirgi.get('tur', '?')}, {oxirgi.get('date', '')}).")
    sozlama_royxat = "\n".join(
        f"  * {k} — {izoh}" + (f"  [hozir: {sozlama(k)}]" if sozlama(k) else "")
        for k, izoh in SOZLAMA_KALITLAR.items())
    kut = _KUTILMOQDA[0]
    kutish = ("Kutilayotgan tasdiq YO'Q." if not kut else
              f"""DIQQAT: sen hozirgina admindan shuni so'rading:
"{kut['savol']}"
Agar admin rozilik bildirsa (ha, mayli, to'g'ri, shu, davom et, bo'ladi,
qil, aynan...) — amal sifatida "{kut['amal']}" ni tanla va ishonch 95 ber.
Agar rad etsa (yo'q, kerakmas, boshqa narsa...) — "javob" tanla.""")

    prompt = f"""{BOT_KONTEKST.strip()}

{holat}
{oxirgi_post}

Sen shu kanalning boshqaruv yordamchisisan. Admin bilan oddiy odam kabi
suhbatlashasan — u hech qanday buyruq yodlamaydi, o'z so'zlari bilan
gapiradi, ba'zan qisqa va noaniq yozadi, imlo xatolari bilan yozadi.

SUHBAT TARIXI (oxirgi gaplar):
{_tarix_matn()}

{kutish}

ADMINNING YANGI XABARI:
---
{matn}
---

Vazifang: uning nimani xohlayotganini tushun.

AMALLAR:
- "hikoya" — 365 hikoyadan birini tayyorlab ko'rsatish (raqam aytilsa o'shani)
- "dars" — kurs darsini tayyorlab ko'rsatish (raqam aytilsa o'shani)
- "yangilik" — yangi yangilik posti tayyorlash
- "haftalik" — kelgusi 7 kunda nima chiqishi ro'yxati
- "holat" — qaysi hikoya/darsda turganimiz
- "yordam" — nima qila olishimni tushuntirish
- "ochir" — kanalga chiqib ketgan oxirgi postni o'chirish
- "tahrirla" — kanalga chiqib ketgan oxirgi postni o'chirib, yangi rasm
  bilan qaytadan tayyorlash (rasm sig'masa, chiroyli chiqmasa, yoqmasa)
- "sozla" — doimiy sozlamani o'zgartirish va GitHub'ga saqlash.
  Bu "bundan keyin doim shunday bo'lsin" degan gaplar uchun.
  "kalit" va "qiymat" maydonlarini ham to'ldir. Mavjud kalitlar:
{sozlama_royxat}
- "javob" — hech narsa qilish shart emas, shunchaki gapga javob berish

ISHONCH (0-100) — nima demoqchi ekanini qanchalik aniq tushunding:
- 80-100: aniq tushundim, darhol bajarsa bo'ladi
- 40-79: taxmin qilyapman, lekin adashishim mumkin
- 0-39: umuman tushunmadim

QOIDALAR:
- Admin postdagi rasm yoki ko'rinishdan norozi bo'lsa ("sig'mayapti",
  "chiroyli emas", "boshqasini qo'y", "xato chiqibdi") — bu KANALGA
  ALLAQACHON CHIQQAN oxirgi post haqida. Faqat rasm muammosi bo'lsa
  "tahrirla", butunlay kerakmas bo'lsa "ochir"
- ISHONCH 80 dan past bo'lsa — "savol" maydoniga o'z so'zlaring bilan,
  oddiy tilda aniqlashtiruvchi savol yoz. Masalan:
  "Kanaldagi oxirgi postning rasmini qayta chizib berayinmi?"
  Savol bitta bo'lsin va unga "ha" deb javob berish oson bo'lsin
- HECH QACHON "tushunmadim", "buyruq bilan urinib ko'ring" dema.
  Tushunmasang — o'z so'zlaring bilan qayta so'ra
- Buyruq nomlarini (/hikoya, /dars) adminga aytma — u ularni bilishi
  shart emas, oddiy gap bilan gaplashaveradi
- O'zbek tilida, lotin yozuvida, do'stona, qisqa va aniq yoz
- HTML: faqat <b> va <i>. Markdown ishlatma

Javobni FAQAT shu JSON ko'rinishida ber:
{{"amal": "{'|'.join(AMALLAR)}",
 "raqam": null yoki son,
 "ishonch": 0 dan 100 gacha son,
 "javob": "amal 'javob' bo'lsa — javob matni; aks holda qisqa tasdiq",
 "savol": "ishonch 80 dan past bo'lsa — aniqlashtiruvchi savol, aks holda bo'sh",
 "kalit": "amal 'sozla' bo'lsa — sozlama kaliti, aks holda bo'sh",
 "qiymat": "amal 'sozla' bo'lsa — yangi qiymat, aks holda bo'sh"}}"""
    # Bitta urinish — tez javob uchun. Xato bo'lsa oddiy_tushun ishlaydi.
    d = gem_json(prompt, temperature=0.3, attempts=1)
    amal = (d.get("amal") or "javob").strip().lower()
    if amal not in AMALLAR:
        amal = "javob"
    try:
        ishonch = int(float(d.get("ishonch", 0)))
    except (TypeError, ValueError):
        ishonch = 0
    try:
        raqam = int(d["raqam"]) if d.get("raqam") not in (None, "") else None
    except (TypeError, ValueError):
        raqam = None
    kalit = (d.get("kalit") or "").strip().lower()
    if kalit not in SOZLAMA_KALITLAR:
        kalit = ""
    return {"amal": amal, "raqam": raqam, "ishonch": ishonch,
            "javob": (d.get("javob") or "").strip(),
            "savol": (d.get("savol") or "").strip(),
            "kalit": kalit, "qiymat": (d.get("qiymat") or "").strip()}


# Sun'iy intellekt umuman ishlamay qolganda ishlatiladigan oddiy tushunish.
# Bu ham "tushunmadim" demaydi — taxmin qilib, tasdiq so'raydi.
_KALIT_SOZLAR = [
    (("o'chir", "ochir", "uchir", "olib tashla", "yo'q qil", "yoq qil",
      "kerakmas", "kerak emas"), "ochir"),
    (("tahrir", "qayta qil", "qaytadan", "boshqa rasm", "sig'ma", "sigma",
      "almashtir", "o'zgartir", "ozgartir", "tuzat", "chiroyli emas"), "tahrirla"),
    (("hikoya", "hikoyani", "story", "chiqar", "tasdiq"), "hikoya"),
    (("dars", "kurs"), "dars"),
    (("yangilik", "xabar", "news"), "yangilik"),
    (("haftalik", "hafta", "kelgusi", "kelasi"), "haftalik"),
    (("holat", "qayerda", "qaysi kun", "nechanchi", "statistika"), "holat"),
    (("yordam", "nima qila", "yordamchi", "help", "start"), "yordam"),
]

_TASDIQ_SOZLAR = ("ha", "xa", "mayli", "bo'ladi", "boladi", "to'g'ri", "togri",
                  "shu", "aynan", "qil", "davom", "ok", "okay", "zo'r", "zor")
_RAD_SOZLAR = ("yo'q", "yoq", "kerakmas", "kerak emas", "bekor", "shart emas")

# Rozilik / rad javobini SUN'IY INTELLEKTSIZ tanish uchun.
# API ishlamay qolsa ham bot "ha" va "yo'q" ni doim tushunadi.
_YOQ_NAQSH = re.compile(
    r"\b(yo'?q|kerakmas|kerak\s*emas|bekor|shart\s*emas|to'?xta\w*|"
    r"o'?tkaz\w*|chiqarma\w*|yuborma\w*|qilma\w*|keyinroq|hozirmas)\b")
_HA_NAQSH = re.compile(
    r"\b(ha+|xa+|mayli|bo'?ladi|bo'?pti|to'?g'?ri|togri|aynan|davom|ok|okey|"
    r"okay|zo'?r|zor|xop|hop|tasdiq\w*|chiqar(?!ma)\w*|yubor(?!ma)\w*|"
    r"qilaver\w*|qilsin|bo'?laver\w*)\b")


def _javob_turi(matn):
    """Adminning gapi rozilikmi yoki radmi. Bilmasa None.

    Bu funksiya hech qanday API'ga bormaydi — shuning uchun Gemini
    ham, Grok ham o'lgan bo'lsa ham ishlaydi.
    """
    m = (matn or "").lower().strip()
    if not m:
        return None
    if _YOQ_NAQSH.search(m):
        return "yoq"
    if _HA_NAQSH.search(m):
        return "ha"
    return None


# Juda aniq, qisqa so'rovlar. Bularga Gemini kerak emas — darhol bajariladi.
_TEZ_NAQSHLAR = [
    (r"^(\d{1,3})\s*[-\s]?\s*(dars|darsni|darsi)\b.{0,15}$", "dars", 1),
    (r"^(dars|darsni|darsi)\s*(\d{1,3})\b.{0,15}$", "dars", 2),
    (r"^(\d{1,3})\s*[-\s]?\s*(hikoya|hikoyani|hikoyasi)\b.{0,15}$", "hikoya", 1),
    (r"^(hikoya|hikoyani|hikoyasi)\s*(\d{1,3})\b.{0,15}$", "hikoya", 2),
    (r"^(hikoya|hikoyani|bugungi hikoya)\s*(chiqar|korsat|ko'rsat|ber)?\s*$",
     "hikoya", None),
    (r"^(dars|darsni|keyingi dars)\s*(chiqar|korsat|ko'rsat|ber)?\s*$",
     "dars", None),
    (r"^(yangilik|yangilikni|yangiliklar)\s*(chiqar|korsat|ko'rsat|tayyorla|ber)?\s*$",
     "yangilik", None),
    (r"^(holat|holatim|qayerdamiz|qaysi kunda|statistika)\s*\??$", "holat", None),
    (r"^(haftalik|kelasi hafta|kelgusi hafta)\s*\??$", "haftalik", None),
    (r"^(yordam|help|start|nima qila olasan)\s*\??$", "yordam", None),
]


def tez_tushun(matn):
    """Aniq va qisqa so'rovni Gemini'siz tanib oladi. Topmasa None."""
    m = (matn or "").strip().lower().rstrip(".!?")
    if len(m) > 40:
        return None
    for naqsh, amal, guruh in _TEZ_NAQSHLAR:
        mo = re.match(naqsh, m)
        if not mo:
            continue
        raqam = None
        if guruh:
            try:
                raqam = int(mo.group(guruh))
            except (ValueError, IndexError):
                raqam = None
        return {"amal": amal, "raqam": raqam, "ishonch": 100,
                "javob": "", "savol": "", "kalit": "", "qiymat": ""}
    return None


def oddiy_tushun(matn):
    """Gemini ham, Grok ham ishlamaganda — kalit so'zlar bo'yicha taxmin."""
    m = " " + (matn or "").lower().strip() + " "
    kut = _KUTILMOQDA[0]
    if kut:
        if any(s in m for s in _TASDIQ_SOZLAR):
            return {"amal": kut["amal"], "raqam": kut.get("raqam"),
                    "ishonch": 90, "javob": "Bo'ldi, qilaman.", "savol": ""}
        if any(s in m for s in _RAD_SOZLAR):
            return {"amal": "javob", "raqam": None, "ishonch": 90,
                    "javob": "Tushundim, tegmadim.", "savol": ""}
    raqam = None
    mr = re.search(r"\b(\d{1,3})\b", m)
    if mr:
        raqam = int(mr.group(1))
    for sozlar, amal in _KALIT_SOZLAR:
        if any(s in m for s in sozlar):
            nomi = {"hikoya": "hikoyani ko'rsatish",
                    "dars": "kurs darsini ko'rsatish",
                    "yangilik": "yangilik posti tayyorlash",
                    "haftalik": "kelgusi hafta ro'yxatini berish",
                    "holat": "hozirgi holatni aytish",
                    "ochir": "kanaldagi oxirgi postni o'chirish",
                    "tahrirla": "oxirgi postni qayta tayyorlash",
                    "yordam": "nima qila olishimni aytish"}[amal]
            return {"amal": amal, "raqam": raqam, "ishonch": 55, "javob": "",
                    "savol": f"Sizni to'g'ri tushundimmi — {nomi} kerakmi?"}
    return {"amal": "javob", "raqam": None, "ishonch": 100, "savol": "",
            "javob": "Sizni eshitdim, lekin nima qilishimni aniq "
                     "tushunmadim. Shulardan birini ayting:\n"
                     "\u2022 <b>chiqar</b> \u2014 navbatdagi hikoyani chiqaraman\n"
                     "\u2022 <b>dars</b> yoki <b>yangilik</b> \u2014 o'sha postni tayyorlayman\n"
                     "\u2022 <b>o'chir</b> \u2014 kanaldagi oxirgi postni o'chiraman\n"
                     "\u2022 <b>haftalik</b> \u2014 kelgusi 7 kunni ko'rsataman"}


def _tasdiq_ochir(entry, tahrir):
    """Oxirgi postni o'chirish/tahrirlashdan oldin bir tugma bilan
    tasdiqlash so'raydi — tasodifan noto'g'ri tushunilsa ham hech narsa
    darhol o'chib ketmaydi."""
    tok = _tok()
    _PENDING[tok] = {"_ochir_entry": entry, "_tahrir": tahrir}
    sarlavha = entry.get("title") or entry.get("topic") or "(nomsiz)"
    amal_matni = ("o'chirib, qaytadan tayyorlab berayinmi" if tahrir
                  else "kanaldan o'chirib tashlayinmi")
    kb = {"inline_keyboard": [[
        {"text": "✅ Ha", "callback_data": f"ochirha:{tok}"},
        {"text": "❌ Yo'q", "callback_data": f"ochiryoq:{tok}"},
    ]]}
    tg_msg(TELEGRAM_ADMIN_ID,
           f"Kanaldagi oxirgi post:\n<b>{sarlavha}</b>\n"
           f"({entry.get('date', '')})\n\nShuni {amal_matni}?", markup=kb)


def _tasdiq_sozla(kalit, qiymat):
    """Sozlamani o'zgartirishdan oldin tasdiq so'raydi."""
    tok = _tok()
    _PENDING[tok] = {"_sozla": (kalit, qiymat)}
    eski = sozlama(kalit) or "(belgilanmagan)"
    kb = {"inline_keyboard": [[
        {"text": "\u2705 Ha, saqla", "callback_data": f"sozha:{tok}"},
        {"text": "\u274c Yo'q", "callback_data": f"sozyoq:{tok}"},
    ]]}
    tg_msg(TELEGRAM_ADMIN_ID,
           f"<b>{SOZLAMA_KALITLAR[kalit]}</b>\n\n"
           f"Hozir: <i>{html.escape(eski)}</i>\n"
           f"Yangi: <b>{html.escape(qiymat)}</b>\n\n"
           f"GitHub'ga saqlab qo'yayinmi? Bundan keyin doim shunday bo'ladi.",
           markup=kb)


def buyruqni_bajar(buyruq, raqam, kalit="", qiymat=""):
    if buyruq == "sozla":
        if not kalit or not qiymat:
            tg_msg(TELEGRAM_ADMIN_ID,
                   "Nimani o'zgartirishni aniq aytolmadim. Masalan shunday "
                   "deng: \"rasm pastidagi yozuvni @aqlustaxona qil\" yoki "
                   "\"ertalabki postni 09:00 da chiqar\".")
            return
        _tasdiq_sozla(kalit, qiymat)
        return
    if buyruq == "yordam":
        tg_msg(TELEGRAM_ADMIN_ID, YORDAM)
        return
    if buyruq == "holat":
        tg_msg(TELEGRAM_ADMIN_ID, cmd_holat())
        return
    if buyruq == "haftalik":
        tg_msg(TELEGRAM_ADMIN_ID, "⏳ Tayyorlanmoqda...")
        weekly_preview()
        return
    if buyruq in ("ochir", "tahrirla"):
        entry = hist_last()
        if not entry:
            tg_msg(TELEGRAM_ADMIN_ID, "Hali kanalga chiqqan post yo'q.")
            return
        _tasdiq_ochir(entry, tahrir=(buyruq == "tahrirla"))
        return
    if buyruq in ("hikoya", "dars", "yangilik"):
        tg_msg(TELEGRAM_ADMIN_ID, "⏳ Post tayyorlanmoqda — 1-2 daqiqa...")
        if buyruq == "hikoya":
            post = build_story_post(int(raqam) if raqam else None)
        elif buyruq == "dars":
            post = build_kurs_post(int(raqam) if raqam else None)
        else:
            post = build_post([], "")
        _yubor_preview(post, f"Yuqorida — {_tur_nomi(post)}. Nima qilay?")
        return
    tg_msg(TELEGRAM_ADMIN_ID, YORDAM)


def matnni_ishla(matn):
    m = (matn or "").strip()
    if not m:
        return
    # Boshidagi "/" ni olib tashlaymiz — bot uchun buyruq ham oddiy gap.
    if m.startswith("/"):
        m = m[1:].replace("@", " ").strip()
        m = {"start": "salom", "help": "nima qila olasan"}.get(m.lower(), m)
    tg_typing(TELEGRAM_ADMIN_ID)
    _tarix_qosh("admin", m)
    kut = _KUTILMOQDA[0]
    # Adminning har bir gapi GitHub kundaligiga tushadi (fonda, kutmasdan)
    gh_havola = gh_suhbat_fonda("Admin", m)

    # 1) Savol kutilayotgan edi — "ha / yo'q / chiqar" ni API'siz tushunamiz.
    #    Shu tufayli Gemini o'lgan bo'lsa ham tasdiq doim ishlaydi.
    d = None
    if kut:
        tur = _javob_turi(m)
        if tur == "yoq":
            _KUTILMOQDA[0] = None
            _javob_ber("Tushundim, tegmadim.")
            return
        if tur == "ha":
            log("[listen] tasdiq (API'siz)")
            d = {"amal": kut["amal"], "raqam": kut.get("raqam"),
                 "ishonch": 100, "javob": "", "savol": "",
                 "kalit": kut.get("kalit", ""),
                 "qiymat": kut.get("qiymat", "")}
    else:
        d = tez_tushun(m)
    if d:
        log(f"[listen] tezkor: {d['amal']}")
    else:
        try:
            d = suhbat(m)
        except Exception as e:
            # Sun'iy intellekt ishlamadi — lekin bot baribir javob beradi.
            log(f"[listen] suhbat xatosi: {e}")
            d = oddiy_tushun(m)

    amal, raqam = d["amal"], d.get("raqam")
    ishonch, javob, savol = d["ishonch"], d.get("javob", ""), d.get("savol", "")

    izoh = ("\n\n<i>\U0001F4DD GitHub kundaligiga yozib qo'ydim.</i>"
            if gh_havola else "")
    if amal == "javob":
        _KUTILMOQDA[0] = None
        _javob_ber((javob or "Eshitdim sizni. Nima qilay?") + izoh)
        return

    # Aniq tushunmadim — o'z so'zim bilan qayta so'rayman.
    if ishonch < 80 and savol:
        _KUTILMOQDA[0] = {"amal": amal, "raqam": raqam, "savol": savol,
                          "kalit": d.get("kalit", ""),
                          "qiymat": d.get("qiymat", "")}
        _javob_ber(savol)
        return

    # Tasdiq kutilayotgan edi — yetishmagan ma'lumotni o'shandan olamiz
    kalit, qiymat = d.get("kalit", ""), d.get("qiymat", "")
    if kut and kut.get("amal") == amal:
        kalit = kalit or kut.get("kalit", "")
        qiymat = qiymat or kut.get("qiymat", "")
        if raqam is None:
            raqam = kut.get("raqam")
    _KUTILMOQDA[0] = None
    if javob:
        _javob_ber(javob)
    try:
        buyruqni_bajar(amal, raqam, kalit, qiymat)
    except Exception as e:
        log(f"[listen] xato: {e}")
        _javob_ber(f"Buni qila olmadim: <code>{str(e)[:300]}</code>\n"
                   f"Boshqacha aytib ko'ring yoki keyinroq urinib ko'raman.")


def tugmani_ishla(cq):
    data = cq.get("data", "")
    amal, _, tok = data.partition(":")
    mid = (cq.get("message") or {}).get("message_id")

    # ── Haftalik 7 kunlik tasdiq ────────────────────────────────
    if amal in ("hafta", "haftayoq"):
        if mid:
            tg_clear_markup(TELEGRAM_ADMIN_ID, mid)
        try:
            h, _, d = tok.partition(":")
            h1, h2 = (int(x) for x in h.split("-"))
            d1, d2 = (int(x) for x in d.split("-")) if d else (0, 0)
        except ValueError:
            tg_answer(cq["id"], "Bu so'rov eskirgan.")
            return
        if amal == "haftayoq":
            tg_answer(cq["id"], "Aytavering")
            _KUTILMOQDA[0] = {"amal": "hikoya", "savol": "haftalik tuzatish"}
            _javob_ber("Qaysi birini va nimasini o'zgartiray? Masalan: "
                       "\"3-hikoyaning sarlavhasi uzun\" yoki "
                       "\"5-darsni boshqasi bilan almashtir\".")
            return
        tg_answer(cq["id"], "Tasdiqlandi")
        qismlar, havola = [], ""
        if h1:
            havola = tasdiq_qosh(range(h1, h2 + 1), "hikoya") or havola
            qismlar.append(f"{h1}\u2013{h2}-hikoya")
        if d1:
            havola = tasdiq_qosh(range(d1, d2 + 1), "dars") or havola
            qismlar.append(f"{d1}\u2013{d2}-dars")
        qosh = (f"\nGitHub'ga yozildi: <a href=\"{havola}\">commit</a>"
                if havola else "")
        _javob_ber(f"\u2705 Tasdiqlandi: {', '.join(qismlar)}.\n"
                   f"Hafta davomida boshqa so'ramayman \u2014 hammasi "
                   f"kelishganimizdek chiqadi.{qosh}")
        return

    post = _PENDING.get(tok)
    if not post:
        tg_answer(cq["id"], "Bu so'rov eskirgan.")
        return

    if isinstance(post, dict) and "_sozla" in post:
        _PENDING.pop(tok, None)
        if mid:
            tg_clear_markup(TELEGRAM_ADMIN_ID, mid)
        if amal == "sozyoq":
            tg_answer(cq["id"], "Bekor qilindi.")
            _javob_ber("Mayli, o'zgartirmadim.")
            return
        tg_answer(cq["id"], "Saqlanmoqda...")
        kalit, qiymat = post["_sozla"]
        try:
            havola = gh_sozlama_yoz(kalit, qiymat)
        except Exception as e:
            _javob_ber(f"GitHub'ga saqlay olmadim: <code>{str(e)[:300]}</code>")
            return
        if havola:
            _javob_ber(f"\u2705 Saqladim. Bundan keyin doim shunday bo'ladi.\n"
                       f"GitHub'ga yozildi: <a href=\"{havola}\">commit</a>")
        else:
            _javob_ber("\u2705 Saqladim. (GitHub ulanmagan — faqat shu "
                       "seansda amal qiladi.)")
        return

    if isinstance(post, dict) and "_ochir_entry" in post:
        _PENDING.pop(tok, None)
        if mid:
            tg_clear_markup(TELEGRAM_ADMIN_ID, mid)
        if amal == "ochiryoq":
            tg_answer(cq["id"], "Bekor qilindi.")
            tg_msg(TELEGRAM_ADMIN_ID, "Hech narsa o'zgarmadi.")
            return
        tg_answer(cq["id"], "O'chirilmoqda...")
        entry = post["_ochir_entry"]
        tahrir = post["_tahrir"]
        try:
            postni_ochir(entry)
        except Exception as e:
            tg_msg(TELEGRAM_ADMIN_ID, f"❌ O'chirishda xato: {str(e)[:300]}")
            return
        if not tahrir:
            tg_msg(TELEGRAM_ADMIN_ID, "✅ Kanaldan o'chirildi.")
            return
        tg_msg(TELEGRAM_ADMIN_ID, "⏳ Yangisi tayyorlanmoqda — 1-2 daqiqa...")
        try:
            tur = entry.get("tur")
            if tur == "stories" and entry.get("day"):
                yangi = build_story_post(entry["day"])
            elif tur == "kurs" and entry.get("dars"):
                yangi = build_kurs_post(entry["dars"])
            else:
                yangi = build_post([])
            _yubor_preview(yangi, "✅ Eskisi o'chirildi. Yangi variant tayyor — "
                                  "nima qilay?")
        except Exception as e:
            tg_msg(TELEGRAM_ADMIN_ID, f"❌ Yangisi tayyorlanmadi: {str(e)[:400]}")
        return

    if amal == "del":
        _PENDING.pop(tok, None)
        tg_answer(cq["id"], "Bekor qilindi.")
        if mid:
            tg_clear_markup(TELEGRAM_ADMIN_ID, mid)
        tg_msg(TELEGRAM_ADMIN_ID, "❌ Bekor qilindi, kanalga chiqmadi.")
        return
    if amal == "img":
        tg_answer(cq["id"], "Rasm qayta chizilmoqda...")
        if mid:
            tg_clear_markup(TELEGRAM_ADMIN_ID, mid)
        try:
            _attach_media(post)
            _yubor_preview(post, "Yangi rasm. Nima qilay?")
        except Exception as e:
            tg_msg(TELEGRAM_ADMIN_ID, f"❌ Rasm chizilmadi: {str(e)[:300]}")
        return
    if amal == "pub":
        tg_answer(cq["id"], "Kanalga chiqarilmoqda...")
        if mid:
            tg_clear_markup(TELEGRAM_ADMIN_ID, mid)
        try:
            agent6_publish(post)
            _PENDING.pop(tok, None)
            tg_msg(TELEGRAM_ADMIN_ID, "✅ Kanalga chiqdi.")
        except Exception as e:
            tg_msg(TELEGRAM_ADMIN_ID, f"❌ Chiqmadi: {str(e)[:400]}")


# ══════════════════════════════════════════════════════════════════
#  MEHMONLAR — bepul PDF va taklif tizimi
# ══════════════════════════════════════════════════════════════════

def _kanal_havola():
    k = TELEGRAM_CHANNEL.lstrip("@")
    return f"https://t.me/{k}"


def _obuna_tugma(payload=""):
    tugma = [[{"text": "📢 Kanalga obuna bo'lish", "url": _kanal_havola()}],
             [{"text": "✅ Obuna bo'ldim", "callback_data": f"tekshir:{payload}"}]]
    return {"inline_keyboard": tugma}


def mehmon_start(uid, ism, payload):
    """/start bosgan mehmon. payload = 'r123456' bo'lishi mumkin."""
    d = obunachilar()
    yangi = str(uid) not in d
    if yangi:
        kelgan = ""
        if payload.startswith("r"):
            r = payload[1:].strip()
            if r.isdigit() and r != str(uid):
                kelgan = r
        d[str(uid)] = {"ism": ism, "kelgan": kelgan,
                       "sana": now().strftime("%Y-%m-%d %H:%M"),
                       "olgan": False}
        _OBUNA_KIR[0] = True

    if not kanalga_obunami(uid):
        tg_msg(uid,
               "🎁 <b>Bepul qo'llanma: «Startapni noldan boshlash»</b>\n\n"
               "30 kunlik amaliy cheklist — har kuni bitta aniq vazifa, "
               "g'oyani tekshirishdan birinchi to'lovgacha. Ichida 3 ta "
               "tayyor shablon ham bor.\n\n"
               "Olish uchun avval kanalga obuna bo'ling — keyin "
               "«Obuna bo'ldim» tugmasini bosing.",
               markup=_obuna_tugma(payload))
        return
    mehmon_sovga(uid)


def mehmon_sovga(uid):
    """Obuna tasdiqlangan — PDF va taklif havolasini beradi."""
    d = obunachilar()
    yozuv = d.setdefault(str(uid), {"ism": "", "kelgan": "",
                                    "sana": now().strftime("%Y-%m-%d %H:%M"),
                                    "olgan": False})
    if not SOVGA_FILE.exists():
        tg_msg(uid, "Qo'llanma hozir tayyorlanmoqda — birozdan keyin urinib "
                    "ko'ring. Kanalda esa postlar allaqachon chiqyapti 👇\n"
                    + _kanal_havola())
        return
    try:
        tg_document(
            uid, SOVGA_FILE,
            caption="🎁 <b>Startapni noldan boshlash</b>\n"
                    "30 kunlik cheklist · 3 ta shablon\n\n"
                    "Birinchi sahifadan boshlang va kuniga bitta vazifani "
                    "bajaring. Shoshilmang — izchillik muhim.")
    except Exception as e:
        log(f"[mehmon] PDF yuborilmadi: {e}")
        tg_msg(uid, "Qo'llanmani yuborishda muammo bo'ldi. "
                    "Birozdan keyin /start bosing.")
        return

    if not yozuv.get("olgan"):
        yozuv["olgan"] = True
        _OBUNA_KIR[0] = True
        # Adminga xabar — yangi odam qo'shildi
        try:
            kim = yozuv.get("kelgan")
            qosh = ""
            if kim:
                qosh = f"\nKim orqali: <code>{kim}</code>"
            tg_msg(TELEGRAM_ADMIN_ID,
                   f"👤 Yangi odam qo'llanmani oldi: "
                   f"{html.escape(yozuv.get('ism') or str(uid))}{qosh}\n"
                   f"Jami: <b>{len(d)}</b> ta")
        except Exception:
            pass

    hav = taklif_havola(uid)
    if hav:
        tg_msg(uid,
               "📣 <b>Do'stingizga ham yuboring</b>\n\n"
               "Quyidagi havola — sizniki. Kim shu havola orqali kelsa, "
               "sizning hisobingizga yoziladi.\n\n"
               f"<code>{hav}</code>\n\n"
               "Hozircha siz olib kelgan odamlar: "
               f"<b>{taklif_soni(uid)}</b> ta\n\n"
               "Reyting: /top  ·  Havolangiz: /taklif",
               markup={"inline_keyboard": [[
                   {"text": "📤 Do'stga yuborish",
                    "url": "https://t.me/share/url?url=" + hav
                           + "&text=" + "Startap bo'yicha bepul 30 kunlik "
                                        "qo'llanma - menga foydali bo'ldi"}]]})


def mehmon_top(uid):
    d = obunachilar()
    hisob = {}
    for v in d.values():
        k = str(v.get("kelgan") or "")
        if k:
            hisob[k] = hisob.get(k, 0) + 1
    if not hisob:
        tg_msg(uid, "Hozircha reyting bo'sh — birinchi bo'lish imkoniyati "
                    "sizda 🙂\nHavolangiz: /taklif")
        return
    top = sorted(hisob.items(), key=lambda x: -x[1])[:10]
    qator = []
    for i, (k, n) in enumerate(top, 1):
        ism = (d.get(k) or {}).get("ism") or f"#{k[-4:]}"
        belgi = "🥇🥈🥉"[i - 1] if i <= 3 else f"{i}."
        meniki = " ← siz" if str(k) == str(uid) else ""
        qator.append(f"{belgi} {html.escape(ism)} — <b>{n}</b>{meniki}")
    tg_msg(uid, "🏆 <b>Eng ko'p odam olib kelganlar</b>\n\n"
                + "\n".join(qator)
                + f"\n\nSizniki: <b>{taklif_soni(uid)}</b> ta · /taklif")


def mehmon_ishla(msg):
    """Admin bo'lmagan odamdan kelgan xabar."""
    frm = msg.get("from") or {}
    uid = frm.get("id")
    if not uid:
        return
    ism = (frm.get("first_name") or "").strip() or frm.get("username") or ""
    matn = (msg.get("text") or "").strip()
    past = matn.lower()

    if past.startswith("/start"):
        payload = matn[6:].strip()
        mehmon_start(uid, ism, payload)
        return
    if past.startswith("/taklif") or past.startswith("/havola"):
        hav = taklif_havola(uid)
        tg_msg(uid, ("📣 <b>Sizning havolangiz</b>\n\n"
                     f"<code>{hav}</code>\n\n"
                     f"Olib kelganlaringiz: <b>{taklif_soni(uid)}</b> ta")
               if hav else "Havola hozir tayyor emas, keyinroq urinib ko'ring.")
        return
    if past.startswith("/top") or past.startswith("/reyting"):
        mehmon_top(uid)
        return
    if past.startswith("/kitob") or past.startswith("/pdf") \
            or past.startswith("/qollanma"):
        if kanalga_obunami(uid):
            mehmon_sovga(uid)
        else:
            tg_msg(uid, "Avval kanalga obuna bo'ling 👇",
                   markup=_obuna_tugma())
        return

    tg_msg(uid,
           "Salom! Men <b>AqlUstaxona</b> kanalining botiman.\n\n"
           "🎁 /start — bepul 30 kunlik qo'llanmani olish\n"
           "📣 /taklif — do'stlaringizni chaqirish havolasi\n"
           "🏆 /top — eng faol odamlar reytingi\n\n"
           "Savolingiz bo'lsa: @Maqsadbek_Bobojonov\n"
           f"Kanal: {_kanal_havola()}")


def mehmon_tugma(cq):
    """Mehmon bosgan tugma."""
    uid = (cq.get("from") or {}).get("id")
    data = cq.get("data") or ""
    if not data.startswith("tekshir:"):
        tg_answer(cq["id"], "")
        return
    payload = data.split(":", 1)[1]
    if kanalga_obunami(uid):
        tg_answer(cq["id"], "Rahmat! Qo'llanma yuborilmoqda...")
        ism = (cq.get("from") or {}).get("first_name") or ""
        d = obunachilar()
        if str(uid) not in d:
            kelgan = payload[1:] if payload.startswith("r") else ""
            d[str(uid)] = {"ism": ism,
                           "kelgan": kelgan if kelgan.isdigit() else "",
                           "sana": now().strftime("%Y-%m-%d %H:%M"),
                           "olgan": False}
            _OBUNA_KIR[0] = True
        mehmon_sovga(uid)
    else:
        tg_answer(cq["id"],
                  "Hali obuna ko'rinmayapti. Kanalga kiring va qayta bosing.",
                  alert=True)


def pin_post():
    """pinned.txt faylini kanalga chiqarib, qadab qo'yadi (pin)."""
    fayl = ROOT / "pinned.txt"
    if not fayl.exists():
        raise RuntimeError("pinned.txt topilmadi")
    matn = fayl.read_text(encoding="utf-8").strip()
    if not matn:
        raise RuntimeError("pinned.txt bo'sh")
    r = tg_call("sendMessage", data={
        "chat_id": TELEGRAM_CHANNEL, "text": matn,
        "parse_mode": "HTML", "disable_web_page_preview": "true"})
    mid = r.get("message_id")
    log(f"[pin] kanalga chiqdi — message_id={mid}")
    try:
        tg_call("pinChatMessage", data={
            "chat_id": TELEGRAM_CHANNEL, "message_id": mid,
            "disable_notification": "true"})
        log("[pin] qadab qo'yildi")
        holat = "chiqdi va qadab qo'yildi"
    except Exception as e:
        log(f"[pin] qadash ishlamadi: {e}")
        holat = ("chiqdi, lekin qadash ishlamadi — botga kanalda "
                 f"«Pin messages» huquqini bering.\n<code>{str(e)[:200]}</code>")
    tg_msg(TELEGRAM_ADMIN_ID, f"📌 Tanishtiruv posti {holat}.")
    return mid


def listen(minutes=340):
    """Botni tinglash rejimi. Admin buyruqlarini qabul qiladi."""
    log(f"[listen] boshlandi — {minutes} daqiqa")
    tg_msg(TELEGRAM_ADMIN_ID,
           "🟢 <b>Boshqaruv yoqildi</b>\n\nBuyruq yozing yoki /yordam.")
    offset = tg_drain()
    tugash = time.time() + minutes * 60
    while time.time() < tugash:
        try:
            ups = tg_call("getUpdates",
                          data={"offset": offset, "timeout": 45,
                                "allowed_updates": json.dumps(
                                    ["message", "callback_query"])},
                          timeout=60)
        except Exception as e:
            xato = str(e).lower()
            # Kunlik post workflow'i ham shu botni tinglayotgan bo'lsa —
            # Telegram 409 beradi. Bir-birimizdan xabar o'g'irlamaslik
            # uchun chetga chiqib turamiz, xabarlar yo'qolmaydi.
            if "conflict" in xato or "terminated by other" in xato:
                log("[listen] boshqa jarayon tinglayapti — 90 soniya kutaman")
                time.sleep(90)
                continue
            log(f"[listen] getUpdates: {e}")
            time.sleep(5)
            continue
        for u in ups or []:
            offset = u["update_id"] + 1
            cq = u.get("callback_query")
            if cq:
                if str((cq.get("from") or {}).get("id")) == str(TELEGRAM_ADMIN_ID):
                    tugmani_ishla(cq)
                else:
                    try:
                        mehmon_tugma(cq)
                    except Exception as e:
                        log(f"[mehmon] tugma xatosi: {e}")
                continue
            msg = u.get("message") or {}
            kim = str((msg.get("from") or {}).get("id") or "")
            if kim != str(TELEGRAM_ADMIN_ID):
                # Oddiy odam — bepul qo'llanma va taklif tizimi
                if kim and (msg.get("chat") or {}).get("type") == "private":
                    try:
                        mehmon_ishla(msg)
                    except Exception as e:
                        log(f"[mehmon] xato: {e}")
                continue
            matnni_ishla(msg.get("text", ""))
        obuna_yoz()
    obuna_yoz(majburiy=True)
    log("[listen] vaqt tugadi")


def weekly_preview():
    """Kelgusi 7 kunlik postlarni adminga oldindan yuboradi.

    Hikoya va dars — to'liq matni bilan.
    Yangilik — mavzu takliflari bilan (matni chiqish kuni jonli
    yoziladi, chunki yangilik eskirib qoladi).
    """
    st = stories_state()
    kun = st.get("last_sent", 0) + 1
    dars = st.get("kurs_oxirgi", 0) + 1
    kechki = st.get("oxirgi_kechki")

    hikoyalar, darslar, jadval, yangilik_kunlari = [], [], [], []
    t = now()
    for i in range(7):
        sana = t + timedelta(days=i)
        qator = f"<b>{sana:%d.%m}</b> · 08:45 — {kun + i}-hikoya"
        if kun + i > STORIES_TOTAL:
            qator = f"<b>{sana:%d.%m}</b> · 08:45 — <i>hikoyalar tugadi</i>"
        else:
            hikoyalar.append(kun + i)
        kechki = "yangilik" if kechki == "kurs" else "kurs"
        if kechki == "kurs":
            qator += f"  ·  19:45 — {dars}-dars"
            darslar.append(dars)
            dars += 1
        else:
            qator += "  ·  19:45 — yangilik"
            yangilik_kunlari.append(f"{sana:%d.%m}")
        jadval.append(qator)

    bosh = ("📅 <b>Kelgusi 7 kun</b>\n\n" + "\n".join(jadval) +
            "\n\nQuyida 7 kunlik hikoya va dars matnlari — aynan kanalga "
            "chiqadigan ko'rinishida.\n"
            "Yangilik matni har safar chiqishidan 10 daqiqa oldin alohida keladi.")
    tg_msg(TELEGRAM_ADMIN_ID, bosh)

    belgi = sozlama("hikoya_belgi", "[{N}-hikoya]")
    for n in hikoyalar:
        try:
            tepa = f"<b>{belgi.replace('{N}', str(n))}</b>\n\n" if belgi else ""
            tg_msg(TELEGRAM_ADMIN_ID, tepa + story_read(n))
            time.sleep(1)
        except Exception as e:
            log(f"[haftalik] {n}-hikoya yuborilmadi: {e}")
    for n in darslar:
        try:
            # Dars aynan kanalga chiqadigan ko'rinishida — nano banana
            # chizgan rasmi bilan birga ko'rsatiladi
            post = build_kurs_post(n)
            tg_send_post(TELEGRAM_ADMIN_ID, post)
            time.sleep(1)
        except Exception as e:
            log(f"[haftalik] {n}-dars rasm bilan chiqmadi: {e}")
            try:
                tg_msg(TELEGRAM_ADMIN_ID,
                       f"<b>[{n}-dars]</b> (rasmsiz — rasm chizilmadi)\n\n"
                       + _qisqa_naqsh(kurs_all()[n]))
            except Exception as e2:
                log(f"[haftalik] {n}-dars yuborilmadi: {e2}")

    # ── Yangilik mavzulari (matni chiqish kuni yoziladi) ────────
    if yangilik_kunlari:
        try:
            mavzular = topic_yangilik([], hammasi=True)[:len(yangilik_kunlari)]
        except Exception as e:
            log(f"[haftalik] yangilik mavzulari olinmadi: {e}")
            mavzular = []
        if mavzular:
            qatorlar = []
            for sana, m in zip(yangilik_kunlari, mavzular):
                qatorlar.append(
                    f"<b>{sana}</b> · {html.escape(m.get('topic', ''))}\n"
                    f"<i>{html.escape(m.get('why', ''))}</i>")
            tg_msg(TELEGRAM_ADMIN_ID,
                   "📰 <b>Kelgusi yangilik mavzulari</b>\n\n"
                   + "\n\n".join(qatorlar) +
                   "\n\nMatni chiqish kuni yoziladi — yangilik eskirmasligi "
                   "uchun. Mavzu yoqmasa ayting, boshqasini topaman.")
        else:
            tg_msg(TELEGRAM_ADMIN_ID,
                   "📰 Yangilik mavzularini hozir topa olmadim — "
                   "chiqish kuni jonli tayyorlanadi.")
        # Yangilikdan bitta namuna — kanalga chiqadigan ko'rinishida,
        # nano banana chizgan rasmi bilan
        try:
            tg_msg(TELEGRAM_ADMIN_ID,
                   "⏳ Yangilikdan bitta namuna tayyorlanmoqda...")
            namuna = build_post([], "namuna", src="yangilik")
            tg_send_post(TELEGRAM_ADMIN_ID, namuna)
            tg_msg(TELEGRAM_ADMIN_ID,
                   "☝️ Yangilik shu ko'rinishda chiqadi. Bu faqat namuna — "
                   "kanalga chiqmadi.")
        except Exception as e:
            log(f"[haftalik] yangilik namunasi chiqmadi: {e}")
            tg_msg(TELEGRAM_ADMIN_ID,
                   f"Yangilik namunasini hozir tayyorlay olmadim: "
                   f"<code>{str(e)[:200]}</code>")

    # ── 7 kunlikni tasdiqlash ───────────────────────────────────
    if hikoyalar or darslar:
        h1, h2 = (hikoyalar[0], hikoyalar[-1]) if hikoyalar else (0, 0)
        d1, d2 = (darslar[0], darslar[-1]) if darslar else (0, 0)
        nima = []
        if hikoyalar:
            nima.append(f"{len(hikoyalar)} ta hikoya")
        if darslar:
            nima.append(f"{len(darslar)} ta dars")
        kb = {"inline_keyboard": [[
            {"text": "✅ 7 kunlikni tasdiqlayman",
             "callback_data": f"hafta:{h1}-{h2}:{d1}-{d2}"},
        ], [
            {"text": "✏️ O'zgartirishim bor",
             "callback_data": f"haftayoq:{h1}-{h2}:{d1}-{d2}"},
        ]]}
        tg_msg(TELEGRAM_ADMIN_ID,
               f"Yuqoridagi <b>{' va '.join(nima)}</b> shu holicha "
               f"chiqaveradimi?\n"
               f"Tasdiqlasangiz — hafta davomida boshqa so'ramayman, "
               f"hammasi o'z vaqtida chiqadi.\n"
               f"Yangilik bunga kirmaydi — u jonli, har safar alohida "
               f"ko'rsataman.\n\n"
               f"O'zgartirish kerak bo'lsa — shunchaki ayting, tuzataman.",
               markup=kb)
    log(f"[haftalik] {len(hikoyalar)} hikoya, {len(darslar)} dars yuborildi")


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

    print(f"\n   Gemini kalitlari: {len(GEMINI_KEYS)} ta "
          f"({'zaxira bor' if len(GEMINI_KEYS) > 1 else 'zaxira YO`Q'})")
    print(f"   Matn modeli: {GEMINI_TEXT_MODEL} "
          f"(zaxira: {', '.join(TEXT_MODEL_FALLBACKS)})")
    print(f"   Grok (eng oxirgi zaxira): "
          f"{'bor, ' + str(len(GROK_KEYS)) + ' ta kalit' if GROK_KEYS else 'yo`q'}")

    # Har bir kalitni ALOHIDA sinaymiz — qaysi biri ishlayotgani ko'rinsin
    print("\n   --- Har bir kalit alohida sinovdan o'tkazilmoqda ---")
    for i, k in enumerate(GEMINI_KEYS, 1):
        oxiri = k[-6:] if len(k) > 6 else "?"
        sinovlar = [("matn", GEMINI_TEXT_MODEL,
                     {"contents": [{"role": "user",
                                    "parts": [{"text": "Salom deb javob ber"}]}]})]
        for m in IMAGE_MODEL_FALLBACKS:
            sinovlar.append((
                "rasm", m,
                {"contents": [{"role": "user", "parts": [
                    {"text": "A red apple on a white table, 3D render"}]}],
                 "generationConfig": {"responseModalities": ["IMAGE"]}}))
        for nom, model, tana in sinovlar:
            try:
                r = requests.post(f"{GEM_BASE}/{model}:generateContent",
                                  params={"key": k}, json=tana, timeout=120)
                if r.status_code == 200:
                    print(f"   [{i}] ...{oxiri} · {nom} ({model}): ISHLAYAPTI")
                else:
                    izoh = " ".join(r.text.split())[:700]
                    print(f"   [{i}] ...{oxiri} · {nom} ({model}): "
                          f"HTTP {r.status_code} — {izoh}")
            except Exception as e:
                print(f"   [{i}] ...{oxiri} · {nom}: {str(e)[:150]}")

    # Bepul rasm zaxirasi ishlaydimi
    print("\n   --- Bepul rasm zaxirasi ---")
    for nom, f in (("cloudflare", _cloudflare), ("pollinations", _pollinations)):
        try:
            p = f("A red apple on a white table, 3D render, soft light",
                  BUILD / f"test_{nom}.png")
            print(f"   {nom}: ISHLAYAPTI — {p.stat().st_size // 1024} KB")
        except Exception as e:
            print(f"   {nom}: {str(e)[:200]}")

    # 5. Kontent jadvali
    print(f"\n5) KONTENT JADVALI (slot: {SLOT})")
    st = stories_state()
    try:
        h = len(stories_all())
        print(f"   Hikoyalar: {h} ta · oxirgi chiqqan: {st.get('last_sent', 0)}-kun"
              f" · keyingi: {stories_next_day()}-kun")
    except Exception as e:
        print(f"   Hikoyalar XATO: {e}")
    try:
        k = len(kurs_all())
        print(f"   Kurs darslari: {k} ta · oxirgi chiqqan: {st.get('kurs_oxirgi', 0)}"
              f" · keyingi: {kurs_next()}-dars")
    except Exception as e:
        print(f"   Kurs XATO: {e}")
    print(f"   Oxirgi kechki post turi: {st.get('oxirgi_kechki') or '(yo`q)'}")
    print(f"   Bugungi slot uchun tanlangan tur: {pick_source()}")

    print("\n" + "=" * 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", action="store_true", help="jadvalni kutmasdan boshlash")
    ap.add_argument("--preview-only", action="store_true", help="kanalga chiqarmaslik")
    ap.add_argument("--doctor", action="store_true", help="faqat diagnostika")
    ap.add_argument("--day", type=int, default=None,
                    help="365 hikoyadan aniq kunni chiqarish (masalan --day 42)")
    ap.add_argument("--dars", type=int, default=None,
                    help="kursdan aniq darsni chiqarish (masalan --dars 12)")
    ap.add_argument("--listen", action="store_true",
                    help="botni tinglash rejimi (boshqaruv paneli)")
    ap.add_argument("--minutes", type=int, default=340,
                    help="tinglash rejimi necha daqiqa ishlasin")
    ap.add_argument("--weekly", action="store_true",
                    help="kelgusi 7 kunlik postlarni adminga yuborish")
    ap.add_argument("--pin", action="store_true",
                    help="pinned.txt ni kanalga chiqarib, qadab qo'yish")
    ap.add_argument("--tur", default="", choices=["", "yangilik", "kurs",
                                                  "hikoya", "ai"],
                    help="post turini majburan tanlash")
    args = ap.parse_args()

    if args.tur:
        globals()["POST_SOURCE"] = args.tur
        log(f"[jadval] tur majburan tanlandi: {args.tur}")

    if args.day:
        os.environ["FORCE_DAY"] = str(args.day)
    if args.dars:
        os.environ["FORCE_KURS"] = str(args.dars)

    if args.listen:
        try:
            listen(args.minutes)
            return 0
        except Exception as e:
            log(f"[FATAL] listen: {e}\n{traceback.format_exc()}")
            return 1

    if args.weekly:
        try:
            weekly_preview()
            return 0
        except Exception as e:
            log(f"[FATAL] haftalik ko'rik: {e}")
            return 1

    if args.pin:
        try:
            pin_post()
            return 0
        except Exception as e:
            log(f"[FATAL] pin: {e}")
            return 1

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
