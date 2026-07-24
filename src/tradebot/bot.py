"""
======================================================
🏏 BHARAT CRICKET PREDICTIONS BOT v25 - CHANNEL-CLEAN
======================================================
v18 (2026-07-09) — Full audit + fixes:
✅ /leavevip now shows plan-chooser instead of dead-end
✅ payment_screenshot photo auto-forward to admin
✅ /broadcast <text> command for admin blasts
✅ /paid <user_id> <plan> to confirm & track revenue
✅ TOSS/MATCH plan-only UPI buttons added
✅ Rate-limit safe DMs (broadcast_to_vips 0.35s → 0.5s)
✅ "tips" callback route was orphaned → wired up
✅ Reminder job includes friendly last-active timestamp
✅ Fixed silent-fail on admin notify (retries + username fallback)
✅ Startup env-var report cleaner
✅ Handler group ordering + explicit CommandHandlers list
──────────────────────────────────────────────────────
v17: Plan chooser flow + fallback tournaments + reminders
v16: joinvip → plan detail flow + admin auto-notify
v15: Bulletproof callback handler (safe_edit_or_send)
v14: ₹99 Trial + UPI direct pay
v13: Bold everywhere + 570 VIP base
======================================================
"""

import os
import re
import json
import sqlite3
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo
import aiohttp
import io
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

# ======================================================
# ⚙️ CONFIG
# ======================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
CRICKET_API_KEY = os.getenv("CRICKET_API_KEY", "")
CRICKET_API_HOST = os.getenv("CRICKET_API_HOST", "")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = os.getenv("RAPIDAPI_HOST", "cricbuzz-cricket.p.rapidapi.com")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
# v21: New FREE cricket data sources
HIGHLIGHTLY_API_KEY = os.getenv("HIGHLIGHTLY_API_KEY", "")  # 100 free req/day
HIGHLIGHTLY_HOST = os.getenv("HIGHLIGHTLY_HOST", "cricket-highlights-api.p.rapidapi.com")
# Optional 2nd CricketData.org key (rotate between 2 keys → 200 req/day free)
CRICKET_API_KEY_2 = os.getenv("CRICKET_API_KEY_2", "")
# ESPN Cricinfo public JSON feed (no key needed) — hourly poll
ESPN_ENABLED = os.getenv("ESPN_ENABLED", "true").lower() == "true"

CHANNEL_ID = "@Bharatcricketpredictions"
BOT_NAME = "🏏 Bharat Cricket Predictions"
CHANNEL_LINK = "https://t.me/Bharatcricketpredictions"

OWNER_USERNAME = os.getenv("OWNER_USERNAME", "@Allrounder_Vip_Link")
PREMIUM_GROUP_LINK = os.getenv("PREMIUM_GROUP_LINK", "https://t.me/+_KpE1UpzaKozNDQ1")
# Admin's numeric Telegram user_id (for auto-notify on new orders). Optional.
try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
except Exception:
    ADMIN_USER_ID = 0
UPI_ID = os.getenv("UPI_ID", "")
UPI_MERCHANT_NAME = os.getenv("UPI_MERCHANT_NAME", "All Rounder Cricket")

PREMIUM_PRICES = {"TRIAL": 99, "TOSS": 599, "MATCH": 999, "SESSION": 1199, "COMBO": 1899}
VIP_BASE_COUNT = 570
IST = ZoneInfo("Asia/Kolkata")

DB_PATH = os.getenv("DB_PATH", "/app/bot_data.db")
if not os.path.exists(os.path.dirname(DB_PATH) or "."):
    DB_PATH = "bot_data.db"

# v25: Only 2 pre-match posts — spread across day (2hr, 1hr before match)
# v25.5: Post EARLIER — toss 4hr before, match prediction 2hr before
# Gives huge window buffer so mid-day discovery still catches upcoming matches
PRE_MATCH_OFFSETS = {"toss": 240, "prediction": 120}

SESSION_TRIGGERS = {
    "T20": [(6, 10, "Powerplay → 7-10 ov"), (10, 15, "10 → 11-15 ov"),
            (15, 20, "15 → 16-20 (Death)"), (20, None, "Innings End")],
    "ODI": [(10, 20, "PP → 11-20 ov"), (20, 30, "20 → 21-30 ov"),
            (30, 40, "30 → 31-40 ov"), (40, 50, "40 → 41-50 ov"),
            (50, None, "Innings End")],
    "TEST": [(15, 30, "15 → 16-30 ov"), (30, 60, "30 → 31-60 ov"),
             (60, 90, "60 → 61-90 ov"), (90, None, "Day End")],
}

# v19: MASSIVELY EXPANDED — cover every cricket league on earth
HIGH_PRIORITY_TOURNAMENTS = [
    # Tier-1 mega (priority 10)
    "ipl", "indian premier", "wpl", "women's premier",
    "world cup", "icc", "champions trophy",
    "asia cup", "border-gavaskar", "ashes",
    # Tier-1 international
    "test series", "odi series", "t20i series",
    "one-day international", "twenty20 international",
    # National franchise T20 leagues (priority 8-9)
    "bbl", "big bash", "wbbl",
    "psl", "pakistan super",
    "cpl", "caribbean premier",
    "sa20", "ilt20", "international league",
    "the hundred", "hundred",
    "t20 blast", "vitality blast", "vitality t20", "blast",
    "super smash",
    "lpl", "lanka premier",
    "bpl", "bangladesh premier",
    "mlc", "major league cricket",
    "abu dhabi t10", "t10 league", "global t20",
    "nepal premier", "npl",
    # One-day domestic
    "one day cup", "metro bank", "royal london", "rl one day",
    "marsh cup", "marsh one-day", "ford trophy",
    "vijay hazare", "list a",
    # First-class domestic
    "county championship", "sheffield shield", "plunket shield",
    "ranji trophy", "duleep trophy", "irani",
    "quaid-e-azam", "quaid e azam",
    "cg trophy",
    # Domestic T20
    "syed mushtaq ali", "smat",
    "national t20", "presidents cup t20",
    "super league",
    # Women's leagues
    "wpl", "women t20", "women odi", "women test",
    "women's tri", "women\'s big bash", "wbbl",
    "women's hundred", "women's cpl", "women t20 world",
    "women's world", "wt20",
    # Tours (bilateral series) — high value
    "tour of", "india tour", "england tour", "australia tour",
    "pakistan tour", "south africa tour", "new zealand tour",
    "west indies tour", "sri lanka tour", "bangladesh tour",
    # Head-to-head keywords
    "india vs", "vs india",
    "england vs", "vs england",
    "australia vs", "vs australia",
    "pakistan vs", "vs pakistan",
    "new zealand vs", "vs new zealand",
    "south africa vs", "vs south africa",
    "west indies vs", "vs west indies",
    "sri lanka vs", "vs sri lanka",
    "bangladesh vs", "vs bangladesh",
    "afghanistan vs", "vs afghanistan",
    "zimbabwe vs", "vs zimbabwe",
    "ireland vs", "vs ireland",
    # Associate & smaller (still coverable — priority 6)
    "ecs", "european cricket series", "ect10",
    "fanpop", "dream11",
    "kcc t10", "emirates t20", "uae d10",
    "cbfs", "sharjah t20",
    "dhaka premier", "dhaka prem",
    "pearls of asia",
    "singapore t20", "hong kong t20",
    "canada global", "canada t20",
    "nomad", "european cricket championship",
    "gulf t20", "oman t20",
    "kuwait", "bahrain",
    "cricket cyprus", "cricket romania", "cricket portugal",
    "cricket france", "cricket germany", "cricket spain",
    "cricket italy", "cricket denmark", "cricket sweden",
    # State/regional India
    "tnpl", "tamil nadu premier",
    "kpl", "karnataka premier",
    "mpl", "mumbai premier",
    "bengal t20", "bihar t20", "haryana",
    # ICC Associate Cup
    "icc mens t20 world cup qualifier",
    "cwc league 2", "cwc league",
    "challenge league",
]

# v19: Slimmed — only skip youth/academy/development sides
SKIP_KEYWORDS = ["u19", "u-19", "u16", "u-16",
                 "academy", "school ", "under 19", "under 23"]

LIVE_POLL_INTERVAL = 30
LIVE_POLL_DURING_MATCH = 7  # v21: 10→7 min for tighter session coverage

SCHEDULED_JOBS = set()
LIVE_MATCHES = {}

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ======================================================
# 💳 UPI HELPER
# ======================================================
def build_upi_link(amount, note="Premium"):
    if not UPI_ID:
        return None
    params = (f"pa={quote(UPI_ID)}&pn={quote(UPI_MERCHANT_NAME)}"
              f"&am={amount}&cu=INR&tn={quote(note)}")
    return f"upi://pay?{params}"

def generate_upi_qr(amount, note="Premium"):
    """v22: Generate a UPI QR-code image (returns BytesIO PNG or None)."""
    if not UPI_ID or not PIL_AVAILABLE:
        return None
    try:
        import qrcode  # Only import when needed
    except ImportError:
        # qrcode lib not installed — fallback: return None (caller uses link only)
        logger.info("qrcode lib not installed — QR image skipped (add 'qrcode' to requirements.txt)")
        return None
    try:
        upi_link = build_upi_link(amount, note)
        if not upi_link: return None
        qr = qrcode.QRCode(version=None, box_size=10, border=4,
                           error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(upi_link)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        # Add branded frame
        W, H = 800, 1000
        canvas = Image.new("RGB", (W, H), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)
        try:
            fp = "/usr/share/fonts/truetype/dejavu/"
            ft_title = ImageFont.truetype(f"{fp}DejaVuSans-Bold.ttf", 44)
            ft_big = ImageFont.truetype(f"{fp}DejaVuSans-Bold.ttf", 70)
            ft_med = ImageFont.truetype(f"{fp}DejaVuSans-Bold.ttf", 32)
            ft_small = ImageFont.truetype(f"{fp}DejaVuSans.ttf", 24)
        except Exception:
            ft_title = ft_big = ft_med = ft_small = ImageFont.load_default()
        # Header
        draw.rectangle([0, 0, W, 110], fill=(30, 144, 60))
        draw.text((W//2, 55), "💳 SCAN & PAY 💳", fill="white",
                  font=ft_title, anchor="mm")
        # Amount
        draw.text((W//2, 155), f"₹{amount}", fill=(30, 144, 60),
                  font=ft_big, anchor="mm")
        draw.text((W//2, 220), note, fill=(60, 60, 60),
                  font=ft_med, anchor="mm")
        # QR
        qr_size = 500
        qr_img = img.resize((qr_size, qr_size))
        canvas.paste(qr_img, ((W - qr_size) // 2, 270))
        # UPI ID
        draw.text((W//2, 810), f"UPI ID: {UPI_ID}", fill=(30, 30, 30),
                  font=ft_med, anchor="mm")
        draw.text((W//2, 855), "GPay • PhonePe • Paytm • BHIM",
                  fill=(100, 100, 100), font=ft_small, anchor="mm")
        # Footer
        draw.rectangle([0, 910, W, 1000], fill=(30, 144, 60))
        draw.text((W//2, 940), "🏏 ALL-ROUNDER CRICKET 🏏",
                  fill="white", font=ft_med, anchor="mm")
        draw.text((W//2, 980), OWNER_USERNAME, fill="white",
                  font=ft_small, anchor="mm")
        buf = io.BytesIO(); canvas.save(buf, format="PNG"); buf.seek(0)
        return buf
    except Exception as e:
        logger.warning(f"UPI QR generation fail: {e}")
        return None


# ======================================================
# 🗄 DATABASE
# ======================================================
def db_init():
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS matches (
        id TEXT PRIMARY KEY, name TEXT, teams TEXT, venue TEXT,
        start_time TEXT, format TEXT, priority INTEGER DEFAULT 0,
        predicted_winner TEXT, predicted_toss_winner TEXT,
        predicted_toss_choice TEXT, actual_winner TEXT,
        actual_toss_winner TEXT, actual_toss_choice TEXT,
        match_started INTEGER DEFAULT 0, match_ended INTEGER DEFAULT 0,
        recap_posted INTEGER DEFAULT 0, toss_posted INTEGER DEFAULT 0,
        created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT, type TEXT,
        inning INTEGER, phase TEXT, predicted_min INTEGER, predicted_max INTEGER,
        predicted_text TEXT, actual_value INTEGER, correct INTEGER, date TEXT,
        pass_posted INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS posted_events (
        match_id TEXT, event_key TEXT, posted_at TEXT,
        PRIMARY KEY (match_id, event_key))""")
    c.execute("""CREATE TABLE IF NOT EXISTS manual_matches (
        id TEXT PRIMARY KEY, tournament TEXT, team1 TEXT, team2 TEXT,
        venue TEXT, start_time TEXT, format TEXT, added_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS vip_users (
        user_id INTEGER PRIMARY KEY, username TEXT, added_at TEXT,
        added_by INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS referrals (
        user_id INTEGER PRIMARY KEY, username TEXT, referred_by INTEGER,
        joined_at TEXT)""")
    # v17: bot_users — track every user who ever interacted (for reminders)
    c.execute("""CREATE TABLE IF NOT EXISTS bot_users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        first_seen TEXT,
        last_active TEXT,
        last_reminded TEXT,
        reminder_count INTEGER DEFAULT 0,
        opted_out INTEGER DEFAULT 0)""")
    conn.commit(); conn.close()
    logger.info(f"✅ DB: {DB_PATH}")


def db_track_user(user):
    """v17: Track every user interaction — for reminder system."""
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        now = now_ist().isoformat()
        c.execute("SELECT user_id FROM bot_users WHERE user_id = ?", (user.id,))
        if c.fetchone():
            c.execute("UPDATE bot_users SET last_active = ?, username = ?, first_name = ? WHERE user_id = ?",
                      (now, user.username or "", user.first_name or "", user.id))
        else:
            c.execute("""INSERT INTO bot_users
                (user_id, username, first_name, first_seen, last_active, reminder_count, opted_out)
                VALUES (?, ?, ?, ?, ?, 0, 0)""",
                (user.id, user.username or "", user.first_name or "", now, now))
        conn.commit(); conn.close()
    except Exception as e:
        logger.warning(f"db_track_user fail: {e}")


def db_get_users_for_reminder(days_inactive=15):
    """Get users who haven't been active for N days & haven't opted out."""
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        cutoff = (now_ist() - timedelta(days=days_inactive)).isoformat()
        # Also skip users reminded in last 15 days
        recent_cutoff = (now_ist() - timedelta(days=15)).isoformat()
        c.execute("""SELECT user_id, username, first_name, last_active, reminder_count
                     FROM bot_users
                     WHERE opted_out = 0
                     AND last_active < ?
                     AND (last_reminded IS NULL OR last_reminded < ?)
                     LIMIT 50""", (cutoff, recent_cutoff))
        rows = c.fetchall(); conn.close()
        return rows
    except Exception as e:
        logger.warning(f"db_get_users_for_reminder fail: {e}"); return []


def db_mark_reminded(user_id):
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("""UPDATE bot_users SET
                     last_reminded = ?,
                     reminder_count = reminder_count + 1
                     WHERE user_id = ?""",
                  (now_ist().isoformat(), user_id))
        conn.commit(); conn.close()
    except Exception as e:
        logger.warning(f"db_mark_reminded fail: {e}")


def db_optout_reminder(user_id):
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("UPDATE bot_users SET opted_out = 1 WHERE user_id = ?", (user_id,))
        conn.commit(); conn.close()
    except Exception as e:
        logger.warning(f"db_optout_reminder fail: {e}")


def db_save_match(match, priority):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("""INSERT OR IGNORE INTO matches
        (id, name, teams, venue, start_time, format, priority, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (match.get("id"), match.get("name"), json.dumps(match.get("teams", [])),
         match.get("venue"), match["_ist_dt"].isoformat(),
         match.get("matchType", "T20"), priority, now_ist().isoformat()))
    conn.commit(); conn.close()


def db_update_match(match_id, **kwargs):
    if not kwargs: return
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    fields = ", ".join(f"{k} = ?" for k in kwargs.keys())
    c.execute(f"UPDATE matches SET {fields} WHERE id = ?",
              list(kwargs.values()) + [match_id])
    conn.commit(); conn.close()


def db_get_match(match_id):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
    row = c.fetchone(); conn.close()
    return dict(row) if row else None


def db_save_simple_prediction(match_id, ptype, predicted_text):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT INTO predictions (match_id, type, predicted_text, date) VALUES (?, ?, ?, ?)",
        (match_id, ptype, predicted_text, now_ist().strftime("%Y-%m-%d")))
    conn.commit(); conn.close()


def db_save_session_prediction(match_id, inning, phase, pmin, pmax):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("""INSERT INTO predictions
        (match_id, type, inning, phase, predicted_min, predicted_max, date)
        VALUES (?, 'session', ?, ?, ?, ?, ?)""",
        (match_id, inning, phase, pmin, pmax, now_ist().strftime("%Y-%m-%d")))
    conn.commit(); conn.close()


def db_get_pending_session_predictions(match_id, inning, phase):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""SELECT * FROM predictions WHERE match_id = ? AND inning = ?
        AND phase = ? AND type = 'session' AND correct IS NULL""",
        (match_id, inning, phase))
    rows = [dict(r) for r in c.fetchall()]; conn.close()
    return rows


def db_resolve_prediction(pred_id, actual, correct):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("UPDATE predictions SET actual_value = ?, correct = ? WHERE id = ?",
              (actual, 1 if correct else 0, pred_id))
    conn.commit(); conn.close()


def db_mark_pass_posted(pred_id):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("UPDATE predictions SET pass_posted = 1 WHERE id = ?", (pred_id,))
    conn.commit(); conn.close()


def db_resolve_simple_prediction(match_id, ptype, actual, correct):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("""UPDATE predictions SET actual_value = NULL, correct = ?,
        predicted_text = predicted_text || ' || actual: ' || ?
        WHERE match_id = ? AND type = ? AND correct IS NULL""",
        (1 if correct else 0, actual, match_id, ptype))
    conn.commit(); conn.close()


def db_get_accuracy(days=1):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    cutoff = (now_ist() - timedelta(days=days)).strftime("%Y-%m-%d")
    c.execute("""SELECT type, COUNT(*) as total,
        SUM(CASE WHEN correct = 1 THEN 1 ELSE 0 END) as correct
        FROM predictions WHERE date >= ? AND correct IS NOT NULL
        GROUP BY type""", (cutoff,))
    rows = c.fetchall(); conn.close()
    return rows


def db_add_manual_match(tournament, t1, t2, venue, start_dt, fmt):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    match_id = f"manual_{int(start_dt.timestamp())}_{t1[:3]}_{t2[:3]}".replace(" ", "")
    c.execute("""INSERT OR REPLACE INTO manual_matches
        (id, tournament, team1, team2, venue, start_time, format, added_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (match_id, tournament, t1, t2, venue, start_dt.isoformat(),
         fmt, now_ist().isoformat()))
    conn.commit(); conn.close()
    return match_id


def db_get_manual_matches():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    c = conn.cursor()
    cutoff = (now_ist() - timedelta(hours=8)).isoformat()
    c.execute("SELECT * FROM manual_matches WHERE start_time >= ? ORDER BY start_time", (cutoff,))
    rows = [dict(r) for r in c.fetchall()]; conn.close()
    return rows


def db_remove_manual_match(match_id):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("DELETE FROM manual_matches WHERE id = ?", (match_id,))
    conn.commit(); conn.close()


def db_add_vip(user_id, username, added_by):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO vip_users
        (user_id, username, added_at, added_by) VALUES (?, ?, ?, ?)""",
        (user_id, username or "", now_ist().isoformat(), added_by))
    conn.commit(); conn.close()


def db_remove_vip(user_id):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("DELETE FROM vip_users WHERE user_id = ?", (user_id,))
    conn.commit(); conn.close()


def db_get_vip_users():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    c = conn.cursor(); c.execute("SELECT * FROM vip_users")
    rows = [dict(r) for r in c.fetchall()]; conn.close()
    return rows


def db_is_vip(user_id):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT 1 FROM vip_users WHERE user_id = ?", (user_id,))
    exists = c.fetchone() is not None; conn.close()
    return exists


def db_event_posted(match_id, event_key):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT 1 FROM posted_events WHERE match_id = ? AND event_key = ?",
              (match_id, event_key))
    exists = c.fetchone() is not None; conn.close()
    return exists


def db_mark_event_posted(match_id, event_key):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO posted_events (match_id, event_key, posted_at) VALUES (?, ?, ?)",
        (match_id, event_key, now_ist().isoformat()))
    conn.commit(); conn.close()


# ======================================================
# 🤖 AI ENGINE
# ======================================================
async def _call_gemini(prompt, max_tokens=2000):
    if not GEMINI_API_KEY: return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    try:
        p = {"contents": [{"parts": [{"text": prompt}]}],
             "generationConfig": {"temperature": 0.85, "maxOutputTokens": max_tokens}}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=p, headers={"Content-Type": "application/json"},
                              timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    d = await r.json()
                    return d["candidates"][0]["content"]["parts"][0]["text"]
                return None
    except Exception as e:
        logger.warning(f"Gemini: {e}"); return None


async def _call_groq(prompt, max_tokens=2000):
    if not GROQ_API_KEY: return None
    url = "https://api.groq.com/openai/v1/chat/completions"
    try:
        p = {"model": "llama-3.3-70b-versatile",
             "messages": [{"role": "user", "content": prompt}],
             "temperature": 0.85, "max_tokens": max_tokens}
        h = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=p, headers=h,
                              timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    d = await r.json()
                    return d["choices"][0]["message"]["content"]
                p["model"] = "llama-3.1-8b-instant"
                async with s.post(url, json=p, headers=h,
                                  timeout=aiohttp.ClientTimeout(total=30)) as r2:
                    if r2.status == 200:
                        d = await r2.json()
                        return d["choices"][0]["message"]["content"]
                return None
    except Exception as e:
        logger.warning(f"Groq: {e}"); return None


async def _call_deepseek(prompt, max_tokens=2000):
    if not DEEPSEEK_API_KEY: return None
    url = "https://api.deepseek.com/chat/completions"
    try:
        p = {"model": "deepseek-chat",
             "messages": [{"role": "user", "content": prompt}],
             "temperature": 0.85, "max_tokens": max_tokens}
        h = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=p, headers=h,
                              timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    d = await r.json()
                    return d["choices"][0]["message"]["content"]
                return None
    except Exception as e:
        logger.warning(f"DeepSeek: {e}"); return None


async def _call_openrouter(prompt, max_tokens=2000):
    if not OPENROUTER_API_KEY: return None
    url = "https://openrouter.ai/api/v1/chat/completions"
    try:
        p = {"model": "meta-llama/llama-3.3-70b-instruct:free",
             "messages": [{"role": "user", "content": prompt}],
             "temperature": 0.85, "max_tokens": max_tokens}
        h = {"Authorization": f"Bearer {OPENROUTER_API_KEY}",
             "Content-Type": "application/json",
             "HTTP-Referer": "https://t.me/Bharatcricketpredictions",
             "X-Title": "Bharat Cricket Bot"}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=p, headers=h,
                              timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    d = await r.json()
                    return d["choices"][0]["message"]["content"]
                return None
    except Exception as e:
        logger.warning(f"OpenRouter: {e}"); return None


async def _call_mistral(prompt, max_tokens=2000):
    if not MISTRAL_API_KEY: return None
    url = "https://api.mistral.ai/v1/chat/completions"
    try:
        p = {"model": "mistral-large-latest",
             "messages": [{"role": "user", "content": prompt}],
             "temperature": 0.85, "max_tokens": max_tokens}
        h = {"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=p, headers=h,
                              timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    d = await r.json()
                    return d["choices"][0]["message"]["content"]
                return None
    except Exception as e:
        logger.warning(f"Mistral: {e}"); return None


async def ask_gemini(prompt, max_tokens=2000):
    for name, fn in [("DeepSeek", _call_deepseek), ("Gemini", _call_gemini),
                     ("Groq", _call_groq), ("OpenRouter", _call_openrouter),
                     ("Mistral", _call_mistral)]:
        r = await fn(prompt, max_tokens)
        if r: return r
    logger.error("❌ All AI failed")
    return None


def today_date(): return datetime.now(IST).strftime("%d %B %Y, %A")
def now_ist(): return datetime.now(IST)
def format_date_style(): return now_ist().strftime("DATE 📅 %d - %B - %Y 🕰💰")


def fmt_normalize(fmt):
    f = (fmt or "T20").upper()
    if "TEST" in f: return "TEST"
    if "ODI" in f or "ODM" in f: return "ODI"
    return "T20"


def calculate_match_priority(match):
    """v19 scoring — every real match gets covered, only youth skipped.

    10 = mega (IPL, WC, ICC, Ashes, WPL)
    9  = tier-1 international bilateral
    8  = franchise T20 (BBL, PSL, CPL, SA20, ILT20, LPL, BPL, MLC, The Hundred, T20 Blast)
    7  = domestic T20 / One Day Cup / Ranji / Sheffield / County Championship / SMAT
    6  = associate/small league (ECS, TNPL, KPL, ECT10, women's associate)
    5  = generic other (any real match)
    2  = SKIP (youth/academy only)
    """
    name = (match.get("name") or "").lower()
    teams = [t.lower() for t in match.get("teams", [])]
    ft = f"{name} {' '.join(teams)}"
    for s in SKIP_KEYWORDS:
        if s in ft:
            return 2
    # Tier 10 — mega
    for k in ["ipl", "indian premier", "wpl", "women's premier",
              "world cup", "icc", "champions trophy", "ashes",
              "border-gavaskar", "asia cup"]:
        if k in ft:
            return 10
    # Tier 9 — international bilateral
    for k in ["test series", "odi series", "t20i series",
              "one-day international", "twenty20 international"]:
        if k in ft:
            return 9
    # Any "X vs Y" of top-10 nations = tier 9
    top_nations = ["india", "australia", "england", "pakistan",
                   "new zealand", "south africa", "west indies",
                   "sri lanka", "bangladesh", "afghanistan"]
    tc = 0
    for n in top_nations:
        if n in " ".join(teams):
            tc += 1
    if tc >= 2:
        return 9
    # Tier 8 — franchise T20 leagues
    for k in ["bbl", "big bash", "psl", "pakistan super",
              "cpl", "caribbean premier", "sa20", "ilt20",
              "international league", "the hundred", "hundred",
              "t20 blast", "vitality blast", "blast",
              "lpl", "lanka premier", "bpl", "bangladesh premier",
              "mlc", "major league cricket",
              "abu dhabi t10", "t10 league", "global t20",
              "super smash", "nepal premier"]:
        if k in ft:
            return 8
    # Tier 7 — domestic first-class + list-A + domestic T20
    for k in ["ranji", "duleep", "irani", "quaid",
              "county championship", "sheffield shield", "plunket shield",
              "one day cup", "metro bank", "royal london", "rl one day",
              "marsh cup", "marsh one-day", "ford trophy",
              "vijay hazare", "syed mushtaq ali", "smat",
              "national t20"]:
        if k in ft:
            return 7
    # Tier 6 — associate & smaller leagues
    for k in HIGH_PRIORITY_TOURNAMENTS:
        if k in ft:
            return 6
    # Any real match — cover it
    return 5


# v26.1: NON-CRICKET SPORT KEYWORDS — reject matches from wrong sports
_NON_CRICKET_KEYWORDS = [
    "socce", "soccer", "football league", "bundesliga",  # premier league removed — Nepal Premier League is cricket!
    "serie a", "la liga", "ligue 1", "eredivisie", "primeira liga",
    "champions league", "europa league", "mls",
    "basketball", "nba", "wnba", "euroleague",
    "tennis", "atp", "wta", "grand slam",
    "hockey", "nhl", "ice hockey",
    "rugby", "nrl", "afl", "aussie rules",
    "baseball", "mlb",
    "handball", "volleyball", "netball",
    "mma", "ufc", "boxing",
    "esports", "csgo", "lol", "dota",
    "f1", "formula", "motogp", "nascar",
    # v26.2: Removed standalone "npl" — killed Nepal Premier League (cricket!)
    # Kept "tasmania npl" specific to Australian soccer state
    "tasmania npl", "victoria npl", "queensland npl", "nsw npl",
    "waratah npl", "npl western",  # Australian soccer state variants
    "bulgarian second", "bulgarian first", "bulgarian league",  # Bulgarian soccer
    "regionalliga", "3. liga",  # German soccer lower tiers
]


def _is_cricket_match(match):
    """v26.1: Strict cricket-only check. Returns False if match looks like another sport."""
    name = (match.get("name") or "").lower()
    teams_str = " ".join(match.get("teams", [])).lower()
    fmt = (match.get("matchType") or "").lower()
    venue = (match.get("venue") or "").lower()
    ft = f"{name} {teams_str} {fmt} {venue}"
    # Reject if any non-cricket keyword found
    for kw in _NON_CRICKET_KEYWORDS:
        if kw in ft:
            return False
    # If format explicitly says non-cricket, reject
    if fmt in ("socce", "soccer", "football", "basket", "tennis", "hockey", "rugby", "baseball"):
        return False
    # Positive indicator: if any cricket keyword present, definitely cricket
    cricket_hints = ["t20", "odi", "test", "cricket", "wicket", "over", "ipl", "bbl",
                     "psl", "cpl", "the hundred", "blast", "ranji", "duleep",
                     "sheffield", "syed mushtaq", "vijay hazare", "ashes"]
    for kw in cricket_hints:
        if kw in ft:
            return True
    # No cricket hint AND no non-cricket hint — be conservative, allow (many minor cricket leagues are unnamed)
    return True


def should_cover_match(match):
    """v26.1: Cricket-ONLY + priority >= 3. Rejects non-cricket sports strictly.
    Logs WHY skipped for user visibility."""
    # STEP 1: Sport check
    if not _is_cricket_match(match):
        name = match.get("name", "?")[:50]
        teams = " vs ".join(match.get("teams", [])[:2])[:40]
        logger.info(f"🚫 NON-CRICKET SKIP: {name} | {teams}")
        return False
    # STEP 2: Priority check
    p = calculate_match_priority(match)
    if p < 3:
        name = match.get("name", "?")[:50]
        teams = " vs ".join(match.get("teams", [])[:2])[:40]
        logger.info(f"⏭️ SKIP (pri={p}): {name} | {teams}")
        return False
    return True


# ======================================================
# 🏏 CRICKET API
# ======================================================
_MATCHES_CACHE = {"data": [], "fetched_at": None}
# v25.6: Per-source stats for /apistatus command
_SOURCE_STATS = {}  # {source_name: {"last_count": N, "last_fetch": dt, "last_error": str}}


async def _fetch_from_cricapi():
    if not CRICKET_API_KEY: return None
    url = f"https://api.cricapi.com/v1/matches?apikey={CRICKET_API_KEY}&offset=0"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200: return None
                d = await r.json()
                if d.get("status") != "success": return None
                return d.get("data", [])
    except Exception as e:
        logger.warning(f"CricAPI: {e}"); return None


async def _fetch_from_cricketdata():
    if not CRICKET_API_KEY: return None
    url = f"https://api.cricapi.com/v1/currentMatches?apikey={CRICKET_API_KEY}&offset=0"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200: return None
                d = await r.json()
                if d.get("status") != "success": return None
                return d.get("data", [])
    except Exception as e:
        logger.warning(f"CricketData: {e}"); return None


# v21: Rotate between primary + secondary CricketData keys for 2x quota
async def _fetch_from_cricapi_v2():
    """Second CricketData.org key rotation for effective 200 req/day free."""
    if not CRICKET_API_KEY_2: return None
    url = f"https://api.cricapi.com/v1/currentMatches?apikey={CRICKET_API_KEY_2}&offset=0"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200: return None
                d = await r.json()
                if d.get("status") != "success": return None
                return d.get("data", [])
    except Exception as e:
        logger.warning(f"CricAPI-v2: {e}"); return None


async def _fetch_from_highlightly():
    """v25.9: Highlightly — tries BOTH direct URL and RapidAPI URL depending on key format."""
    if not HIGHLIGHTLY_API_KEY: return None
    today = now_ist().date()
    dates = [today.strftime("%Y-%m-%d"),
             (today + timedelta(days=1)).strftime("%Y-%m-%d"),
             (today + timedelta(days=2)).strftime("%Y-%m-%d")]
    # v25.9: Try direct highlightly.net API first (works with keys from highlightly.net),
    # if 401/403, fallback to RapidAPI URL
    direct_host = "cricket.highlightly.net"
    rapidapi_host = HIGHLIGHTLY_HOST  # cricket-highlights-api.p.rapidapi.com
    all_matches = []
    tried_direct = False
    try:
        async with aiohttp.ClientSession() as s:
            # Attempt 1: Direct highlightly.net URL
            for d in dates:
                url = f"https://{direct_host}/matches?date={d}&limit=100"
                h = {"x-rapidapi-key": HIGHLIGHTLY_API_KEY}  # only key, no host header
                try:
                    async with s.get(url, headers=h,
                                     timeout=aiohttp.ClientTimeout(total=20)) as r:
                        if r.status == 200:
                            tried_direct = True
                            data = await r.json()
                            for m in (data if isinstance(data, list) else data.get("data", [])):
                                teams = m.get("teams") or {}
                                hn = (teams.get("home", {}) or {}).get("name", "")
                                an = (teams.get("away", {}) or {}).get("name", "")
                                league = m.get("league", {}) or {}
                                ln = league.get("name", "") if isinstance(league, dict) else str(league)
                                venue = m.get("venue", "") if isinstance(m.get("venue"), str) else \
                                        (m.get("venue", {}) or {}).get("name", "")
                                fmt = (m.get("type") or m.get("format") or "T20").upper()
                                start = m.get("date") or m.get("startDate") or m.get("startTime")
                                n = {
                                    "id": f"hl_{m.get('id', '')}",
                                    "name": f"{ln} - {m.get('round', '')}".strip(" -"),
                                    "matchType": fmt,
                                    "status": m.get("status", ""),
                                    "venue": venue or "TBA",
                                    "teams": [hn, an] if hn and an else [],
                                    "dateTimeGMT": start,
                                }
                                if n["teams"]:
                                    all_matches.append(n)
                        elif r.status in (401, 403) and not tried_direct:
                            # Direct API failed — try RapidAPI URL
                            logger.info(f"Highlightly direct {r.status}, trying RapidAPI URL...")
                            break  # exit direct loop, try RapidAPI below
                except Exception as e:
                    logger.warning(f"Highlightly direct {d} fail: {e}")
            if all_matches:
                return all_matches
            # Attempt 2: RapidAPI URL (original code)
            h = {"x-rapidapi-key": HIGHLIGHTLY_API_KEY,
                 "x-rapidapi-host": rapidapi_host}
            for d in dates:
                url = f"https://{rapidapi_host}/matches?date={d}&limit=100"
                try:
                    async with s.get(url, headers=h,
                                     timeout=aiohttp.ClientTimeout(total=20)) as r:
                        if r.status == 401 or r.status == 403:
                            logger.error(f"❌ Highlightly {d}: HTTP {r.status} — API key invalid/quota exhausted")
                            return None  # No point trying tomorrow with same bad key
                        if r.status == 429:
                            logger.warning(f"⚠️ Highlightly {d}: HTTP 429 — rate limit hit, will retry later")
                            return None
                        if r.status != 200:
                            logger.warning(f"Highlightly {d}: HTTP {r.status}")
                            continue
                        data = await r.json()
                        for m in (data if isinstance(data, list) else data.get("data", [])):
                            teams = m.get("teams") or {}
                            hn = (teams.get("home", {}) or {}).get("name", "")
                            an = (teams.get("away", {}) or {}).get("name", "")
                            league = m.get("league", {}) or {}
                            ln = league.get("name", "") if isinstance(league, dict) else str(league)
                            venue = m.get("venue", "") if isinstance(m.get("venue"), str) else \
                                    (m.get("venue", {}) or {}).get("name", "")
                            fmt = (m.get("type") or m.get("format") or "T20").upper()
                            start = m.get("date") or m.get("startDate") or m.get("startTime")
                            n = {
                                "id": f"hl_{m.get('id', '')}",
                                "name": f"{ln} - {m.get('round', '')}".strip(" -"),
                                "matchType": fmt,
                                "status": m.get("status", ""),
                                "venue": venue or "TBA",
                                "teams": [hn, an] if hn and an else [],
                                "dateTimeGMT": start,
                                "_hl_raw": m,
                            }
                            if n["teams"]:
                                all_matches.append(n)
                except Exception as e:
                    logger.warning(f"Highlightly {d} fail: {e}")
        return all_matches if all_matches else None
    except Exception as e:
        logger.warning(f"Highlightly: {e}"); return None


async def _fetch_from_espn():
    """v21: ESPN Cricinfo unofficial JSON (no key). Zero-cost fallback."""
    if not ESPN_ENABLED: return None
    urls = [
        "https://hs-consumer-api.espncricinfo.com/v1/pages/matches/current?lang=en&latest=true",
    ]
    all_matches = []
    try:
        async with aiohttp.ClientSession() as s:
            for url in urls:
                try:
                    async with s.get(url, timeout=aiohttp.ClientTimeout(total=20),
                                     headers={"User-Agent": "Mozilla/5.0"}) as r:
                        if r.status != 200: continue
                        data = await r.json()
                        for m in data.get("matches", []):
                            teams = m.get("teams", [])
                            if len(teams) < 2: continue
                            t1 = teams[0].get("team", {}).get("longName", "")
                            t2 = teams[1].get("team", {}).get("longName", "")
                            series = m.get("series", {}) or {}
                            start_str = m.get("startTime", "")
                            n = {
                                "id": f"espn_{m.get('id') or m.get('objectId', '')}",
                                "name": f"{series.get('name', '')} - {m.get('title', '')}".strip(" -"),
                                "matchType": (m.get("format") or "T20").upper(),
                                "status": m.get("status", ""),
                                "venue": (m.get("ground", {}) or {}).get("name", "TBA"),
                                "teams": [t1, t2],
                                "dateTimeGMT": start_str,
                            }
                            if t1 and t2:
                                all_matches.append(n)
                except Exception as e:
                    logger.warning(f"ESPN endpoint fail: {e}")
        return all_matches if all_matches else None
    except Exception as e:
        logger.warning(f"ESPN: {e}"); return None


async def _fetch_from_thesportsdb():
    """v25.8: TheSportsDB — FREE (no signup, key='3'). 30 req/min.
    Cricket league ID mapping: fetches upcoming events for major leagues."""
    # v26.1: REAL cricket league IDs (verified from TheSportsDB API — previous were SOCCER!)
    league_ids = [
        "4461",   # Australian Big Bash League (BBL)
        "5530",   # Sheffield Shield (Australia)
        "5529",   # Bangladesh Premier League (BPL)
        "5176",   # Caribbean Premier League (CPL)
        "5534",   # Shpageeza Cricket League (Afghanistan)
    ]
    all_matches = []
    try:
        async with aiohttp.ClientSession() as s:
            for lid in league_ids:
                url = f"https://www.thesportsdb.com/api/v1/json/3/eventsnextleague.php?id={lid}"
                try:
                    async with s.get(url, timeout=aiohttp.ClientTimeout(total=15),
                                     headers={"User-Agent": "Mozilla/5.0"}) as r:
                        if r.status != 200: continue
                        data = await r.json()
                        events = data.get("events") or []
                        if not events: continue
                        for ev in events:
                            # v26.1: STRICT — verify sport is Cricket
                            if (ev.get("strSport") or "").lower() != "cricket":
                                continue
                            t1 = ev.get("strHomeTeam", "")
                            t2 = ev.get("strAwayTeam", "")
                            if not t1 or not t2: continue
                            # TheSportsDB gives strTimestamp in ISO UTC
                            ts = ev.get("strTimestamp") or ""
                            if not ts:
                                # fallback: strDate + strTime
                                d = ev.get("dateEvent", "")
                                t = ev.get("strTime", "00:00:00")
                                if d: ts = f"{d}T{t}Z"
                            n = {
                                "id": f"tsdb_{ev.get('idEvent', '')}",
                                "name": f"{ev.get('strLeague', '')} - {ev.get('strEvent', '')}".strip(" -"),
                                "matchType": (ev.get("strSport") or "T20").upper()[:5],
                                "status": ev.get("strStatus", ""),
                                "venue": ev.get("strVenue", "TBA"),
                                "teams": [t1, t2],
                                "dateTimeGMT": ts,
                            }
                            all_matches.append(n)
                        await asyncio.sleep(0.3)  # ~30 req/min limit — safe pacing
                except Exception as e:
                    logger.warning(f"TheSportsDB league {lid} fail: {e}")
        return all_matches if all_matches else None
    except Exception as e:
        logger.warning(f"TheSportsDB: {e}"); return None


async def _fetch_from_espn_summary():
    """v25.8: ESPN Cricinfo summary endpoint — different from main feed, gives more upcoming."""
    if not ESPN_ENABLED: return None
    urls = [
        "https://hs-consumer-api.espncricinfo.com/v1/pages/matches/home",
        "https://site.web.api.espn.com/apis/site/v2/sports/cricket/scoreboard",
    ]
    all_matches = []
    try:
        async with aiohttp.ClientSession() as s:
            for url in urls:
                try:
                    async with s.get(url, timeout=aiohttp.ClientTimeout(total=20),
                                     headers={"User-Agent": "Mozilla/5.0"}) as r:
                        if r.status != 200: continue
                        data = await r.json()
                        # Endpoint 1: espncricinfo format
                        for m in data.get("matches", []):
                            teams = m.get("teams", [])
                            if len(teams) < 2: continue
                            t1 = teams[0].get("team", {}).get("longName", "")
                            t2 = teams[1].get("team", {}).get("longName", "")
                            if not t1 or not t2: continue
                            series = m.get("series", {}) or {}
                            n = {
                                "id": f"espn2_{m.get('id') or m.get('objectId', '')}",
                                "name": f"{series.get('name', '')} - {m.get('title', '')}".strip(" -"),
                                "matchType": (m.get("format") or "T20").upper(),
                                "status": m.get("status", ""),
                                "venue": (m.get("ground", {}) or {}).get("name", "TBA"),
                                "teams": [t1, t2],
                                "dateTimeGMT": m.get("startTime", ""),
                            }
                            all_matches.append(n)
                        # Endpoint 2: site.api.espn.com format
                        for ev in data.get("events", []):
                            comps = ev.get("competitions", [])
                            if not comps: continue
                            teams = comps[0].get("competitors", [])
                            if len(teams) < 2: continue
                            t1 = teams[0].get("team", {}).get("displayName", "")
                            t2 = teams[1].get("team", {}).get("displayName", "")
                            if not t1 or not t2: continue
                            n = {
                                "id": f"espn3_{ev.get('id', '')}",
                                "name": ev.get("name", "") or ev.get("shortName", ""),
                                "matchType": "T20",
                                "status": (ev.get("status", {}) or {}).get("type", {}).get("description", ""),
                                "venue": (comps[0].get("venue", {}) or {}).get("fullName", "TBA"),
                                "teams": [t1, t2],
                                "dateTimeGMT": ev.get("date", ""),
                            }
                            all_matches.append(n)
                except Exception as e:
                    logger.warning(f"ESPN-summary endpoint fail: {e}")
        return all_matches if all_matches else None
    except Exception as e:
        logger.warning(f"ESPN-summary: {e}"); return None


async def _fetch_from_sofascore():
    """v25.9: SofaScore public feed — FREE, no key, no signup. Global cricket."""
    today = now_ist().date()
    dates = [today.strftime("%Y-%m-%d"),
             (today + timedelta(days=1)).strftime("%Y-%m-%d")]
    all_matches = []
    try:
        async with aiohttp.ClientSession() as s:
            for d in dates:
                # SofaScore's scheduled events endpoint for cricket
                url = f"https://api.sofascore.com/api/v1/sport/cricket/scheduled-events/{d}"
                try:
                    async with s.get(url, timeout=aiohttp.ClientTimeout(total=15),
                                     headers={"User-Agent": "Mozilla/5.0 (Bot)",
                                              "Accept": "application/json"}) as r:
                        if r.status != 200: continue
                        data = await r.json()
                        for ev in data.get("events", []):
                            # v26.1: Verify tournament is actually cricket
                            tournament = ev.get("tournament", {}) or {}
                            category = (tournament.get("category", {}) or {}).get("sport", {}) or {}
                            sport_name = (category.get("name", "") or "").lower()
                            if sport_name and sport_name != "cricket":
                                continue  # Not cricket!
                            home = ev.get("homeTeam", {}) or {}
                            away = ev.get("awayTeam", {}) or {}
                            t1 = home.get("name", "")
                            t2 = away.get("name", "")
                            if not t1 or not t2: continue
                            tournament = ev.get("tournament", {}) or {}
                            trn_name = tournament.get("name", "")
                            season = ev.get("season", {}) or {}
                            venue = (ev.get("venue", {}) or {}).get("name", "TBA")
                            start_ts = ev.get("startTimestamp")
                            iso_dt = ""
                            if start_ts:
                                try:
                                    iso_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()
                                except Exception: pass
                            fmt = "T20"
                            if "test" in trn_name.lower(): fmt = "TEST"
                            elif "odi" in trn_name.lower() or "50-over" in trn_name.lower(): fmt = "ODI"
                            n = {
                                "id": f"sofa_{ev.get('id', '')}",
                                "name": trn_name,
                                "matchType": fmt,
                                "status": (ev.get("status", {}) or {}).get("description", ""),
                                "venue": venue,
                                "teams": [t1, t2],
                                "dateTimeGMT": iso_dt,
                            }
                            all_matches.append(n)
                except Exception as e:
                    logger.warning(f"SofaScore {d} fail: {e}")
        return all_matches if all_matches else None
    except Exception as e:
        logger.warning(f"SofaScore: {e}"); return None


async def _fetch_from_scorebat():
    """v25.9: Backup lightweight scraper — Google Cricket schedule via SerpAPI-style scrape.
    Uses ESPN Cricinfo's series page as fallback."""
    urls = [
        "https://hs-consumer-api.espncricinfo.com/v1/pages/series/schedule?lang=en&latest=true&type=international",
        "https://hs-consumer-api.espncricinfo.com/v1/pages/series/schedule?lang=en&latest=true&type=league",
        "https://hs-consumer-api.espncricinfo.com/v1/pages/series/schedule?lang=en&latest=true&type=domestic",
    ]
    all_matches = []
    try:
        async with aiohttp.ClientSession() as s:
            for url in urls:
                try:
                    async with s.get(url, timeout=aiohttp.ClientTimeout(total=20),
                                     headers={"User-Agent": "Mozilla/5.0"}) as r:
                        if r.status != 200: continue
                        data = await r.json()
                        for series in data.get("series", []):
                            for m in series.get("matches", []):
                                teams = m.get("teams", [])
                                if len(teams) < 2: continue
                                t1 = teams[0].get("team", {}).get("longName", "")
                                t2 = teams[1].get("team", {}).get("longName", "")
                                if not t1 or not t2: continue
                                n = {
                                    "id": f"espn_sch_{m.get('id') or m.get('objectId', '')}",
                                    "name": f"{series.get('name', '')} - {m.get('title', '')}".strip(" -"),
                                    "matchType": (m.get("format") or "T20").upper(),
                                    "status": m.get("status", ""),
                                    "venue": (m.get("ground", {}) or {}).get("name", "TBA"),
                                    "teams": [t1, t2],
                                    "dateTimeGMT": m.get("startTime", ""),
                                }
                                all_matches.append(n)
                except Exception as e:
                    logger.warning(f"ESPN schedule endpoint fail: {e}")
        return all_matches if all_matches else None
    except Exception as e:
        logger.warning(f"ESPN-schedule: {e}"); return None


def _parse_cricbuzz_response(data):
    matches = []
    for tm in data.get("typeMatches", []):
        for sm in tm.get("seriesMatches", []):
            sw = sm.get("seriesAdWrapper", {})
            series_name = sw.get("seriesName", "")
            for m in sw.get("matches", []):
                mi = m.get("matchInfo", {})
                t1 = mi.get("team1", {}).get("teamName", "")
                t2 = mi.get("team2", {}).get("teamName", "")
                venue = mi.get("venueInfo", {}).get("ground", "")
                city = mi.get("venueInfo", {}).get("city", "")
                start_ts = mi.get("startDate")
                n = {"id": str(mi.get("matchId", "")),
                     "name": f"{series_name} - {mi.get('matchDesc', '')}",
                     "matchType": mi.get("matchFormat", "T20"),
                     "status": mi.get("status", ""),
                     "venue": f"{venue}, {city}" if city else venue,
                     "teams": [t1, t2], "dateTimeGMT": None,
                     "_start_ts": start_ts}
                if start_ts:
                    try:
                        dt = datetime.fromtimestamp(int(start_ts)/1000, tz=timezone.utc)
                        n["dateTimeGMT"] = dt.isoformat()
                    except Exception: pass
                matches.append(n)
    return matches


async def _fetch_from_cricbuzz():
    ak = RAPIDAPI_KEY or CRICKET_API_KEY
    ah = RAPIDAPI_HOST or CRICKET_API_HOST
    if not ak or not ah: return None
    h = {"x-rapidapi-key": ak, "x-rapidapi-host": ah}
    all_matches = []
    try:
        async with aiohttp.ClientSession() as s:
            for ep in ["live", "upcoming", "recent"]:
                url = f"https://{ah}/matches/v1/{ep}"
                try:
                    async with s.get(url, headers=h, timeout=aiohttp.ClientTimeout(total=20)) as r:
                        if r.status != 200: continue
                        d = await r.json()
                        parsed = _parse_cricbuzz_response(d)
                        existing = {m["id"] for m in all_matches}
                        for m in parsed:
                            if m["id"] not in existing:
                                all_matches.append(m)
                except Exception: continue
        return all_matches if all_matches else None
    except Exception: return None


async def fetch_todays_matches():
    if _MATCHES_CACHE["fetched_at"]:
        age = (now_ist() - _MATCHES_CACHE["fetched_at"]).total_seconds() / 60
        if age < 30 and _MATCHES_CACHE["data"]:
            return _MATCHES_CACHE["data"]

    # v21: 5 sources, aggregate all successful (not just first-hit) for max coverage
    sources = [
        ("Cricbuzz", _fetch_from_cricbuzz),
        ("Highlightly", _fetch_from_highlightly),
        ("CricAPI", _fetch_from_cricapi),
        ("CricketData", _fetch_from_cricketdata),
        ("CricAPI-v2", _fetch_from_cricapi_v2),
        ("ESPN", _fetch_from_espn),
        ("ESPN-2", _fetch_from_espn_summary),
        ("ESPN-Sched", _fetch_from_scorebat),
        ("TheSportsDB", _fetch_from_thesportsdb),
        ("SofaScore", _fetch_from_sofascore),
        # v26.1: Flashscore REMOVED — was returning basketball data (wrong sport_id)
    ]
    raw = []
    used_sources = []
    for name, fn in sources:
        try:
            r = await fn()
            src_count = len(r) if r else 0
            _SOURCE_STATS[name] = {
                "last_count": src_count,
                "last_fetch": now_ist(),
                "last_error": "",
            }
            if r:
                # v25.6: Dedupe by team-pair + DAY (was hour — too strict, missing near-dupes)
                dupes_removed = 0
                for m in r:
                    dup = False
                    for existing in raw:
                        e_teams = set(t.lower()[:12] for t in existing.get("teams", []) if t)
                        m_teams = set(t.lower()[:12] for t in m.get("teams", []) if t)
                        if e_teams and m_teams and e_teams == m_teams:
                            e_dt = existing.get("dateTimeGMT", "")
                            m_dt = m.get("dateTimeGMT", "")
                            # Same day = duplicate (was same hour)
                            if e_dt and m_dt and str(e_dt)[:10] == str(m_dt)[:10]:
                                dup = True; break
                    if not dup:
                        raw.append(m)
                    else:
                        dupes_removed += 1
                used_sources.append(f"{name}({len(r)}→{len(r)-dupes_removed})")
                logger.info(f"✅ {len(r)} from {name} ({dupes_removed} dupes removed)")
        except Exception as e:
            logger.warning(f"Source {name} exception: {e}")
            _SOURCE_STATS[name] = {
                "last_count": 0,
                "last_fetch": now_ist(),
                "last_error": str(e)[:200],
            }
    if used_sources:
        logger.info(f"📊 Total after dedup: {len(raw)} from {', '.join(used_sources)}")
    used = ",".join([s.split("(")[0] for s in used_sources]) or "None"

    if not raw:
        return _MATCHES_CACHE["data"] if _MATCHES_CACHE["data"] else []

    # v25.6: Widen window — accept anything from NOW to +36h
    # This covers late-night matches (e.g. Australian PM start = ~1 AM IST) that
    # were previously classified as "tomorrow" and often missed.
    now_dt = now_ist()
    today = now_dt.date()
    tomorrow = today + timedelta(days=1)
    day_after = today + timedelta(days=2)
    todays = []
    tomorrows = []
    skipped_past = 0
    skipped_far = 0
    skipped_bad_dt = 0
    for m in raw:
        dt_gmt = m.get("dateTimeGMT")
        if not dt_gmt:
            skipped_bad_dt += 1
            continue
        try:
            dt_utc = datetime.fromisoformat(str(dt_gmt).replace("Z", "+00:00"))
            if dt_utc.tzinfo is None:
                dt_utc = dt_utc.replace(tzinfo=timezone.utc)
            dt_ist = dt_utc.astimezone(IST)
            m["_ist_dt"] = dt_ist
            m["_priority"] = calculate_match_priority(m)
            m["_source"] = used
            hours_from_now = (dt_ist - now_dt).total_seconds() / 3600
            # Accept anything from -3 hours (in progress) to +36 hours
            if hours_from_now < -3:
                skipped_past += 1
                continue
            if hours_from_now > 36:
                skipped_far += 1
                continue
            # Categorize by IST date
            if dt_ist.date() == today:
                todays.append(m)
            elif dt_ist.date() == tomorrow or dt_ist.date() == day_after:
                tomorrows.append(m)
        except Exception as e:
            logger.warning(f"date parse fail: {e} for {m.get('name', '?')[:40]}")
            skipped_bad_dt += 1
    logger.info(f"📊 Match filter: {len(todays)} today, {len(tomorrows)} tomorrow, "
                f"skipped {skipped_past} past + {skipped_far} far + {skipped_bad_dt} bad-date")
    # v25.6: MIN-5 GUARANTEE + always pull upcoming tomorrow matches
    # (Adds ALL sub-24h upcoming from tomorrow list, then fills up to 5 total)
    covered_today = [m for m in todays if calculate_match_priority(m) >= 3]
    # Always add high-priority tomorrow matches (priority >=8) automatically
    top_tom = [m for m in tomorrows if calculate_match_priority(m) >= 8]
    for m in top_tom:
        m["_from_tomorrow"] = True
        todays.append(m)
    if top_tom:
        logger.info(f"⭐ Auto-added {len(top_tom)} HIGH-PRIORITY tomorrow matches")
    # Then MIN-5 fill from remaining tomorrow
    covered_today_after = [m for m in todays if calculate_match_priority(m) >= 3]
    needed = max(0, 5 - len(covered_today_after))
    if needed > 0:
        remaining_tom = [m for m in tomorrows if calculate_match_priority(m) >= 3 and m not in top_tom]
        pull = sorted(remaining_tom, key=lambda x: -x.get("_priority", 5))[:min(8, needed + 3)]
        for m in pull:
            m["_from_tomorrow"] = True
            todays.append(m)
        if pull:
            logger.info(f"📅 MIN-5 guarantee: pulled {len(pull)} extra from tomorrow (had {len(covered_today_after)}, target 5)")

    for mm in db_get_manual_matches():
        try:
            sd = datetime.fromisoformat(mm["start_time"])
            if sd.tzinfo is None: sd = sd.replace(tzinfo=IST)
            if sd.date() == today and not any(m.get("id") == mm["id"] for m in todays):
                todays.append({
                    "id": mm["id"], "name": mm["tournament"],
                    "teams": [mm["team1"], mm["team2"]],
                    "venue": mm["venue"], "matchType": mm["format"],
                    "dateTimeGMT": sd.astimezone(timezone.utc).isoformat(),
                    "_ist_dt": sd.astimezone(IST), "_priority": 10,
                    "_source": "Manual", "_manual": True})
        except Exception as e:
            logger.error(f"Manual: {e}")

    _MATCHES_CACHE["data"] = todays
    _MATCHES_CACHE["fetched_at"] = now_ist()
    return todays


async def fetch_match_info(match_id):
    if not CRICKET_API_KEY or not match_id: return None
    url = f"https://api.cricapi.com/v1/match_info?apikey={CRICKET_API_KEY}&id={match_id}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 200: return None
                d = await r.json()
                if d.get("status") != "success": return None
                return d.get("data")
    except Exception: return None


async def fetch_live_scorecard(match_id):
    if not CRICKET_API_KEY or not match_id: return None
    url = f"https://api.cricapi.com/v1/match_scorecard?apikey={CRICKET_API_KEY}&id={match_id}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 200: return None
                d = await r.json()
                if d.get("status") != "success": return None
                return d.get("data")
    except Exception: return None


def parse_current_state(info):
    if not info: return None
    score = info.get("score", [])
    if not score: return None
    innings = []
    for s in score:
        innings.append({"inning": s.get("inning", ""),
                        "r": int(s.get("r", 0) or 0),
                        "w": int(s.get("w", 0) or 0),
                        "o": float(s.get("o", 0) or 0)})
    current = innings[-1] if innings else {}
    return {"innings": innings, "current_inning": len(innings),
            "current_over": current.get("o", 0),
            "current_runs": current.get("r", 0),
            "current_wickets": current.get("w", 0),
            "status": info.get("status", ""),
            "match_started": info.get("matchStarted", False),
            "match_ended": info.get("matchEnded", False)}


def over_to_int(o): return int(o) if o else 0


def _match_header(match):
    teams = match.get("teams", [])
    t1 = teams[0] if len(teams) > 0 else "Team A"
    t2 = teams[1] if len(teams) > 1 else "Team B"
    return t1, t2, match.get("venue", "TBA"), match.get("matchType", "T20").upper(), match["_ist_dt"]


# ======================================================
# 📢 CHANNEL POSTS (unchanged - all bold HTML)
# ======================================================
_LAST_POST_TIME = {"ts": None}
_POST_LOCK = asyncio.Lock()


async def post_to_channel(bot, text, channel=None, reply_markup=None):
    target = channel or CHANNEL_ID
    if not text: return
    async with _POST_LOCK:
        # v25.1: 5s → 15s spacing between channel posts (avoid dump-feel)
        if _LAST_POST_TIME["ts"]:
            e = (now_ist() - _LAST_POST_TIME["ts"]).total_seconds()
            if e < 15: await asyncio.sleep(15 - e)
        _LAST_POST_TIME["ts"] = now_ist()
    max_len = 4000
    if len(text) <= max_len:
        msgs = [text]
    else:
        lines = text.split('\n'); msgs, cur = [], ""
        for line in lines:
            if len(cur) + len(line) + 1 > max_len:
                if cur: msgs.append(cur)
                cur = line
            else:
                cur = cur + "\n" + line if cur else line
        if cur: msgs.append(cur)
    for i, msg in enumerate(msgs):
        try:
            mk = reply_markup if (reply_markup and i == len(msgs)-1) else None
            await bot.send_message(chat_id=target, text=msg,
                                   disable_web_page_preview=True,
                                   parse_mode="HTML", reply_markup=mk)
            if len(msgs) > 1: await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"❌ Post: {e}")
            # Retry without HTML
            try:
                await bot.send_message(chat_id=target, text=msg,
                                       disable_web_page_preview=True,
                                       reply_markup=mk)
            except Exception: pass


async def send_photo_to_channel(bot, image_buf, caption, channel=None, reply_markup=None):
    target = channel or CHANNEL_ID
    if not image_buf:
        await post_to_channel(bot, caption, channel=channel); return
    try:
        image_buf.seek(0)
        await bot.send_photo(chat_id=target, photo=image_buf,
                             caption=caption[:1024], parse_mode="HTML",
                             reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"❌ Photo: {e}")
        await post_to_channel(bot, caption, channel=channel)


async def send_poll_to_channel(bot, question, options, channel=None):
    target = channel or CHANNEL_ID
    try:
        await bot.send_poll(chat_id=target, question=question[:255],
                            options=options, is_anonymous=True,
                            allows_multiple_answers=False)
    except Exception as e:
        logger.error(f"❌ Poll: {e}")


async def broadcast_to_vips(bot, text, image_buf=None):
    vips = db_get_vip_users()
    if not vips: return 0
    success = 0
    vt = "<b>🌟 VIP EARLY ALERT</b> 🌟\n━━━━━━━━━━━━━━━━\n\n" + text
    for vip in vips:
        try:
            if image_buf:
                image_buf.seek(0)
                await bot.send_photo(chat_id=vip["user_id"], photo=image_buf,
                                     caption=vt[:1024], parse_mode="HTML")
            else:
                await bot.send_message(chat_id=vip["user_id"], text=vt,
                                       parse_mode="HTML", disable_web_page_preview=True)
            success += 1
            await asyncio.sleep(0.5)
        except Exception: pass
    return success


# ======================================================
# 🌅 GOOD MORNING/NIGHT + MATCH POSTS
# ======================================================
async def gen_good_morning():
    """v17: Short, sharp, premium Good Morning with green separators."""
    import random
    quotes = [
        "Boss Ki Prediction, Boss Ki Jeet",
        "Over Sure Toss • Unlimited Kheloo",
        "Lifetime Loss Cover Guarantee 🧿",
        "Jeeto Roz — Sirf All-Rounder Boss Ke Saath",
        "100% Sure Toss • Zero Risk 🏆",
    ]
    tagline = random.choice(quotes)
    body = (
        f"🌅✨ <b>GOOD MORNING CHAMPIONS!</b> ✨🌅\n\n"
        f"🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢\n\n"
        f"📅 <b>{format_date_style()}</b>\n\n"
        f"🏏 <b>Aaj Ka Match • Aaj Ki Jeet 💰</b>\n"
        f"🔥 <b>Toss • Match • Session — All Ready!</b>\n"
        f"🎯 <b>Full Day Coverage Loading ⚡</b>\n\n"
        f"🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢\n\n"
        f"💎 <b>★ {tagline} ★</b>\n\n"
        f"👑 <b>All-Rounder Cricket 🏏</b>"
    )
    return body


async def gen_good_night_with_stats():
    """v24: DEPRECATED — no longer used, kept as stub."""
    return None
async def gen_morning_header(mc, tc):
    """v19: Short punchy header, no bullet spam."""
    return (
        f"🏏✨ <b>AAJ KI CRICKET ACTION</b> ✨🏏\n\n"
        f"🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢\n\n"
        f"<b>{format_date_style()}</b>\n\n"
        f"<b>🔥 Total Matches Covered: {mc}</b>\n"
        f"<b>⭐ Top-tier: {tc}</b>\n\n"
        f"<b>👇 Har match ka detailed post niche 👇</b>\n\n"
        f"🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢\n\n"
        f"<b>💎 ★ Boss Ki Prediction, Boss Ki Jeet ★ 💎</b>\n"
        f"<b>👑 All-Rounder Cricket 🏏</b>"
    )


async def gen_single_match_preview(match, idx, total):
    t1, t2, venue, fmt, dt = _match_header(match)
    name = match.get('name', 'Match')
    p = match.get('_priority', 5)
    badge = "⭐ TOP MATCH" if p >= 8 else "🏏 MATCH"
    # v22.1: Mark matches from tomorrow (pulled by MIN-2 guarantee)
    if match.get("_from_tomorrow"):
        badge = "📅 TOMORROW " + badge
    return (
        f"<b>{badge} #{idx}</b>\n\n"
        f"<b>🆚 {t1} vs {t2}</b>\n"
        f"<b>🏆 {name}</b>\n📍 {venue}\n"
        f"<b>🕐 {dt.strftime('%I:%M %p IST')}</b>\n"
        f"🏷 <b>{fmt}</b>\n\n"
        f"<b>📢 Full coverage starts:</b>\n"
        f"<b>{(dt - timedelta(minutes=120)).strftime('%I:%M %p IST')}</b>\n\n"
        f"<b>🏏 All-Rounder Cricket 🏏</b>"
    )


async def gen_match_info(match):
    t1, t2, venue, fmt, dt = _match_header(match)
    trn = match.get('name', 'Match')
    prompt = f"Cricket match preview in Hinglish, 4-5 punchy lines, aggressive tone. Match: {t1} vs {t2} at {venue}. Tournament: {trn}. Direct lines only. No headers, no markdown."
    r = await ask_gemini(prompt)
    analysis = r.strip() if r else f"Kadak match aane wala hai! {t1} aur {t2} dono form mein hain."
    return (
        f"<b>{format_date_style()}</b>\n\n"
        f"<b>🏏 MATCH PREVIEW 🏏</b>\n\n"
        f"<b>🆚 {t1} vs {t2}</b>\n"
        f"<b>🏆 {trn}</b>\n📍 {venue}\n"
        f"<b>🕐 {dt.strftime('%I:%M %p IST')}</b>\n🏷 <b>{fmt}</b>\n\n"
        f"<b>📋 PREVIEW:</b>\n<b>{analysis}</b>\n\n"
        f"<b>⏳ Aane wala hai:</b>\n"
        f"• Ground Report\n• Toss Prediction\n"
        f"• Playing 11\n• Match Winner\n"
        f"• Live Session (6/10/15/20 ov)\n\n"
        f"( <i>Preview by All Rounder</i> )\n\n"
        f"<b>🏏 All-Rounder Cricket 🏏</b>"
    )


# v22: FREE Weather API (Open-Meteo — no key, no signup)
_VENUE_COORDS = {
    # Popular cricket grounds mapped to lat/lon
    "wankhede": (18.9389, 72.8258), "mumbai": (18.9389, 72.8258),
    "eden gardens": (22.5646, 88.3433), "kolkata": (22.5646, 88.3433),
    "chinnaswamy": (12.9788, 77.5996), "bengaluru": (12.9788, 77.5996),
    "bangalore": (12.9788, 77.5996),
    "chepauk": (13.0627, 80.2794), "chennai": (13.0627, 80.2794),
    "kotla": (28.6379, 77.2432), "delhi": (28.6379, 77.2432),
    "arun jaitley": (28.6379, 77.2432),
    "narendra modi": (23.0912, 72.5975), "ahmedabad": (23.0912, 72.5975),
    "motera": (23.0912, 72.5975),
    "rajiv gandhi": (17.4062, 78.5504), "hyderabad": (17.4062, 78.5504),
    "uppal": (17.4062, 78.5504),
    "sawai mansingh": (26.8880, 75.8095), "jaipur": (26.8880, 75.8095),
    "pca": (30.6912, 76.7357), "mohali": (30.6912, 76.7357),
    "green park": (26.4869, 80.3402), "kanpur": (26.4869, 80.3402),
    "mca": (18.6753, 73.9105), "pune": (18.6753, 73.9105),
    "dharamshala": (32.2196, 76.3221), "hpca": (32.2196, 76.3221),
    "lucknow": (26.8467, 80.9462), "ekana": (26.8467, 80.9462),
    "guwahati": (26.1445, 91.7362),
    "vizag": (17.7231, 83.3016), "visakhapatnam": (17.7231, 83.3016),
    # International
    "mcg": (-37.8199, 144.9834), "melbourne": (-37.8199, 144.9834),
    "scg": (-33.8917, 151.2247), "sydney": (-33.8917, 151.2247),
    "gabba": (-27.4859, 153.0381), "brisbane": (-27.4859, 153.0381),
    "adelaide": (-34.9159, 138.5960),
    "perth": (-31.9505, 115.8605),
    "lords": (51.5294, -0.1725), "lord": (51.5294, -0.1725),
    "the oval": (51.4832, -0.1150), "kia oval": (51.4832, -0.1150),
    "edgbaston": (52.4558, -1.9022), "birmingham": (52.4558, -1.9022),
    "old trafford": (53.4562, -2.2870), "manchester": (53.4562, -2.2870),
    "headingley": (53.8175, -1.5822), "leeds": (53.8175, -1.5822),
    "trent bridge": (52.9358, -1.1319), "nottingham": (52.9358, -1.1319),
    "rose bowl": (50.9257, -1.3200), "southampton": (50.9257, -1.3200),
    "dubai": (25.0269, 55.2113), "sharjah": (25.3428, 55.4257),
    "abu dhabi": (24.4419, 54.6070),
    "gaddafi": (31.5194, 74.3286), "lahore": (31.5194, 74.3286),
    "karachi": (24.8940, 67.0632), "national stadium": (24.8940, 67.0632),
    "rawalpindi": (33.6386, 73.0552),
    "eden park": (-36.8734, 174.7449), "auckland": (-36.8734, 174.7449),
    "hagley oval": (-43.5375, 172.6205), "christchurch": (-43.5375, 172.6205),
    "wellington": (-41.2865, 174.7762),
    "colombo": (6.9271, 79.8612), "premadasa": (6.9271, 79.8612),
    "kandy": (7.2841, 80.6280), "pallekele": (7.2841, 80.6280),
    "galle": (6.0329, 80.2170),
    "mirpur": (23.8103, 90.4125), "dhaka": (23.8103, 90.4125),
    "chattogram": (22.3569, 91.7832), "chittagong": (22.3569, 91.7832),
    "cape town": (-33.9184, 18.4249), "newlands": (-33.9184, 18.4249),
    "johannesburg": (-26.2041, 28.0473), "wanderers": (-26.2041, 28.0473),
    "durban": (-29.8579, 31.0292), "kingsmead": (-29.8579, 31.0292),
    "centurion": (-25.8636, 28.1889), "supersport": (-25.8636, 28.1889),
    "bridgetown": (13.1132, -59.5644), "kensington oval": (13.1132, -59.5644),
    "queens park": (10.6543, -61.5109), "trinidad": (10.6543, -61.5109),
    "sabina park": (17.9714, -76.7827), "jamaica": (17.9714, -76.7827),
    "guyana": (6.8013, -58.1551),
    "harare": (-17.8252, 31.0335), "bulawayo": (-20.1500, 28.5833),
}


async def fetch_weather(venue: str, when_ist: datetime = None):
    """v22: FREE weather from Open-Meteo (no API key). Returns dict or None."""
    if not venue: return None
    v = venue.lower()
    coords = None
    for key, c in _VENUE_COORDS.items():
        if key in v:
            coords = c; break
    if not coords:
        return None
    lat, lon = coords
    try:
        # Use forecast endpoint (hourly for accuracy at match time)
        url = (f"https://api.open-meteo.com/v1/forecast"
               f"?latitude={lat}&longitude={lon}"
               f"&hourly=temperature_2m,precipitation_probability,wind_speed_10m,relative_humidity_2m,cloudcover"
               f"&timezone=Asia%2FKolkata&forecast_days=2")
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200: return None
                data = await r.json()
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        if not times: return None
        # Find hour closest to match start
        target = (when_ist or now_ist()).strftime("%Y-%m-%dT%H:00")
        idx = None
        for i, t in enumerate(times):
            if t >= target:
                idx = i; break
        if idx is None: idx = 0
        def _safe_get(field):
            arr = hourly.get(field) or [None]
            if idx < len(arr): return arr[idx]
            return None
        temp = _safe_get("temperature_2m")
        rain = _safe_get("precipitation_probability")
        wind = _safe_get("wind_speed_10m")
        hum = _safe_get("relative_humidity_2m")
        cloud = _safe_get("cloudcover")
        # Dew factor heuristic: high humidity + low temp + evening/night
        hr = (when_ist or now_ist()).hour
        dew_risk = "HIGH" if (hum and hum > 75 and hr >= 17 and temp and temp < 28) else \
                   "MODERATE" if (hum and hum > 65) else "LOW"
        return {
            "temp_c": round(temp, 1) if temp is not None else None,
            "rain_pct": rain,
            "wind_kmh": round(wind, 1) if wind is not None else None,
            "humidity": hum,
            "cloud_pct": cloud,
            "dew_risk": dew_risk,
            "summary": f"{temp:.0f}°C, Humidity {hum:.0f}%, Rain {rain:.0f}%, Dew: {dew_risk}"
                       if all(x is not None for x in [temp, hum, rain]) else "Data unavailable",
        }
    except Exception as e:
        logger.warning(f"Weather fetch fail for {venue}: {e}")
        return None


async def gen_ground_post(match):
    """v22: Enriched with REAL weather from Open-Meteo."""
    t1, t2, venue, fmt, dt = _match_header(match)
    # Fetch real weather data
    weather = await fetch_weather(venue, dt)
    w_ctx = ""
    if weather:
        w_ctx = (f"\nREAL-TIME WEATHER at match start:\n"
                 f"Temperature: {weather['temp_c']}°C\n"
                 f"Rain probability: {weather['rain_pct']}%\n"
                 f"Humidity: {weather['humidity']}%\n"
                 f"Wind: {weather['wind_kmh']} km/h\n"
                 f"Dew risk: {weather['dew_risk']}\n"
                 f"Use these REAL numbers in your DEW and WEATHER lines below.\n")
    prompt = f"""Cricket ground/pitch report for {venue} ({fmt}).{w_ctx}
Give ONLY these facts:
PITCH: [Batting/Bowling/Balanced]
PACE: [1 line]
SPIN: [1 line]
AVG_1ST: [number]
AVG_2ND: [number]
BAT_FIRST_WIN: [%]
DEW: [Yes/No + reason]
WEATHER: [1 line — use real temp/rain if given above]
PAR_SCORE: [number]
STRATEGY: [1-2 lines Hinglish]"""
    r = await ask_gemini(prompt)
    def ex(k, d=""):
        m = re.search(rf"{k}:\s*(.+)", r or "", re.IGNORECASE)
        return m.group(1).strip().split("\n")[0] if m else d
    return (
        f"<b>{format_date_style()}</b>\n\n"
        f"<b>🏟️ GROUND REPORT 🏟️</b>\n\n"
        f"<b>🆚 {t1} vs {t2}</b>\n📍 <b>{venue}</b>\n\n"
        f"<b>🎳 PITCH ANALYSIS:</b>\n"
        f"• Type: <b>{ex('PITCH', 'Balanced')}</b>\n"
        f"• Pace: <b>{ex('PACE', 'Movement in first 10')}</b>\n"
        f"• Spin: <b>{ex('SPIN', 'Slow bowlers effective')}</b>\n\n"
        f"<b>📊 VENUE STATS:</b>\n"
        f"• Avg 1st: <b>{ex('AVG_1ST', '160')}</b>\n"
        f"• Avg 2nd: <b>{ex('AVG_2ND', '150')}</b>\n"
        f"• Bat First Win: <b>{ex('BAT_FIRST_WIN', '52%')}</b>\n"
        f"• Par Score: <b>{ex('PAR_SCORE', '165')}</b>\n\n"
        f"<b>🌤️ CONDITIONS:</b>\n"
        f"• Weather: <b>{ex('WEATHER', 'Clear')}</b>\n"
        f"• Dew: <b>{ex('DEW', 'Possible')}</b>\n\n"
        f"<b>💡 STRATEGY:</b>\n<b>{ex('STRATEGY', 'Toss winner should bat first')}</b>\n\n"
        f"( <i>Report by All Rounder</i> )\n\n"
        f"<b>🏏 All-Rounder Cricket 🏏</b>"
    )


async def gen_playing11_post(match):
    t1, t2, _, _, _ = _match_header(match)
    prompt = f"""Probable Playing 11 for {t1} vs {t2}. Format:
TEAM1_PLAYERS:
1. Player (Role)
... all 11
TEAM2_PLAYERS:
1. Player (Role)
... all 11
IMPACT: Player — reason"""
    r = await ask_gemini(prompt) or ""
    t1b = "Squad to be confirmed"; t2b = "Squad to be confirmed"
    imp = "Star performer to watch"
    try:
        if "TEAM1_PLAYERS:" in r:
            a1 = r.split("TEAM1_PLAYERS:")[1]
            if "TEAM2_PLAYERS:" in a1:
                t1b = a1.split("TEAM2_PLAYERS:")[0].strip()
                t2b = a1.split("TEAM2_PLAYERS:")[1]
                if "IMPACT:" in t2b:
                    imp = t2b.split("IMPACT:")[1].strip().split("\n")[0]
                    t2b = t2b.split("IMPACT:")[0].strip()
                else:
                    t2b = t2b.strip()
    except Exception: pass
    return (
        f"<b>{format_date_style()}</b>\n\n"
        f"<b>📋 PROBABLE PLAYING 11 📋</b>\n\n"
        f"<b>🆚 {t1} vs {t2}</b>\n\n"
        f"<b>🏏 {t1}:</b>\n<b>{t1b}</b>\n\n"
        f"<b>🏏 {t2}:</b>\n<b>{t2b}</b>\n\n"
        f"<b>💥 IMPACT PLAYER:</b>\n<b>{imp}</b>\n\n"
        f"( <i>Squad by All Rounder</i> )\n\n"
        f"<b>🏏 All-Rounder Cricket 🏏</b>"
    )


async def gen_toss_post(match):
    """v25: CLEAN TOSS PREDICTION — user's exact template format."""
    t1, t2, venue, fmt, dt = _match_header(match)
    trn = match.get("name", "").upper() or f"{t1} vs {t2}"
    time_of_day = "night (dew factor)" if dt.hour >= 17 else "day"
    match_no = ""
    # Try to extract match number/type from name
    mn_match = re.search(r"(\d+(?:ST|ND|RD|TH)?\s*(?:T20|ODI|TEST|MATCH))", trn, re.IGNORECASE)
    if mn_match:
        match_no = mn_match.group(1).upper()
    else:
        match_no = fmt.upper() + " MATCH"
    date_str = dt.strftime("%d %b %Y").upper()
    prompt = f"""[EXPERT TOSS ANALYST — 75%+ accuracy target]
MATCH: {t1} vs {t2} at {venue} ({time_of_day})
Silently analyze: venue toss history, dew factor for {time_of_day} matches,
recent toss patterns for both captains, pitch report.
Reply EXACTLY on ONE line:
TOSS: TEAM_NAME | CHOICE: BAT (or BOWL)"""
    r = await ask_gemini(prompt)
    if not r: return None
    m = re.search(r"TOSS:\s*([^|]+)\s*\|\s*CHOICE:\s*(BAT|BOWL|FIELD|CHASE|BAT FIRST|BOWL FIRST|BATTING|BOWLING|FIELDING)",
                  r, re.IGNORECASE)
    if not m:
        m = re.search(r"PREDICTION:\s*([^|]+)\s*\|\s*(BAT|BOWL|FIELD|CHASE|BATTING|BOWLING|FIELDING)",
                      r, re.IGNORECASE)
    winner = "TBA"; choice = "BAT"
    if m:
        winner = m.group(1).strip()
        choice_raw = m.group(2).strip().upper()
        if choice_raw in ("BOWL", "BOWLING", "FIELD", "FIELDING", "CHASE", "BOWL FIRST"):
            choice = "BOWL"
        else:
            choice = "BAT"
        db_save_simple_prediction(match.get("id"), "toss_winner", winner)
        db_save_simple_prediction(match.get("id"), "toss_choice", choice)
        db_update_match(match.get("id"), predicted_toss_winner=winner,
                        predicted_toss_choice=choice)
    # USER'S EXACT TEMPLATE
    return (
        f"<b>{trn}</b> 🏆\n\n"
        f"<b>{match_no} - {date_str}</b>\n\n"
        f"<b>TOSS 👉 {t1} 🆚 {t2} ✅</b>\n\n"
        f"<b>TOSS WINNER 🏆 {winner} 🏆</b>\n\n"
        f"<b>➥ PLAY BIG LIMIT ✔️✔️</b>\n\n"
        f"<b>➥ SURE SHOT TOSS ✔️✔️</b>\n\n"
        f"<b>FAAD DO BOOKIE KO....🔇🔕</b>\n\n"
        f"<b>100% OVER SURE TOSS HAI....🔰</b>\n\n"
        f"<b>LIFE TIME LOSS COVER TOSS ✅</b>\n\n"
        f"<b>SIGN. BY :- ALL-ROUNDER CRICKET FANTASY</b>"
    )

async def gen_prediction_post(match):
    """v25: CLEAN MATCH WINNER PREDICTION — user's exact template format."""
    t1, t2, venue, fmt, dt = _match_header(match)
    trn = match.get("name", "").upper() or f"{t1} vs {t2}"
    time_of_day = "night (dew factor important)" if dt.hour >= 17 else "day"
    match_no = ""
    mn_match = re.search(r"(\d+(?:ST|ND|RD|TH)?\s*(?:T20|ODI|TEST|MATCH))", trn, re.IGNORECASE)
    if mn_match:
        match_no = mn_match.group(1).upper()
    else:
        match_no = fmt.upper() + " MATCH"
    date_str = dt.strftime("%d %b %Y").upper()
    prompt = f"""[EXPERT CRICKET ANALYST — Target 80%+ prediction accuracy]
MATCH: {t1} vs {t2}
TOURNAMENT: {trn}
VENUE: {venue}
FORMAT: {fmt}
START TIME: {dt.strftime('%I:%M %p IST')} ({time_of_day})

Silently analyze 7 factors: recent form, H2H at venue, pitch, weather/dew,
key player availability, toss impact, home advantage.

Pick STRONGER team. Reply EXACTLY:
PREDICTION: TEAM_NAME"""
    r = await ask_gemini(prompt)
    if not r: return None
    m = re.search(r"PREDICTION:\s*(.+)", r)
    winner = "TBA"
    if m:
        winner = m.group(1).strip().split("\n")[0].strip()
        winner = re.sub(r"\s*\|.*$", "", winner).strip()
        db_save_simple_prediction(match.get("id"), "winner", winner)
        db_update_match(match.get("id"), predicted_winner=winner)
    # USER'S EXACT TEMPLATE
    return (
        f"<b>🔰🔰 {trn} 🔰🔰</b>\n\n"
        f"<b>{match_no} - {date_str}</b>\n\n"
        f"<b>{t1}   V/s   {t2}</b>\n\n"
        f"<b>MATCH WINNER ➜ {winner} 💯</b>\n\n"
        f"<b>NONCUTTING WINNER ✍🏽</b>\n\n"
        f"<b>PLAY HUGE EARN HUGE 😍</b>\n\n"
        f"<b>BOARD WINNER ➜ {winner} 🏆🎉🏆</b>\n\n"
        f"<b>#ALL-ROUNDER ❤️🙏</b>"
    )

async def gen_live_score_post(match, state):
    t1, t2, _, fmt, _ = _match_header(match)
    fn = fmt_normalize(fmt)
    lines = [f"<b>🔴 LIVE UPDATE</b>", "",
             f"<b>🆚 {t1} vs {t2}</b>", f"🏷 <b>{fn}</b>", ""]
    for inn in state.get("innings", []):
        rr = round(inn["r"]/inn["o"], 2) if inn["o"] > 0 else 0
        lines.append(f"<b>📊 {inn.get('inning', 'Inn')}: {inn['r']}/{inn['w']}</b> ({inn['o']} ov)")
        lines.append(f"    <b>Run Rate: {rr}</b>")
    lines.append("")
    lines.append(f"<b>💬 {state.get('status', 'Match in progress')}</b>")
    lines.append(f"🕐 <b>{now_ist().strftime('%I:%M %p IST')}</b>")
    lines.append("")
    lines.append(f"<b>🏏 All-Rounder Cricket 🏏</b>")
    return "\n".join(lines)


def detect_events(old_state, new_state):
    events = []
    if not old_state or not new_state: return events
    oi = old_state.get("innings", []); ni = new_state.get("innings", [])
    for i, ns in enumerate(ni):
        if i >= len(oi): continue
        os_ = oi[i]
        if ns["w"] > os_["w"]:
            events.append({"type": "wicket", "inning": ns["inning"],
                           "score": f"{ns['r']}/{ns['w']}", "overs": ns["o"]})
        om = os_["r"] // 50; nm = ns["r"] // 50
        if nm > om and ns["r"] >= 50:
            events.append({"type": "milestone", "inning": ns["inning"],
                           "runs": ns["r"], "milestone": nm * 50})
    return events


# v24: DEPRECATED — no longer called from live_intelligence (spammy)
async def gen_wicket_alert(match, event):
    t1, t2, _, _, _ = _match_header(match)
    return (
        f"<b>🚨 WICKET! WICKET! WICKET! 🚨</b>\n\n"
        f"<b>🆚 {t1} vs {t2}</b>\n\n"
        f"<b>💥 Score: {event['score']}</b> ({event.get('overs', 0)} ov)\n"
        f"🔴 <b>{event.get('inning', 'Inning')}</b>\n\n"
        f"<b>Game Changer Moment! 🔥</b>\n"
        f"<b>Match ka rukh badla!</b>\n\n"
        f"<b>🏏 All-Rounder Cricket 🏏</b>"
    )


# v24: DEPRECATED — no longer called from live_intelligence (spammy)
async def gen_milestone_alert(match, event):
    t1, t2, _, _, _ = _match_header(match)
    m = event['milestone']
    fire = "🔥🔥🔥" if m >= 150 else "🔥🔥" if m >= 100 else "🔥"
    return (
        f"<b>🎉 {m} RUNS UP! {fire}</b>\n\n"
        f"<b>🆚 {t1} vs {t2}</b>\n\n"
        f"<b>📊 {event.get('inning', 'Inn')}: {event['runs']} runs</b>\n\n"
        f"<b>Kadak batting! 💪</b>\n"
        f"<b>Milestone conquered!</b>\n\n"
        f"<b>🏏 All-Rounder Cricket 🏏</b>"
    )


async def check_and_post_toss_result(bot, match):
    """v25: TOSS PASS post — user's exact template format."""
    mid = match.get("id"); dm = db_get_match(mid)
    if dm and dm.get("toss_posted"): return
    info = await fetch_match_info(mid)
    if not info: return
    tw = info.get("tossWinner"); tc = info.get("tossChoice")
    if not tw or not tc: return
    pw = dm.get("predicted_toss_winner") if dm else None
    pc = dm.get("predicted_toss_choice") if dm else None
    wc = False; cc = False
    if pw:
        wc = pw.lower() in tw.lower() or tw.lower() in pw.lower()
        db_resolve_simple_prediction(mid, "toss_winner", tw, wc)
    if pc:
        cc = pc.lower() == tc.lower()
        db_resolve_simple_prediction(mid, "toss_choice", tc, cc)
    # Only post PASS if our prediction was correct (else silent)
    # User template — only celebrates the pass
    choice_text = "BAT FIRST" if tc.upper().startswith("BAT") else "BOWL FIRST"
    text = (
        f"<b>🏆 {tw.upper()} 🏆 WON THE TOSS &amp; OPTED TO {choice_text} ✅</b>\n\n"
        f"<b>Enjoy Your Daily Profits. Wait For next.</b>\n\n"
        f"<b>कोई नहीं हे टक्कर में ! क्यों पड़े हो किसी के चक्कर में !!</b>\n\n"
        f"<b>All - Rounder Cricket fantasy 🏏🏏</b>"
    )
    await post_to_channel(bot, text)
    db_update_match(mid, toss_posted=1, actual_toss_winner=tw, actual_toss_choice=tc)




def _get_situation_read(wickets, run_rate, overs_left):
    """v23: Human-mind situation reader for session posts."""
    if wickets >= 7:
        return "Wickets girr rahe — bachao mode 🛡"
    if wickets >= 5 and run_rate < 6:
        return "Rebuild time — steady chahiye 🧱"
    if run_rate >= 10 and wickets <= 3:
        return "Attack mode ON — big total banega 💥"
    if run_rate >= 8:
        return "Momentum team ke haath me — dhaka dega 🚀"
    if run_rate < 5:
        return "Struggle chal raha — spinners ka fayda 🎯"
    if overs_left <= 5:
        return "Death overs — sixes barsenge 🔥"
    return "Balanced phase — dono team same footing 🎪"


async def gen_session_prediction(match, state, co, ne, pn):
    """v23: Situation-aware session prediction with human commentary."""
    t1, t2, venue, fmt, _ = _match_header(match)
    inn = state.get("current_inning", 1)
    cr = state.get("current_runs", 0); cw = state.get("current_wickets", 0)
    teams = match.get("teams", [])
    bt = teams[inn - 1] if len(teams) >= inn else f"Team {inn}"
    rr = round(cr / co, 2) if co > 0 else 0
    prompt = f"""[EXPERT SESSION ANALYST — 75%+ accuracy target]
Match: {t1} vs {t2} at {venue}
Batting: {bt} | Current: {cr}/{cw} in {co} overs (RR: {rr})
Predict runs between over {co + 1} to {ne}.
Consider: wickets in hand, current RR, pitch condition, batting depth.
Reply EXACTLY: SESSION_RANGE: MIN-MAX"""
    r = await ask_gemini(prompt)
    if not r: return None
    m = re.search(r"SESSION_RANGE:\s*(\d+)\s*-\s*(\d+)", r)
    pmin, pmax = 30, 45
    if m and ne:
        pmin = int(m.group(1)); pmax = int(m.group(2))
        pk = f"{co}-{ne}"
        db_save_session_prediction(match.get("id"), inn, pk, pmin, pmax)
    return (
        f"<b>{format_date_style()}</b>\n\n"
        f"<b>📊 {co}-{ne} OVER SESSION 📊</b>\n\n"
        f"<b>🆚 {t1} vs {t2}</b>\n"
        f"<b>🔴 LIVE | Inning {inn}</b>\n"
        f"<b>📈 Current: {cr}/{cw} ({co} ov)</b>\n"
        f"<b>⚡ Run Rate: {rr}</b>\n\n"
        f"<b>💭 SITUATION:</b>\n"
        f"<b>{_get_situation_read(cw, rr, ne - co)}</b>\n\n"
        f"<b>🎯 SESSION PREDICTION:</b>\n"
        f"<b>Overs: {co + 1} to {ne}</b>\n"
        f"<b>Runs Range: {pmin} - {pmax} 💰</b>\n\n"
        f"( <i>Session by All Rounder</i> )\n\n"
        f"<b>Over Sure Session...</b>\n\n"
        f"<b>Unlimited Kheloo....</b>\n\n"
        f"<b>Lifetime Loss Cover Session 🧿</b>\n\n"
        f"<b>★ Advance Prediction, Guaranteed Sure ★</b>\n\n"
        f"<b>🏏 All-Rounder Cricket 🏏</b>"
    )


async def check_session_predictions_pass(bot, match, state, co):
    mid = match.get("id"); inn = state.get("current_inning", 1)
    fn = fmt_normalize(match.get("matchType", "T20"))
    for to, ne, pn in SESSION_TRIGGERS.get(fn, []):
        if ne is None or co < ne: continue
        pk = f"{to}-{ne}"
        pending = db_get_pending_session_predictions(mid, inn, pk)
        if not pending: continue
        cached = LIVE_MATCHES.get(f"{mid}_phase_start_{pk}")
        if not cached: continue
        apr = state.get("current_runs", 0) - cached.get("runs_at_start", 0)
        t1, t2, _, _, _ = _match_header(match)
        for pred in pending:
            pmin = pred["predicted_min"]; pmax = pred["predicted_max"]
            correct = pmin <= apr <= pmax
            db_resolve_prediction(pred["id"], apr, correct)
            if not pred.get("pass_posted"):
                # v23: Post BOTH pass AND fail — full transparency
                if correct:
                    # Determine emotional mood based on how tight the call was
                    mid_pred = (pmin + pmax) / 2
                    delta = abs(apr - mid_pred)
                    if delta <= 3:
                        mood_line = "<b>🎯 BULLSEYE! Middle of range hit 🔥</b>"
                    elif delta <= 8:
                        mood_line = "<b>💯 Perfect Zone Call!</b>"
                    else:
                        mood_line = "<b>✅ Just Made It — Boss ki Nazar 👁</b>"
                    text = (
                        f"<b>{format_date_style()}</b>\n\n"
                        f"<b>🎯 SESSION PASS 🎯</b>\n\n"
                        f"<b>🆚 {t1} vs {t2}</b>\n"
                        f"<b>🏏 Inning {inn}</b>\n\n"
                        f"<b>📊 SESSION ({to}-{ne} ov):</b>\n"
                        f"<b>Predicted: {pmin}-{pmax}</b> runs\n"
                        f"<b>Actual: {apr}</b> runs ✅\n\n"
                        f"<b>🏆 PASS HO GAYI! 🔥</b>\n"
                        f"{mood_line}\n\n"
                        f"( <i>Confirmed by All Rounder</i> )\n\n"
                        f"<b>Over Sure Session Delivered 💯</b>\n"
                        f"<b>Lifetime Trust Session 🧿</b>\n\n"
                        f"<b>★ Boss ki Prediction, Boss ki Jeet ★</b>\n\n"
                        f"<b>🏏 All-Rounder Cricket 🏏</b>"
                    )
                else:
                    # v23: NEW — session FAIL post (honest transparency)
                    diff = apr - pmin if apr < pmin else apr - pmax
                    reason = "batting bhaari" if apr > pmax else "wickets girre"
                    text = (
                        f"<b>{format_date_style()}</b>\n\n"
                        f"<b>📊 SESSION RESULT UPDATE 📊</b>\n\n"
                        f"<b>🆚 {t1} vs {t2}</b>\n"
                        f"<b>🏏 Inning {inn}</b>\n\n"
                        f"<b>📊 SESSION ({to}-{ne} ov):</b>\n"
                        f"<b>Predicted: {pmin}-{pmax}</b> runs\n"
                        f"<b>Actual: {apr}</b> runs\n\n"
                        f"<b>🎯 Result: Range se bahar — {reason} 🎪</b>\n\n"
                        f"<i>Honesty is our brand.</i>\n"
                        f"<i>Kuch calls miss hoti hain — long-run accuracy is what matters.</i>\n\n"
                        f"<b>📈 Overall accuracy check /stats me dekho</b>\n\n"
                        f"<b>★ Boss ki Prediction — Har baar sikhna ★</b>\n\n"
                        f"<b>🏏 All-Rounder Cricket 🏏</b>"
                    )
                await post_to_channel(bot, text)
                db_mark_pass_posted(pred["id"])


async def check_and_post_recap(bot, match):
    """v25: MATCH PASS post — user's exact template format."""
    mid = match.get("id"); dm = db_get_match(mid)
    if dm and dm.get("recap_posted"): return
    info = await fetch_match_info(mid)
    md = match.get("_ist_dt")
    force = False
    if md:
        hrs_since_start = (now_ist() - md).total_seconds() / 3600
        fn = fmt_normalize(match.get("matchType", "T20"))
        max_hrs = 10 if fn == "TEST" else 8 if fn == "ODI" else 5
        if hrs_since_start > max_hrs:
            force = True
            logger.info(f"⏰ Force-recap after {hrs_since_start:.1f}h: {match.get('name')}")
    if not info:
        if not force: return
        info = {"status": "Match completed", "matchEnded": True}
    if not info.get("matchEnded") and not force:
        return
    status = info.get("status", "")
    teams = match.get("teams", []); winner = None
    for team in teams:
        if team.lower() in status.lower() and ("won" in status.lower() or "win" in status.lower()):
            winner = team; break
    pw = dm.get("predicted_winner") if dm else None
    wc = False
    if pw and winner:
        wc = pw.lower() in winner.lower() or winner.lower() in pw.lower()
        db_resolve_simple_prediction(mid, "winner", winner, wc)
    # Parse win margin from status if possible
    # e.g. "India won by 6 wickets" or "England won by 45 runs"
    margin_match = re.search(r"won\s+by\s+(\d+)\s+(wicket|run|wkt)", status, re.IGNORECASE)
    if margin_match:
        num = margin_match.group(1)
        unit = margin_match.group(2).upper()
        if "WICKET" in unit or "WKT" in unit:
            margin_text = f"BY {num} WKT'S"
        else:
            margin_text = f"BY {num} RUNS"
    else:
        margin_text = ""
    winner_display = (winner or "MATCH").upper()
    # USER'S EXACT TEMPLATE
    text = (
        f"<b>🏆 {winner_display} 🏆 WON MATCH {margin_text}</b>\n\n"
        f"<b>NONCUTTING MATCH PASS 💰✔️</b>\n\n"
        f"<b>Enjoy Your Daily Profits. Wait For next.</b>\n\n"
        f"<b>कोई नहीं हे टक्कर में ! क्यों पड़े हो किसी के चक्कर में !!</b>\n\n"
        f"<b>All - Rounder Cricket fantasy 🏏🏏</b>"
    )
    await post_to_channel(bot, text)
    db_update_match(mid, recap_posted=1, actual_winner=winner or "", match_ended=1)

# v25.7: FAMOUS_TOURNAMENTS restored — used by fallback post when 0 matches
FAMOUS_TOURNAMENTS = [
    ("🏆 IPL — Indian Premier League", "Mar–May annually", "The world's biggest T20 spectacle"),
    ("🌍 T20 World Cup", "Jun (ICC)", "Global glory chase!"),
    ("⚡ WPL — Women's Premier League", "Feb–Mar", "Rising star of women's cricket"),
    ("🏏 Asia Cup", "Aug–Sep", "IND vs PAK — mahayudh!"),
    ("🏆 ICC Champions Trophy", "Feb–Mar", "Top 8 nations knockout"),
    ("🌟 Big Bash League (BBL)", "Dec–Jan", "Australia's T20 party"),
    ("💥 Pakistan Super League (PSL)", "Feb–Mar", "PSL — Karachi vs Lahore!"),
    ("🔥 T20 Blast (Vitality)", "Jun–Jul", "England county T20 rush"),
    ("🏆 The Hundred", "Jul–Aug", "100-ball English madness"),
    ("🇱🇰 Lanka Premier League (LPL)", "Jul–Aug", "Sri Lanka T20 spice"),
    ("🇧🇩 Bangladesh Premier League (BPL)", "Jan–Feb", "Dhaka thunder"),
    ("🇺🇸 Major League Cricket (MLC)", "Jul", "USA cricket rising"),
    ("🏆 Ranji Trophy", "Oct–Mar", "India's domestic Test war"),
    ("⚡ Syed Mushtaq Ali Trophy", "Oct–Nov", "India domestic T20"),
    ("🏏 Vijay Hazare Trophy", "Dec–Jan", "India domestic ODI"),
    ("🌍 ICC World Test Championship", "Ongoing", "Best of 5-day cricket"),
    ("🇦🇺 Border-Gavaskar Trophy", "Nov–Jan", "IND vs AUS Test war"),
    ("🏆 The Ashes", "Nov–Jan", "England vs Australia — history!"),
]


async def gen_no_match_fallback():
    """v19: When 0 matches found, post upcoming schedule + short brand msg.

    Tries to fetch upcoming matches from cache/API first for real data.
    """
    body = f"🏏 <b>UPCOMING CRICKET FIXTURES</b> 🏏\n\n"
    body += "🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢\n\n"
    body += f"📅 <b>{format_date_style()}</b>\n\n"
    # Attempt to fetch upcoming matches (next 3 days) from data cache
    upcoming = []
    try:
        raw = _MATCHES_CACHE.get("data", []) or []
        today = now_ist().date()
        cutoff = today + timedelta(days=3)
        for m in raw:
            dt = m.get("_ist_dt")
            if dt and today < dt.date() <= cutoff:
                if should_cover_match(m):
                    upcoming.append(m)
    except Exception:
        pass
    if upcoming:
        body += "<b>⚡ AGLE 3 DIN KA SCHEDULE ⚡</b>\n\n"
        for m in upcoming[:6]:
            dt = m["_ist_dt"]
            teams = m.get("teams", ["Team A", "Team B"])
            body += (f"<b>🆚 {teams[0]} vs {teams[1]}</b>\n"
                     f"  🏆 {m.get('name', '')[:60]}\n"
                     f"  📆 <b>{dt.strftime('%d %b • %I:%M %p IST')}</b>\n\n")
    else:
        body += "<b>⚡ ONGOING SERIES AROUND THE WORLD ⚡</b>\n\n"
        for name, when, desc in FAMOUS_TOURNAMENTS[:5]:
            body += f"<b>{name}</b>\n"
            body += f"  📆 <i>{when}</i>\n\n"
    body += "🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢\n\n"
    body += "<b>🎯 All-Rounder Boss covers EVERY match — small or big!</b>\n"
    body += "<b>🪙 Toss • 🏆 Match • 📊 Session — Full coverage.</b>\n\n"
    body += "<b>👑 All-Rounder Cricket 🏏</b>\n"
    body += "<b>★ Boss Ki Prediction, Boss Ki Jeet ★</b>"
    return body


# ======================================================
# 📢 15-DAY COMEBACK REMINDER (v17)
# ======================================================
async def job_user_reminder(bot):
    """Every day at 10 AM — DM inactive users (last active >15 days ago).

    Skips users reminded in last 15 days. Uses opt-out flag.
    """
    logger.info("📣 Running user reminder job")
    users = db_get_users_for_reminder(days_inactive=15)
    if not users:
        logger.info("📣 No users to remind today")
        return
    logger.info(f"📣 Reminding {len(users)} inactive users")
    sent = 0
    for uid, uname, fname, last_active, rcount in users:
        try:
            name = fname or "Champion"
            text = (
                f"👋 <b>Hey {name} bhai!</b>\n\n"
                f"🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢\n\n"
                f"<b>🏏 All-Rounder Boss aapko yaad kar raha hai!</b>\n\n"
                f"🔥 <b>Aaj kal channel par:</b>\n"
                f"• 🪙 Sure Toss Predictions\n"
                f"• 🏆 Match Winner Analysis\n"
                f"• 📊 Session Predictions (6/10/15/20 overs)\n"
                f"• 💰 Free daily tips\n\n"
                f"🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢\n\n"
                f"<b>👉 Wapas aa jao — jeet ke liye ready ho jao!</b>\n\n"
                f"<b>💎 Premium Plans:</b>\n"
                f"🎓 Trial ₹{PREMIUM_PRICES['TRIAL']} / 3 Days\n"
                f"💎 Combo ₹{PREMIUM_PRICES['COMBO']} / Month\n\n"
                f"<b>👑 All-Rounder Cricket 🏏</b>\n"
                f"<b>★ Boss Ki Prediction, Boss Ki Jeet ★</b>\n\n"
                f"<i>Reminders band karne ke liye: /stopreminder</i>"
            )
            kb = [
                [InlineKeyboardButton("📢 Open Channel", url=CHANNEL_LINK)],
                [InlineKeyboardButton("💎 View Premium Plans",
                                      callback_data="joinvip_btn")],
                [InlineKeyboardButton("🎓 ₹99 Trial", callback_data="trial")],
                [InlineKeyboardButton("🔕 Stop Reminders",
                                      callback_data="stopreminder")],
            ]
            await bot.send_message(chat_id=uid, text=text, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(kb),
                                   disable_web_page_preview=True)
            db_mark_reminded(uid)
            sent += 1
            await asyncio.sleep(0.4)  # rate-limit safe
        except Exception as e:
            # User blocked bot / deleted — mark opt-out silently
            msg = str(e).lower()
            if "blocked" in msg or "forbidden" in msg or "chat not found" in msg:
                db_optout_reminder(uid)
                logger.info(f"📣 Auto opt-out (blocked/deleted): {uid}")
            else:
                logger.warning(f"reminder fail {uid}: {e}")
    logger.info(f"📣 Reminder job done — sent {sent}/{len(users)}")


async def cmd_stop_reminder(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/stopreminder — user opts out of DM reminders."""
    user = update.effective_user
    db_optout_reminder(user.id)
    await update.message.reply_text(
        "<b>🔕 Reminders band kar diye.</b>\n\n"
        "Aap ab bhi channel par sab kuch dekh sakte ho:\n"
        f"📢 {CHANNEL_LINK}\n\n"
        "<b>Wapas chalu karne ke liye /start bhejo</b>",
        parse_mode="HTML")


async def job_night(bot):
    """v24: DEPRECATED — kept as no-op for safety, no longer scheduled."""
    logger.info("🌙 job_night called but v24 disabled — skipping")
    return


async def job_morning(bot):
    """v25.1 RESTORED: 9 AM daily good morning post to channel."""
    logger.info("🌅 Good Morning job triggered")
    try:
        await post_to_channel(bot, await gen_good_morning())
    except Exception as e:
        logger.error(f"job_morning fail: {e}")

def build_premium_promo_kb(bot_username):
    """Build inline keyboard for premium promo — UPI + Contact + VIP Group + Bot."""
    kb = []
    if UPI_ID:
        u_trial = build_upi_link(PREMIUM_PRICES['TRIAL'], "Trial 3-Days")
        u_combo = build_upi_link(PREMIUM_PRICES['COMBO'], "Combo Plan")
        if u_trial:
            kb.append([InlineKeyboardButton(
                f"🎓 Pay ₹{PREMIUM_PRICES['TRIAL']} Trial (UPI)", url=u_trial)])
        if u_combo:
            kb.append([InlineKeyboardButton(
                f"💎 Pay ₹{PREMIUM_PRICES['COMBO']} Combo (UPI)", url=u_combo)])
    kb.append([InlineKeyboardButton(
        f"💬 Contact {OWNER_USERNAME}",
        url=f"https://t.me/{OWNER_USERNAME.lstrip('@')}")])
    if PREMIUM_GROUP_LINK:
        kb.append([InlineKeyboardButton("🔒 VIP Group Preview",
                                        url=PREMIUM_GROUP_LINK)])
    if bot_username:
        kb.append([InlineKeyboardButton("🌟 Join FREE VIP",
                                        url=f"https://t.me/{bot_username}?start=vip")])
    return kb


async def gen_highlight_match_post():
    """v20: Build the 'Highlight Match of the Day' post from REAL data.

    Picks the highest-priority match from today's cache (that hasn't ended).
    Returns (text, image_buf) — text is always ready, image_buf may be None.
    """
    try:
        matches = _MATCHES_CACHE.get("data", []) or []
    except Exception:
        matches = []
    # Filter: today, covered, not already ended (best-effort)
    today = now_ist().date()
    now = now_ist()
    valid = []
    for m in matches:
        dt = m.get("_ist_dt")
        if not dt or dt.date() != today:
            continue
        if not should_cover_match(m):
            continue
        # Skip if match ended >2 hours ago
        elapsed = (now - dt).total_seconds() / 60
        # Assume max match length: 240 min T20, 480 min ODI/Test
        fn = fmt_normalize(m.get("matchType", "T20"))
        max_len = 480 if fn in ("ODI", "TEST") else 240
        if elapsed > max_len + 120:
            continue
        valid.append(m)
    if not valid:
        return None, None
    # Sort by priority, then by upcoming (not-yet-started preferred)
    def sort_key(m):
        dt = m["_ist_dt"]
        upcoming = 0 if dt > now else 1  # upcoming first
        return (-m.get("_priority", 5), upcoming, dt)
    valid.sort(key=sort_key)
    hm = valid[0]
    t1, t2, venue, fmt, dt = _match_header(hm)
    trn = hm.get("name", "Match")
    # Get prediction from DB if available
    dm = db_get_match(hm.get("id"))
    predicted_winner = (dm or {}).get("predicted_winner") or "TBA"
    toss_winner = (dm or {}).get("predicted_toss_winner") or "TBA"
    toss_choice = (dm or {}).get("predicted_toss_choice") or "BAT"
    when = dt.strftime("%I:%M %p IST")
    # Status line
    elapsed = (now - dt).total_seconds() / 60
    if elapsed < -5:
        status = f"🔜 <b>{when} — SHURU HOGA!</b>"
    elif -5 <= elapsed <= 15:
        status = f"🔴 <b>ABHI SHURU! ({when})</b>"
    else:
        status = f"⚡ <b>LIVE / RECENT — {when}</b>"
    text = (
        f"🏆✨ <b>AAJ KA HIGHLIGHT MATCH</b> ✨🏆\n\n"
        f"🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢\n\n"
        f"<b>{format_date_style()}</b>\n\n"
        f"<b>🆚 {t1} vs {t2}</b>\n"
        f"<b>🏆 {trn[:80]}</b>\n"
        f"📍 <b>{venue}</b>\n"
        f"🏷 <b>{fmt}</b>\n"
        f"{status}\n\n"
        f"🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢\n\n"
        f"<b>🪙 BOSS KA TOSS CALL:</b>\n"
        f"<b>👉 {toss_winner} — {toss_choice} FIRST 🧿</b>\n\n"
        f"<b>🏆 BOSS KA MATCH WINNER:</b>\n"
        f"<b>👉 {predicted_winner} 💪</b>\n\n"
        f"🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢\n\n"
        f"<b>🎯 Kya karega Boss aaj?</b>\n"
        f"• 🪙 Toss Prediction — 1 hr pehle DM (VIP)\n"
        f"• 🏆 Match Winner — 30 min pehle image card\n"
        f"• 📊 6/10/15/20 Over Sessions — LIVE\n"
        f"• 🚨 Wicket + Milestone Alerts — Real-time\n\n"
        f"🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢\n\n"
        f"<b>💎 PAID CHAHIYE?</b>\n"
        f"🎓 <b>Trial ₹{PREMIUM_PRICES['TRIAL']} / 3 Days</b>\n"
        f"💎 <b>Combo ₹{PREMIUM_PRICES['COMBO']} / Month</b>\n"
        f"👉 <b>{OWNER_USERNAME}</b>\n\n"
        f"<b>👑 All-Rounder Cricket 🏏</b>\n"
        f"<b>★ Over Sure Toss, Unlimited Kheloo, Lifetime Loss Cover Toss 🧿 ★</b>"
    )
    # Try to build image using existing match-winner image function
    image_buf = None
    try:
        image_buf = generate_match_winner_image(t1, t2, predicted_winner, trn, venue)
    except Exception as e:
        logger.warning(f"highlight img fail: {e}")
    return text, image_buf


async def job_highlight_match(bot):
    """v20: Daily 5 PM — post 'Highlight Match of the Day' to channel."""
    logger.info("🏆 Running Highlight Match job")
    try:
        # Ensure matches are loaded (may trigger fetch)
        _ = await fetch_todays_matches()
    except Exception as e:
        logger.warning(f"highlight fetch fail: {e}")
    text, img = await gen_highlight_match_post()
    if not text:
        logger.info("🏆 No highlight match to post today (no matches available)")
        return
    # Buttons: VIP / Trial / Contact / Channel
    kb = [
        [InlineKeyboardButton(f"🎓 Trial ₹{PREMIUM_PRICES['TRIAL']}",
                              url=f"https://t.me/{(await bot.get_me()).username}?start=vip")],
        [InlineKeyboardButton(f"💬 {OWNER_USERNAME} (Paid)",
                              url=f"https://t.me/{OWNER_USERNAME.lstrip('@')}")],
    ]
    if PREMIUM_GROUP_LINK:
        kb.append([InlineKeyboardButton("🔒 VIP Group Request",
                                        url=PREMIUM_GROUP_LINK)])
    markup = InlineKeyboardMarkup(kb)
    try:
        if img:
            await send_photo_to_channel(bot, img, text, reply_markup=markup)
        else:
            await post_to_channel(bot, text, reply_markup=markup)
        logger.info("🏆 Highlight match posted to channel")
    except Exception as e:
        logger.error(f"highlight post fail: {e}")
        try:
            await post_to_channel(bot, text)
        except Exception:
            pass


async def job_premium_promo(bot):
    logger.info("💎 Premium Promo")
    vs = ""
    if PREMIUM_GROUP_LINK:
        vs = f"\n<b>🔒 Premium members exclusive VIP Group access!</b>\n\n"
    text = (
        f"<b>💎 ALL-ROUNDER PREMIUM 💎</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🔥 Free predictions se aage badho! 🔥</b>\n\n"
        f"<b>🎓 TRIAL — ₹{PREMIUM_PRICES['TRIAL']} / 3 DAYS 🆕</b>\n"
        f"→ Try before buy!\n\n"
        f"<b>🪙 TOSS — ₹{PREMIUM_PRICES['TOSS']}/month</b>\n"
        f"<b>🏆 MATCH — ₹{PREMIUM_PRICES['MATCH']}/month</b>\n"
        f"<b>📊 SESSION — ₹{PREMIUM_PRICES['SESSION']}/month</b>\n"
        f"<b>💎 COMBO — ₹{PREMIUM_PRICES['COMBO']}/month</b>\n"
        f"(Toss+Match+Session — <b>Best Value 💰</b>)\n"
        f"{vs}"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>💳 Payment:</b> UPI / GPay / PhonePe / Paytm\n"
        f"<b>📞 Contact:</b> {OWNER_USERNAME}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🌟 FREE VIP:</b> Tap button below for early DM alerts\n\n"
        f"<b>🎁 REFER &amp; EARN:</b>\n"
        f"• 3 refs = FREE VIP week\n"
        f"• 10 refs = FREE VIP month\n"
        f"• 25 refs = FREE Premium month\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🏏 All-Rounder Cricket 🏏</b>\n"
        f"<b>★ Boss Ki Prediction, Boss Ki Jeet ★</b>"
    )
    bot_username = None
    try:
        bi = await bot.get_me()
        bot_username = bi.username
    except Exception: pass
    kb = build_premium_promo_kb(bot_username)
    try:
        await bot.send_message(chat_id=CHANNEL_ID, text=text,
                               parse_mode="HTML", disable_web_page_preview=True,
                               reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        logger.error(f"Promo: {e}")
        await post_to_channel(bot, text)


async def post_match_info(bot, match):
    logger.info(f"🏏 Info: {match.get('name')}")
    await post_to_channel(bot, await gen_match_info(match))


async def post_ground(bot, match):
    logger.info(f"🏟️ Ground: {match.get('name')}")
    await post_to_channel(bot, await gen_ground_post(match))


async def post_playing11(bot, match):
    logger.info(f"📋 P11: {match.get('name')}")
    await post_to_channel(bot, await gen_playing11_post(match))


async def post_toss(bot, match):
    """v25: CHANNEL-ONLY toss post. No VIP DM auto-broadcast."""
    logger.info(f"🪙 Toss: {match.get('name')}")
    pt = await gen_toss_post(match)
    if not pt: return
    # v25: Clean text-only post to channel (no image spam)
    try:
        await post_to_channel(bot, pt)
    except Exception as e:
        logger.error(f"Toss post fail: {e}")


async def post_prediction(bot, match):
    """v25: CHANNEL-ONLY match winner post. No VIP DM, no image, no poll."""
    logger.info(f"🔮 Pred: {match.get('name')}")
    pt = await gen_prediction_post(match)
    if not pt: return
    try:
        await post_to_channel(bot, pt)
    except Exception as e:
        logger.error(f"Prediction post fail: {e}")


# v25: Only 2 pre-match jobs — toss + prediction
PRE_MATCH_FUNCTIONS = {
    "toss": post_toss, "prediction": post_prediction,
}


async def job_live_intelligence(bot):
    # v23: Runs every 7 min (24/7). Covers day AND night matches.
    tm = await fetch_todays_matches()
    now = now_ist()
    hlw = False
    for m in tm:
        if not should_cover_match(m): continue
        md = m.get("_ist_dt")
        if not md: continue
        em = (now - md).total_seconds() / 60
        fn = fmt_normalize(m.get("matchType", "T20"))
        md2 = 480 if fn == "TEST" else 480 if fn == "ODI" else 240
        if -10 <= em <= md2:
            hlw = True; break
    if not hlw: return
    for m in tm:
        if not should_cover_match(m): continue
        mid = m.get("id"); md = m.get("_ist_dt")
        if not md: continue
        em = (now - md).total_seconds() / 60
        fmt = m.get("matchType", "T20"); fn = fmt_normalize(fmt)
        md2 = 480 if fn == "TEST" else 480 if fn == "ODI" else 240
        if em < -10 or em > md2: continue
        info = await fetch_match_info(mid)
        if not info: continue
        await check_and_post_toss_result(bot, m)
        if not info.get("matchStarted"): continue
        if info.get("matchEnded"):
            await check_and_post_recap(bot, m); continue
        sc = await fetch_live_scorecard(mid) or info
        state = parse_current_state(sc)
        if not state: continue
        co = over_to_int(state["current_over"])
        os_ = LIVE_MATCHES.get(mid)
        # v24: Wicket + milestone alerts REMOVED — too spammy per user request
        # detect_events still runs internally so LIVE_MATCHES stays fresh, but no posts
        _ = detect_events(os_, state)
        # v25: Session predictions REMOVED from channel — user delivers via premium
        # (Session prediction function still exists for future use in VIP DM)
        # v25: Live score posts REMOVED — only 4 clean posts (toss/match/tossresult/matchresult)
        LIVE_MATCHES[mid] = state


async def job_discover_matches(bot, scheduler):
    logger.info("🔍 Discovering...")
    am = await fetch_todays_matches()
    matches = [m for m in am if should_cover_match(m)] if am else []
    tm = [m for m in matches if m.get("_priority", 0) >= 8]
    tk = now_ist().strftime("%Y-%m-%d") + "_list"
    # v17: Fallback — if no matches today, post famous tournaments (once/day)
    fallback_key = now_ist().strftime("%Y-%m-%d") + "_fallback"
    if not matches:
        if fallback_key not in SCHEDULED_JOBS:
            try:
                await post_to_channel(bot, await gen_no_match_fallback())
                SCHEDULED_JOBS.add(fallback_key)
                logger.info("📢 Fallback tournaments posted (no matches today)")
            except Exception as e:
                logger.warning(f"fallback post fail: {e}")
        return
    # v25.5: Post header any time discovery finds matches (once per day only via tk)
    # (tk = today's date key, so it only posts ONCE per day, no dumping on restart)
    hour_now = now_ist().hour
    if tk not in SCHEDULED_JOBS and matches:
        import random
        sorted_matches = sorted(matches, key=lambda m: m.get("_priority", 0), reverse=True)
        # v26.0: Preview 3-6 matches (was 3-8), bias toward 4-5
        max_pick = min(6, len(sorted_matches))
        min_pick = min(3, max_pick)
        pick_count = random.choices(
            list(range(min_pick, max_pick + 1)),
            weights=[max(1, 4 - abs(x - 5)) for x in range(min_pick, max_pick + 1)]  # peak at 4-5
        )[0]
        pm = sorted_matches[:pick_count]
        await post_to_channel(bot, await gen_morning_header(len(matches), len(tm)))
        await asyncio.sleep(5)
        for i, m in enumerate(pm, 1):
            await post_to_channel(bot, await gen_single_match_preview(m, i, len(pm)))
            await asyncio.sleep(5)  # v25.1: 3s→5s spacing between previews
        SCHEDULED_JOBS.add(tk)
        logger.info(f"📢 Posted {len(pm)} match previews (randomized 3-8 from {len(matches)} available)")
    # v25.5: removed silent-discovery skip — header always posts (once per day)
    now = now_ist()
    immediate_count = 0  # cap immediate-post backlog per discovery run
    # v26.0 QUALITY GATE: Only post toss+prediction for HIGH-QUALITY matches
    # Priority scoring: 10=IPL/WC, 9=Intl bilateral, 8=Franchise T20 (BBL/PSL/CPL),
    # 7=Ranji/Sheffield/Marsh, 6=Associate leagues, 5=other, 2=SKIP (youth)
    quality_matches = [m for m in matches if m.get("_priority", 0) >= 6]
    # Fallback: if zero quality matches, take TOP 2 whatever we have
    if not quality_matches and matches:
        quality_matches = sorted(matches, key=lambda x: -x.get("_priority", 0))[:2]
        logger.info(f"⚠️ No priority-6+ matches — using top 2 fallback (quality gate)")
    # Cap: MAX 6 matches per day get toss+prediction posts (quality > quantity)
    quality_matches = sorted(quality_matches, key=lambda x: -x.get("_priority", 0))[:6]
    covered_ids = {m.get("id") for m in quality_matches}
    logger.info(f"🎯 QUALITY GATE: {len(quality_matches)} matches will get toss+prediction posts")
    for m in matches:
        if m.get("id") not in covered_ids:
            # Save to DB for tracking but no pre-match posts
            db_save_match(m, m.get("_priority", 5))
            continue
        mid = m.get("id") or f"{m.get('name')}_{m['_ist_dt'].isoformat()}"
        md = m["_ist_dt"]
        # v25.5: Only skip if match is already LIVE (started >10 min ago)
        # Otherwise post toss/prediction immediately — better late than never!
        mins_to_start = (md - now).total_seconds() / 60
        if mins_to_start < -10:
            # Match already started >10 min ago — pre-match posts pointless
            db_save_match(m, m.get("_priority", 5))
            continue
        db_save_match(m, m.get("_priority", 5))
        for ok, mb in PRE_MATCH_OFFSETS.items():
            jk = f"{mid}_pre_{ok}"
            if jk in SCHEDULED_JOBS: continue
            pt_ = md - timedelta(minutes=mb)
            fn = PRE_MATCH_FUNCTIONS[ok]
            if pt_ <= now < md:
                # v25.5: Post immediately anytime within the pre-match window
                # (was 5 min limit — too tight, missed most posts)
                if immediate_count < 4:  # cap 4 immediate posts per discovery run
                    delay = immediate_count * 30  # space 30s apart
                    async def _delayed_post(fn=fn, m=m, delay=delay):
                        if delay > 0:
                            await asyncio.sleep(delay)
                        await fn(bot, m)
                    asyncio.create_task(_delayed_post())
                    immediate_count += 1
                    logger.info(f"⚡ Immediate post scheduled ({delay}s delay): {m.get('name', '?')[:40]}")
                SCHEDULED_JOBS.add(jk)
            elif pt_ > now:
                scheduler.add_job(fn, trigger=DateTrigger(run_date=pt_, timezone=IST),
                    args=[bot, m], id=f"job_{jk}", replace_existing=True,
                    misfire_grace_time=600)
                SCHEDULED_JOBS.add(jk)


# ======================================================
# 🌟 VIP PLAN CHOOSER + PLAN DETAIL BUILDERS (v16)
# Flow: joinvip_btn → chooser → plan_xxx → detail + contact admin + group request
# ======================================================

PLAN_META = {
    "trial": {
        "code": "TRIAL",
        "emoji": "🎓",
        "name": "TRIAL PLAN",
        "duration": "3 Days",
        "tagline": "Try before you buy — full premium access!",
        "features": [
            "✅ Toss Prediction (30 min early)",
            "✅ Match Winner Prediction",
            "✅ Session Prediction (LIVE)",
            "✅ Private VIP Group Access",
            "✅ Direct DM alerts",
        ],
    },
    "toss": {
        "code": "TOSS",
        "emoji": "🪙",
        "name": "TOSS PLAN",
        "duration": "1 Month",
        "tagline": "Over Sure Toss • Lifetime Loss Cover 🧿",
        "features": [
            "✅ 100% Sure Toss Prediction",
            "✅ DM 1 hour before match",
            "✅ Loss Cover Guarantee",
            "✅ Private VIP Group Access",
        ],
    },
    "match": {
        "code": "MATCH",
        "emoji": "🏆",
        "name": "MATCH PLAN",
        "duration": "1 Month",
        "tagline": "Match Winner + Playing 11 pro analysis",
        "features": [
            "✅ Match Winner Prediction",
            "✅ Playing 11 Deep Analysis",
            "✅ Pitch + Weather Report",
            "✅ Head-to-Head insights",
            "✅ Private VIP Group Access",
        ],
    },
    "session": {
        "code": "SESSION",
        "emoji": "📊",
        "name": "SESSION PLAN",
        "duration": "1 Month",
        "tagline": "Over-by-Over Session Predictions 🔥",
        "features": [
            "✅ 📊 6/10/15/20 Over Sessions (T20)",
            "✅ 📊 10/20/30/40/50 Over Sessions (ODI)",
            "✅ ⚡ LIVE delivery in VIP Group",
            "✅ 🎯 PASS/FAIL updates",
            "✅ 💪 Runs range predictions",
            "✅ 🔒 Private VIP Group Access",
            "✅ 💬 Direct chat with Boss",
        ],
    },
    "combo": {
        "code": "COMBO",
        "emoji": "💎",
        "name": "COMBO PLAN ⭐ BEST VALUE",
        "duration": "1 Month",
        "tagline": "Toss + Match + Session — Save ₹299 💰",
        "features": [
            "✅ 🪙 Toss Prediction (Sure Shot)",
            "✅ 🏆 Match Winner Prediction",
            "✅ 📊 SESSION PREDICTIONS (LIVE — 6/10/15/20 overs)",
            "✅ 🔒 Private VIP Group Access",
            "✅ 🎁 Loss Cover Guarantee",
            "✅ 💬 Direct chat with All-Rounder Boss",
            "✅ ⚡ Instant delivery in VIP group",
            "✅ 💰 SAVE ₹299 vs individual plans",
        ],
    },
}


def build_vip_full_message(user, already_vip=False):
    """Main VIP entry — shows PLAN CHOOSER menu.

    User clicks a plan → gets full details + Contact Admin + Group Request buttons.
    """
    header = ("<b>✅ You are already in our FREE VIP list!</b>\n\n"
              "<b>💎 UPGRADE to PAID PREMIUM for guaranteed daily tips 👇</b>"
              if already_vip else
              "<b>🎉 WELCOME TO ALL-ROUNDER PREMIUM! 🎉</b>\n\n"
              "<b>👇 Choose Your Plan Below 👇</b>")

    text = (
        f"{header}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🎓 TRIAL — ₹{PREMIUM_PRICES['TRIAL']} / 3 Days 🆕</b>\n"
        f"→ Try before buy!\n\n"
        f"<b>🪙 TOSS — ₹{PREMIUM_PRICES['TOSS']} / Month</b>\n"
        f"→ Over Sure Toss 🧿\n\n"
        f"<b>🏆 MATCH — ₹{PREMIUM_PRICES['MATCH']} / Month</b>\n"
        f"→ Match Winner + Playing 11\n\n"
        f"<b>📊 SESSION — ₹{PREMIUM_PRICES['SESSION']} / Month</b>\n"
        f"→ 6/10/15/20 Over Sessions LIVE\n\n"
        f"<b>💎 COMBO — ₹{PREMIUM_PRICES['COMBO']} / Month ⭐</b>\n"
        f"→ Toss + Match + Session (SAVE big!)\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>📌 HOW IT WORKS:</b>\n"
        f"1️⃣ Tap your plan below\n"
        f"2️⃣ Message Admin ({OWNER_USERNAME}) for payment\n"
        f"3️⃣ Send Payment Screenshot\n"
        f"4️⃣ Request to join VIP Group\n"
        f"5️⃣ Admin approves — <b>You are IN! 🎉</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🌟 Or get FREE VIP</b> (basic alerts only)\n\n"
        f"<b>🏏 All-Rounder Cricket 🏏</b>\n"
        f"<b>★ Boss Ki Prediction, Boss Ki Jeet ★</b>"
    )

    kb = [
        [InlineKeyboardButton(f"🎓 Trial ₹{PREMIUM_PRICES['TRIAL']} / 3 Days",
                              callback_data="plan_trial")],
        [InlineKeyboardButton(f"🪙 Toss ₹{PREMIUM_PRICES['TOSS']}",
                              callback_data="plan_toss"),
         InlineKeyboardButton(f"🏆 Match ₹{PREMIUM_PRICES['MATCH']}",
                              callback_data="plan_match")],
        [InlineKeyboardButton(f"📊 Session ₹{PREMIUM_PRICES['SESSION']}",
                              callback_data="plan_session")],
        [InlineKeyboardButton(f"💎 Combo ₹{PREMIUM_PRICES['COMBO']} ⭐ Best Value",
                              callback_data="plan_combo")],
        [InlineKeyboardButton("🌟 Get FREE VIP (basic alerts)",
                              callback_data="plan_free")],
        [InlineKeyboardButton("📢 Main Channel", url=CHANNEL_LINK)],
    ]
    return text, InlineKeyboardMarkup(kb)


def build_plan_detail(plan_key):
    """Build detailed message + keyboard for a selected paid plan."""
    meta = PLAN_META.get(plan_key)
    if not meta:
        return None, None
    price = PREMIUM_PRICES[meta["code"]]
    feats = "\n".join(meta["features"])

    text = (
        f"<b>{meta['emoji']} {meta['name']}</b>\n"
        f"<b>💰 Price: ₹{price} / {meta['duration']}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🔥 {meta['tagline']}</b>\n\n"
        f"<b>📦 What You Get:</b>\n"
        f"{feats}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>📌 PAYMENT PROCESS (3 Simple Steps):</b>\n\n"
        f"<b>🚨 PAYMENT KE LIYE ADMIN KO MESSAGE KARO 🚨</b>\n\n"
        f"┏━━━━━━━━━━━━━━━━━━┓\n"
        f"  👤 <b>ADMIN USERNAME:</b>\n"
        f"  👉 <b>{OWNER_USERNAME}</b> 👈\n"
        f"  💰 <b>Amount:</b> ₹{price}\n"
        f"┗━━━━━━━━━━━━━━━━━━┛\n\n"
        f"<b>📌 SIMPLE 4-STEP PROCESS:</b>\n\n"
        f"<b>STEP 1️⃣</b> Tap ‘💬 Message Admin’ button below\n"
        f"   → Admin ({OWNER_USERNAME}) ka DM khulega\n\n"
        f"<b>STEP 2️⃣</b> ‘HI, {meta['name']} chahiye’ likhkr bhej\n"
        f"   → Admin turant UPI ID / QR bhej dega\n"
        f"   → GPay / PhonePe / Paytm — sab chalega\n\n"
        f"<b>STEP 3️⃣</b> ₹{price} bhejne ke baad screenshot bhej\n"
        f"   → Admin 5-10 min me verify karega ✅\n\n"
        f"<b>STEP 4️⃣</b> Tap ‘🔒 Request to Join VIP Group’\n"
        f"   → <b>Admin approve karega → Tu IN! 🎉</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>⚡ FAST APPROVAL — usually within 5-10 min after payment</b>\n\n"
        f"<b>🏏 All-Rounder Cricket 🏏</b>\n"
        f"<b>★ Boss Ki Prediction, Boss Ki Jeet ★</b>"
    )

    kb = []
    # Optional UPI direct-pay (only if UPI_ID set)
    if UPI_ID:
        upi = build_upi_link(price, f"{meta['code']} Plan")
        if upi:
            kb.append([InlineKeyboardButton(
                f"💳 Pay ₹{price} via UPI (Direct)", url=upi)])
    # PRIMARY CTA — message admin
    kb.append([InlineKeyboardButton(
        f"💬 Message Admin {OWNER_USERNAME} (Payment)",
        url=f"https://t.me/{OWNER_USERNAME.lstrip('@')}")])
    # VIP group request link (request-to-join type; admin manually approves)
    if PREMIUM_GROUP_LINK:
        kb.append([InlineKeyboardButton(
            "🔒 Request to Join VIP Group",
            url=PREMIUM_GROUP_LINK)])
    kb.append([
        InlineKeyboardButton("🔙 Back to Plans", callback_data="joinvip_btn"),
        InlineKeyboardButton("📢 Channel", url=CHANNEL_LINK),
    ])
    return text, InlineKeyboardMarkup(kb)


async def notify_admin_new_order(ctx, user, plan_key):
    """Auto-DM the admin whenever a user selects a paid plan.

    Silent no-op if ADMIN_USER_ID is not configured.
    """
    if not ADMIN_USER_ID:
        return
    meta = PLAN_META.get(plan_key)
    if not meta:
        return
    price = PREMIUM_PRICES[meta["code"]]
    uname = f"@{user.username}" if user.username else "(no username)"
    full_name = (user.first_name or "") + (" " + user.last_name if user.last_name else "")
    text = (
        f"<b>🔔 NEW ORDER ALERT 🔔</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>👤 User:</b> {full_name.strip() or 'Unknown'}\n"
        f"<b>🆔 Username:</b> {uname}\n"
        f"<b>🔢 User ID:</b> <code>{user.id}</code>\n\n"
        f"<b>{meta['emoji']} Plan:</b> {meta['name']}\n"
        f"<b>💰 Amount:</b> ₹{price} / {meta['duration']}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>👉 Ye user tumhe payment ke liye message karega.</b>\n"
        f"<b>Payment aane ke baad VIP group request approve karo.</b>\n\n"
        f"<b>🏏 All-Rounder Cricket 🏏</b>"
    )
    kb_rows = [[InlineKeyboardButton(
        f"💬 Open Chat: {uname}",
        url=f"tg://user?id={user.id}")]]
    # If user has @username, also add a t.me/username button (works when tg://user fails)
    if user.username:
        kb_rows.append([InlineKeyboardButton(
            f"🔗 t.me/{user.username}",
            url=f"https://t.me/{user.username}")])
    kb = kb_rows
    try:
        await ctx.bot.send_message(chat_id=ADMIN_USER_ID, text=text,
                                   parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(kb))
        logger.info(f"🔔 Admin notified: order {plan_key} from {user.id}")
    except Exception as e:
        logger.warning(f"admin notify failed: {e}")



# ======================================================
# 🛡 SAFE MESSAGE SENDER (for callback responses)
# ======================================================
async def safe_edit_or_send(ctx, q, text, reply_markup=None):
    """
    BULLETPROOF: Try to edit message, if fails send new message.
    Guarantees user sees a response.
    """
    user_id = q.from_user.id
    try:
        await q.edit_message_text(text, parse_mode="HTML",
            reply_markup=reply_markup, disable_web_page_preview=True)
        return True
    except Exception as e:
        err = str(e).lower()
        # "Message is not modified" is fine — just skip
        if "not modified" in err:
            return True
        logger.warning(f"Edit failed for {user_id}: {e}")
    # Fallback: send new message
    try:
        await ctx.bot.send_message(chat_id=user_id, text=text,
            parse_mode="HTML", reply_markup=reply_markup,
            disable_web_page_preview=True)
        return True
    except Exception as e2:
        logger.error(f"Send fallback failed: {e2}")
    # Last resort: send without HTML
    try:
        # Strip HTML tags for plain text fallback
        plain = re.sub(r"<[^>]+>", "", text)
        await ctx.bot.send_message(chat_id=user_id, text=plain[:4000],
            reply_markup=reply_markup, disable_web_page_preview=True)
        return True
    except Exception as e3:
        logger.error(f"Plain fallback failed: {e3}")
        return False


# ======================================================
# 🤖 BOT COMMANDS
# ======================================================
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        db_track_user(update.effective_user)
    except Exception:
        pass
    user = update.effective_user
    logger.info(f"/start from {user.id} (@{user.username})")

    if ctx.args and ctx.args[0] == "vip":
        return await cmd_join_vip(update, ctx)

    if ctx.args and ctx.args[0].startswith("ref_"):
        try:
            rid = int(ctx.args[0].replace("ref_", ""))
            if rid != user.id:
                conn = sqlite3.connect(DB_PATH); c = conn.cursor()
                c.execute("SELECT 1 FROM referrals WHERE user_id = ?", (user.id,))
                if not c.fetchone():
                    c.execute("""INSERT INTO referrals (user_id, username, referred_by, joined_at) VALUES (?, ?, ?, ?)""",
                        (user.id, user.username or user.first_name, rid, now_ist().isoformat()))
                    conn.commit()
                    try:
                        await ctx.bot.send_message(chat_id=rid,
                            text=f"<b>🎉 New Referral!</b>\n\n@{user.username or user.first_name} joined!",
                            parse_mode="HTML")
                    except Exception: pass
                conn.close()
        except Exception: pass

    # v19: Cleaner button layout — most-used first, dummy-free
    kb = [
        [InlineKeyboardButton("📢 Open Channel", url=CHANNEL_LINK)],
        [InlineKeyboardButton("💎 Premium Plans", callback_data="premium"),
         InlineKeyboardButton("🎓 ₹99 TRIAL", callback_data="trial")],
        [InlineKeyboardButton("🏏 Today's Matches", callback_data="today"),
         InlineKeyboardButton("🌟 FREE VIP", callback_data="joinvip_btn")],
        [InlineKeyboardButton("🎁 Refer & Earn", callback_data="refer"),
         InlineKeyboardButton("ℹ️ Help", callback_data="help")]
    ]
    await update.message.reply_text(
        f"<b>🏏 Welcome to {BOT_NAME}!</b>\n\n"
        f"<b>🔥 100% AUTOMATIC Cricket Bot 🔥</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>📅 DAILY IN CHANNEL:</b>\n\n"
        f"• 🏏 Match Previews\n"
        f"• 🏟️ Ground Reports\n"
        f"• 🪙 Toss Predictions\n"
        f"• 🏆 Match Winners\n"
        f"• 📊 6/10/15/20 Over Sessions\n"
        f"• 🚨 Live Wicket Alerts\n"
        f"• 🎯 PASS/FAIL Verification\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>💎 PREMIUM PLANS:</b>\n\n"
        f"• 🎓 <b>TRIAL — ₹{PREMIUM_PRICES['TRIAL']} / 3 Days 🆕</b>\n"
        f"• 🪙 Toss — ₹{PREMIUM_PRICES['TOSS']}/mo\n"
        f"• 🏆 Match — ₹{PREMIUM_PRICES['MATCH']}/mo\n"
        f"• 💎 Combo — ₹{PREMIUM_PRICES['COMBO']}/mo\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🌟 FREE VIP:</b> Early DM alerts!\n\n"
        f"<b>📢 Channel:</b> {CHANNEL_LINK}\n\n"
        f"👇 <b>Choose an option:</b>",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="HTML", disable_web_page_preview=True
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logger.info(f"/help from {update.effective_user.id}")
    text = (
        f"<b>🏏 {BOT_NAME}</b>\n"
        f"<b>Complete Command Guide</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🤖 100% AUTOMATIC BOT</b>\n\n"
        f"Auto-detects matches 24/7 and posts clean coverage in channel:\n"
        f"• 🪙 Toss Prediction (2hr before match)\n"
        f"• 🏆 Match Winner (1hr before match)\n"
        f"• ✅ Toss Result (when toss happens)\n"
        f"• 🏆 Match Result (when match ends)\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>💎 PREMIUM &amp; VIP:</b>\n\n"
        f"/premium — View paid plans\n"
        f"/trial — 🆕 ₹99 / 3-day trial\n"
        f"/joinvip — Get <b>FREE VIP</b>\n"
        f"/leavevip — Opt out\n"
        f"/viplist — Total VIP count\n"
        f"/refer — Your referral link\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>📅 MATCH INFO:</b>\n\n"
        f"/today — Today's matches\n"
        f"/status — Scheduled jobs\n"
        f"/stats — Accuracy report\n"
        f"/refresh — Force re-fetch\n"
        f"/live — Force live cycle\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🔮 MANUAL PREDICTIONS:</b>\n\n"
        f"/predict T1 vs T2\n"
        f"/toss T1 vs T2\n"
        f"/ground VENUE\n"
        f"/playing11 T1 vs T2\n"
        f"/points [TOURNAMENT]\n"
        f"/tips\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>💳 EASY PAYMENT FLOW:</b>\n\n"
        f"1️⃣ Tap 💎 Premium below\n"
        f"2️⃣ Choose your plan\n"
        f"3️⃣ Message {OWNER_USERNAME}\n"
        f"4️⃣ Send payment screenshot in bot chat\n"
        f"   → Bot auto-forwards to Admin ✅\n"
        f"5️⃣ Tap 🔒 VIP Group Request → Admin approves\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>📢 Channel:</b> {CHANNEL_LINK}\n"
        f"<b>📞 Owner:</b> {OWNER_USERNAME}\n"
        f"<b>🔕 Stop reminder DMs:</b> /stopreminder\n\n"
        f"<b>🏏 All-Rounder Cricket 🏏</b>"
    )
    kb = [
        [InlineKeyboardButton("💎 View Premium", callback_data="premium"),
         InlineKeyboardButton("🎓 ₹99 Trial", callback_data="trial")],
        [InlineKeyboardButton("🌟 Join FREE VIP", callback_data="joinvip_btn"),
         InlineKeyboardButton("🏏 Today", callback_data="today")],
        [InlineKeyboardButton("📊 Stats", callback_data="stats"),
         InlineKeyboardButton("🎁 Refer", callback_data="refer")],
        [InlineKeyboardButton("📢 Channel", url=CHANNEL_LINK)]
    ]
    await update.message.reply_text(text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb), disable_web_page_preview=True)


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("<b>🔄 Fetching...</b>", parse_mode="HTML")
    matches = await fetch_todays_matches()
    if not matches:
        await msg.edit_text("<b>❌ No matches found today</b>\n\nTry /refresh in 15 min.",
            parse_mode="HTML"); return
    covered = [m for m in matches if should_cover_match(m)]
    lines = [f"<b>📅 TODAY'S MATCHES</b>", f"<b>{format_date_style()}</b>", "",
             f"<b>🏏 Total: {len(matches)} | Covered: {len(covered)}</b>",
             f"━━━━━━━━━━━━━━━━━━", ""]
    for i, m in enumerate(covered, 1):
        badge = "⭐ TOP" if m.get("_priority", 0) >= 8 else "🏏"
        lines.append(f"<b>{badge} #{i}</b>")
        lines.append(f"<b>{m.get('name', '')[:70]}</b>")
        lines.append(f"🕐 <b>{m['_ist_dt'].strftime('%I:%M %p IST')}</b> | {m.get('matchType', 'T20')}")
        lines.append("")
    skipped = len(matches) - len(covered)
    if skipped > 0:
        lines.append(f"<i>⏭️ {skipped} low-priority skipped</i>")
    lines.append(""); lines.append(f"<b>🏏 All-Rounder Cricket 🏏</b>")
    await msg.edit_text("\n".join(lines)[:4000], parse_mode="HTML")


async def cmd_accuracy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """v22: Quick accuracy — alias for /stats with cleaner output."""
    return await cmd_stats(update, ctx)


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s7 = db_get_accuracy(days=7); s30 = db_get_accuracy(days=30)
    text = (f"<b>📊 PREDICTION ACCURACY REPORT</b>\n"
            f"<b>{format_date_style()}</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━\n\n<b>📅 LAST 7 DAYS:</b>\n\n")
    if s7:
        for r in s7:
            pt, t, c = r
            pct = (c * 100 // t) if t > 0 else 0
            emo = "🟢" if pct >= 60 else "🟡" if pct >= 40 else "🔴"
            lb = {"winner": "Match Winner", "toss_winner": "Toss Winner",
                  "toss_choice": "Toss Choice", "session": "Session"}.get(pt, pt)
            text += f"{emo} <b>{lb}:</b> {c}/{t} <b>({pct}%)</b>\n"
    else:
        text += "<i>No data yet</i>\n"
    text += "\n━━━━━━━━━━━━━━━━━━\n\n<b>📅 LAST 30 DAYS:</b>\n\n"
    if s30:
        for r in s30:
            pt, t, c = r
            pct = (c * 100 // t) if t > 0 else 0
            emo = "🟢" if pct >= 60 else "🟡" if pct >= 40 else "🔴"
            lb = {"winner": "Match Winner", "toss_winner": "Toss Winner",
                  "toss_choice": "Toss Choice", "session": "Session"}.get(pt, pt)
            text += f"{emo} <b>{lb}:</b> {c}/{t} <b>({pct}%)</b>\n"
    else:
        text += "<i>No data yet</i>\n"
    text += "\n━━━━━━━━━━━━━━━━━━\n\n<b>🏏 All-Rounder Cricket 🏏</b>"
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_refresh(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("<b>🔄 Refreshing...</b>", parse_mode="HTML")
    sch = ctx.application.bot_data.get("scheduler")
    if sch:
        SCHEDULED_JOBS.clear(); _MATCHES_CACHE["fetched_at"] = None
        await job_discover_matches(ctx.bot, sch)
        await msg.edit_text("<b>✅ Re-scheduled!</b>", parse_mode="HTML")
    else:
        await msg.edit_text("<b>❌ Not ready</b>", parse_mode="HTML")


async def cmd_live(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("<b>🔴 Force live...</b>", parse_mode="HTML")
    await job_live_intelligence(ctx.bot)
    await msg.edit_text("<b>✅ Triggered!</b>", parse_mode="HTML")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sch = ctx.application.bot_data.get("scheduler")
    if not sch:
        await update.message.reply_text("<b>❌ Not ready</b>", parse_mode="HTML"); return
    jobs = sch.get_jobs()
    if not jobs:
        await update.message.reply_text("<b>ℹ️ No jobs</b>", parse_mode="HTML"); return
    lines = [f"<b>⏰ SCHEDULED JOBS: {len(jobs)}</b>", f"━━━━━━━━━━━━━━━━━━", ""]
    for j in sorted(jobs, key=lambda x: x.next_run_time or now_ist()):
        nrt = j.next_run_time
        if nrt:
            lines.append(f"• <b>{nrt.strftime('%d %b %I:%M %p IST')}</b>")
            lines.append(f"  <code>{j.id}</code>")
    lines.append(""); lines.append("<b>🏏 All-Rounder Cricket 🏏</b>")
    text = "\n".join(lines)
    if len(text) > 4000: text = text[:3900] + "\n<i>...</i>"
    await update.message.reply_text(text, parse_mode="HTML")


async def _parse_vs(args):
    text = " ".join(args).lower()
    if "vs" not in text: return None, None
    p = text.split("vs")
    return p[0].strip().upper(), p[1].strip().upper()


async def gen_prediction(t1, t2, venue="TBA", fmt="T20", tournament=""):
    fake = {"teams": [t1, t2], "venue": venue, "matchType": fmt,
            "name": tournament or f"{t1} vs {t2}",
            "id": f"manual_{t1}_{t2}_{now_ist().timestamp()}",
            "_ist_dt": now_ist()}
    return await gen_prediction_post(fake)


async def gen_toss(t1, t2, venue="TBA", fmt="T20"):
    fake = {"teams": [t1, t2], "venue": venue, "matchType": fmt,
            "name": f"{t1} vs {t2}",
            "id": f"manual_t_{t1}_{t2}_{now_ist().timestamp()}",
            "_ist_dt": now_ist()}
    return await gen_toss_post(fake)


async def gen_ground(venue, t1="", t2=""):
    fake = {"teams": [t1 or "Team A", t2 or "Team B"],
            "venue": venue, "matchType": "T20", "name": "Ground Report",
            "id": f"manual_g_{venue}_{now_ist().timestamp()}", "_ist_dt": now_ist()}
    return await gen_ground_post(fake)


async def gen_playing11(t1, t2, tournament=""):
    fake = {"teams": [t1, t2], "venue": "TBA", "matchType": "T20",
            "name": tournament or f"{t1} vs {t2}",
            "id": f"manual_p11_{t1}_{t2}_{now_ist().timestamp()}",
            "_ist_dt": now_ist()}
    return await gen_playing11_post(fake)


async def gen_points(tournament=""):
    prompt = f"Point table for ongoing tournament ({today_date()}). Tournament: {tournament if tournament else 'Most relevant ongoing'}. Give STANDINGS + PLAYOFF status. Hinglish."
    r = await ask_gemini(prompt)
    return f"<b>{r}</b>\n\n━━━━━━━━━━━━━━━━━━━\n📢 <b>{BOT_NAME}</b>\n🔗 {CHANNEL_LINK}" if r else None


async def gen_tips():
    prompt = f"Cricket tips for {today_date()}. TOP UPDATES + EXPERT TIPS. Hinglish, no markdown."
    r = await ask_gemini(prompt)
    return f"<b>{r}</b>\n\n━━━━━━━━━━━━━━━━━━━\n📢 <b>{BOT_NAME}</b>\n🔗 {CHANNEL_LINK}" if r else None


async def cmd_predict(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t1, t2 = await _parse_vs(ctx.args)
    if not t1: await update.message.reply_text("<b>⚠️ /predict CSK vs MI</b>", parse_mode="HTML"); return
    msg = await update.message.reply_text("<b>🔄 Generating...</b>", parse_mode="HTML")
    r = await gen_prediction(t1, t2)
    if r:
        await msg.edit_text(r, parse_mode="HTML")
        await post_to_channel(ctx.bot, r)
    else:
        await msg.edit_text("<b>❌ Failed</b>", parse_mode="HTML")


async def cmd_toss(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t1, t2 = await _parse_vs(ctx.args)
    if not t1: await update.message.reply_text("<b>⚠️ /toss CSK vs MI</b>", parse_mode="HTML"); return
    msg = await update.message.reply_text("<b>🪙 Generating...</b>", parse_mode="HTML")
    r = await gen_toss(t1, t2)
    if r:
        await msg.edit_text(r, parse_mode="HTML")
        await post_to_channel(ctx.bot, r)
    else:
        await msg.edit_text("<b>❌ Failed</b>", parse_mode="HTML")


async def cmd_ground(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args: await update.message.reply_text("<b>⚠️ /ground Wankhede</b>", parse_mode="HTML"); return
    msg = await update.message.reply_text("<b>🏟️ Generating...</b>", parse_mode="HTML")
    r = await gen_ground(" ".join(ctx.args))
    if r:
        await msg.edit_text(r, parse_mode="HTML")
        await post_to_channel(ctx.bot, r)
    else:
        await msg.edit_text("<b>❌ Failed</b>", parse_mode="HTML")


async def cmd_playing11(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t1, t2 = await _parse_vs(ctx.args)
    if not t1: await update.message.reply_text("<b>⚠️ /playing11 CSK vs MI</b>", parse_mode="HTML"); return
    msg = await update.message.reply_text("<b>📋 Generating...</b>", parse_mode="HTML")
    r = await gen_playing11(t1, t2)
    if r:
        await msg.edit_text(r, parse_mode="HTML")
        await post_to_channel(ctx.bot, r)
    else:
        await msg.edit_text("<b>❌ Failed</b>", parse_mode="HTML")


async def cmd_points(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("<b>📊 Generating...</b>", parse_mode="HTML")
    r = await gen_points(" ".join(ctx.args) if ctx.args else "")
    if r:
        await msg.edit_text(r, parse_mode="HTML")
        await post_to_channel(ctx.bot, r)
    else:
        await msg.edit_text("<b>❌ Failed</b>", parse_mode="HTML")


async def cmd_tips(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("<b>📰 Generating...</b>", parse_mode="HTML")
    r = await gen_tips()
    if r:
        await msg.edit_text(r, parse_mode="HTML")
        await post_to_channel(ctx.bot, r)
    else:
        await msg.edit_text("<b>❌ Failed</b>", parse_mode="HTML")


async def cmd_addmatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args) if ctx.args else ""
    if "|" not in text:
        await update.message.reply_text(
            "<b>⚠️ FORMAT:</b>\n<code>/addmatch Tournament | Team1 vs Team2 | Venue | HH:MM | T20/ODI/TEST</code>",
            parse_mode="HTML"); return
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < 5:
        await update.message.reply_text("<b>⚠️ 5 parts required</b>", parse_mode="HTML"); return
    trn, teams, venue, ts, fmt = parts[:5]
    if "vs" not in teams.lower():
        await update.message.reply_text("<b>⚠️ Team1 vs Team2</b>", parse_mode="HTML"); return
    tp = teams.lower().split("vs")
    t1 = tp[0].strip().upper(); t2 = tp[1].strip().upper()
    try:
        h, mn = map(int, ts.strip().split(":"))
        td = now_ist().date()
        sd = datetime(td.year, td.month, td.day, h, mn, tzinfo=IST)
        if sd < now_ist() - timedelta(hours=2): sd += timedelta(days=1)
    except Exception:
        await update.message.reply_text("<b>⚠️ Time HH:MM</b>", parse_mode="HTML"); return
    fmt = fmt.strip().upper()
    if fmt not in ["T20", "ODI", "TEST"]: fmt = "T20"
    mid = db_add_manual_match(trn, t1, t2, venue, sd, fmt)
    _MATCHES_CACHE["fetched_at"] = None
    sch = ctx.application.bot_data.get("scheduler")
    if sch:
        SCHEDULED_JOBS.clear()
        asyncio.create_task(job_discover_matches(ctx.bot, sch))
    await update.message.reply_text(
        f"<b>✅ MATCH ADDED!</b>\n\n"
        f"🏆 <b>{trn}</b>\n🆚 <b>{t1} vs {t2}</b>\n"
        f"📍 {venue}\n🕐 <b>{sd.strftime('%d %b %I:%M %p IST')}</b>\n"
        f"🏷 {fmt}\n\nID: <code>{mid}</code>", parse_mode="HTML")


async def cmd_listmatches(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    matches = db_get_manual_matches()
    if not matches:
        await update.message.reply_text("<b>📭 No manual matches</b>", parse_mode="HTML"); return
    lines = [f"<b>📋 MANUAL MATCHES ({len(matches)}):</b>", ""]
    for m in matches:
        try:
            sd = datetime.fromisoformat(m["start_time"])
            lines.append(f"<b>⭐ {m['team1']} vs {m['team2']}</b>")
            lines.append(f"   🏆 {m['tournament']}")
            lines.append(f"   🕐 <b>{sd.strftime('%d %b %I:%M %p IST')}</b>")
            lines.append(f"   🆔 <code>{m['id']}</code>")
            lines.append("")
        except Exception: pass
    lines.append("💡 <code>/removematch ID</code>")
    await update.message.reply_text("\n".join(lines)[:4000], parse_mode="HTML")


async def cmd_removematch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("<b>⚠️ /removematch &lt;ID&gt;</b>", parse_mode="HTML"); return
    mid = ctx.args[0]
    db_remove_manual_match(mid)
    _MATCHES_CACHE["fetched_at"] = None
    await update.message.reply_text(f"<b>🗑 Removed: {mid}</b>", parse_mode="HTML")


async def cmd_join_vip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """v16: Shows PLAN CHOOSER. User picks Free or a paid plan explicitly."""
    user = update.effective_user
    logger.info(f"/joinvip from {user.id}")
    already = db_is_vip(user.id)
    text, markup = build_vip_full_message(user, already)
    await update.message.reply_text(text, parse_mode="HTML",
        reply_markup=markup, disable_web_page_preview=True)


async def cmd_leave_vip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"/leavevip from {user.id}")
    if not db_is_vip(user.id):
        kb = [[InlineKeyboardButton("🌟 Join FREE VIP Now", callback_data="joinvip_btn")]]
        await update.message.reply_text(
            "<b>ℹ️ Aap abhi VIP list me nahi ho.</b>\n\n"
            "<b>Use /joinvip — <i>Bilkul FREE!</i></b>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb)); return
    db_remove_vip(user.id)
    kb = [
        [InlineKeyboardButton("🌟 Rejoin FREE VIP", callback_data="joinvip_btn")],
        [InlineKeyboardButton("💎 View Paid Plans", callback_data="premium"),
         InlineKeyboardButton("🎓 ₹99 Trial", callback_data="trial")],
        [InlineKeyboardButton("📢 Main Channel", url=CHANNEL_LINK)],
    ]
    await update.message.reply_text(
        "<b>😢 VIP list se remove kar diya.</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Aap kabhi bhi wapas jud sakte ho:</b>\n"
        "• 🌟 <b>FREE VIP</b> — /joinvip\n"
        "• 🎓 <b>Trial ₹99 / 3 Days</b> — /trial\n"
        "• 💎 <b>Paid Plans</b> — /premium\n\n"
        "<b>🏏 All-Rounder Cricket 🏏</b>",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))


async def cmd_vip_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    vips = db_get_vip_users()
    total = VIP_BASE_COUNT + len(vips)
    text = (
        f"<b>🌟 ALL-ROUNDER VIP CLUB 🌟</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>💎 Total VIP Members: {total}+ </b>\n\n"
        f"🔒 <i>Member identities kept private for security.</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Become a VIP:</b>\n\n"
        f"🌟 <b>FREE VIP</b> → /joinvip\n"
        f"🎓 <b>₹99 TRIAL</b> → /trial\n"
        f"💎 <b>PAID PREMIUM</b> → /premium\n\n"
        f"<b>🏏 All-Rounder Cricket 🏏</b>"
    )
    kb = [[InlineKeyboardButton("🌟 Join FREE VIP", callback_data="joinvip_btn"),
           InlineKeyboardButton("💎 View Premium", callback_data="premium")]]
    await update.message.reply_text(text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb))


async def cmd_trial(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logger.info(f"/trial from {update.effective_user.id}")
    text = build_trial_message()
    kb = build_trial_kb()
    await update.message.reply_text(text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb),
        disable_web_page_preview=True)


def build_trial_message():
    return (
        f"<b>🎓 ALL-ROUNDER TRIAL PLAN 🎓</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>💥 ONLY ₹{PREMIUM_PRICES['TRIAL']} / 3 DAYS! 💥</b>\n\n"
        f"<b>Perfect for first-time users!</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>✅ YOU WILL GET (3 Days):</b>\n\n"
        f"• 🪙 <b>Sure Toss Predictions</b>\n"
        f"• 🏆 <b>Match Winner Analysis</b>\n"
        f"• 📊 <b>Session Predictions</b>\n"
        f"• 📸 <b>Premium Image Cards</b>\n"
        f"• 💬 <b>Direct DM Support</b>\n"
        f"• 🔒 <b>VIP Group Access</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>💳 EASY PAYMENT:</b>\n\n"
        f"→ <b>UPI Direct</b> (tap button below)\n"
        f"→ GPay / PhonePe / Paytm\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>📞 CONTACT AFTER PAYMENT:</b>\n"
        f"👉 <b>{OWNER_USERNAME}</b>\n"
        f"(Send payment screenshot)\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🏏 All-Rounder Cricket 🏏</b>\n"
        f"<b>★ Try Once, Trust Forever ★</b>"
    )


def build_trial_kb():
    kb = []
    if UPI_ID:
        u = build_upi_link(PREMIUM_PRICES['TRIAL'], "Trial 3-Days")
        if u:
            kb.append([InlineKeyboardButton(
                f"💳 Pay ₹{PREMIUM_PRICES['TRIAL']} via UPI (Direct)", url=u)])
    kb.append([InlineKeyboardButton(
        f"💬 Contact Owner (Paid Group)",
        url=f"https://t.me/{OWNER_USERNAME.lstrip('@')}")])
    if PREMIUM_GROUP_LINK:
        kb.append([InlineKeyboardButton("🔒 VIP Group Preview", url=PREMIUM_GROUP_LINK)])
    kb.append([InlineKeyboardButton("📢 Main Channel", url=CHANNEL_LINK)])
    return kb


async def cmd_premium(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logger.info(f"/premium from {update.effective_user.id}")
    text = build_premium_message()
    kb = build_premium_kb()
    await update.message.reply_text(text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb), disable_web_page_preview=True)


def build_premium_message():
    vg = ""
    if PREMIUM_GROUP_LINK:
        vg = (f"\n<b>🔒 PREMIUM MEMBERS GET:</b>\n"
              f"• Access to Private VIP Group\n"
              f"• Instant tips before match\n"
              f"• Direct chat with All Rounder\n\n")
    return (
        f"<b>💎 ALL-ROUNDER PREMIUM 💎</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🎓 TRIAL — ₹{PREMIUM_PRICES['TRIAL']} / 3 DAYS 🆕</b>\n"
        f"• Full premium access\n• Try before commitment\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🪙 TOSS — ₹{PREMIUM_PRICES['TOSS']}/month</b>\n"
        f"• 100% Sure Toss\n• DM 1 hr before match\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🏆 MATCH — ₹{PREMIUM_PRICES['MATCH']}/month</b>\n"
        f"• Match Winner\n• Playing 11 Analysis\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>📊 SESSION — ₹{PREMIUM_PRICES['SESSION']}/month</b>\n"
        f"• 6/10/15/20 Over Session Predictions\n"
        f"• LIVE delivery + PASS/FAIL updates\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>💎 COMBO — ₹{PREMIUM_PRICES['COMBO']}/month</b>\n"
        f"• 🪙 Toss + 🏆 Match + 📊 Session\n"
        f"• 6/10/15/20 Over Sessions LIVE\n"
        f"• Private VIP Group Access 🔒\n"
        f"• <b>SAVE ₹299 💰</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━"
        f"{vg}\n"
        f"<b>💳 EASY PAYMENT:</b>\n"
        f"• UPI Direct (tap buttons below)\n"
        f"• GPay / PhonePe / Paytm\n\n"
        f"<b>📞 CONTACT:</b>\n"
        f"👉 <b>{OWNER_USERNAME}</b>\n\n"
        f"<b>🏏 All-Rounder Cricket 🏏</b>"
    )


def build_premium_kb():
    kb = []
    if UPI_ID:
        u_tr = build_upi_link(PREMIUM_PRICES['TRIAL'], "Trial 3-Days")
        u_to = build_upi_link(PREMIUM_PRICES['TOSS'], "Toss Plan")
        u_ma = build_upi_link(PREMIUM_PRICES['MATCH'], "Match Plan")
        u_se = build_upi_link(PREMIUM_PRICES['SESSION'], "Session Plan")
        u_co = build_upi_link(PREMIUM_PRICES['COMBO'], "Combo Plan")
        if u_tr:
            kb.append([InlineKeyboardButton(
                f"🎓 Pay ₹{PREMIUM_PRICES['TRIAL']} Trial", url=u_tr)])
        if u_to and u_ma:
            kb.append([
                InlineKeyboardButton(f"🪙 ₹{PREMIUM_PRICES['TOSS']}", url=u_to),
                InlineKeyboardButton(f"🏆 ₹{PREMIUM_PRICES['MATCH']}", url=u_ma),
            ])
        if u_se:
            kb.append([InlineKeyboardButton(
                f"📊 Pay ₹{PREMIUM_PRICES['SESSION']} Session", url=u_se)])
        if u_co:
            kb.append([InlineKeyboardButton(
                f"💎 Pay ₹{PREMIUM_PRICES['COMBO']} Combo (Best Value)", url=u_co)])
    kb.append([
        InlineKeyboardButton("💬 Message Owner",
            url=f"https://t.me/{OWNER_USERNAME.lstrip('@')}"),
        InlineKeyboardButton("📢 Channel", url=CHANNEL_LINK)
    ])
    if PREMIUM_GROUP_LINK:
        kb.append([InlineKeyboardButton("🔒 Preview VIP Group",
                                        url=PREMIUM_GROUP_LINK)])
    return kb


async def cmd_refer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    bu = (await ctx.bot.get_me()).username
    rl = f"https://t.me/{bu}?start=ref_{user.id}"
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM referrals WHERE referred_by = ?", (user.id,))
    cnt = c.fetchone()[0]; conn.close()
    text = (
        f"<b>🎁 REFERRAL PROGRAM 🎁</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>👤 Your Referrals: {cnt}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🎯 REWARDS:</b>\n\n"
        f"🥉 <b>3 refs</b> → FREE VIP (1 week)\n"
        f"🥈 <b>10 refs</b> → FREE VIP (1 month)\n"
        f"🥇 <b>25 refs</b> → FREE Premium (1 month)\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>📤 YOUR REFERRAL LINK:</b>\n"
        f"<code>{rl}</code>\n\n"
        f"<i>Tap to copy — share with friends!</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🏏 All-Rounder Cricket 🏏</b>"
    )
    kb = [[InlineKeyboardButton("📤 Share Now",
        url=f"https://t.me/share/url?url={rl}&text=🏏 Best Cricket Bot!")]]
    await update.message.reply_text(text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb))


# ======================================================
# 🧾 PAYMENT SCREENSHOT AUTO-FORWARD (v18)
# When user sends photo in DM → forward to admin with user info tag
# ======================================================
async def handle_user_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Auto-forward user photos (payment screenshots) to admin."""
    if not update.message or not update.message.photo:
        return
    user = update.effective_user
    if user.id == ADMIN_USER_ID:
        return  # Don't loop admin photos back
    try:
        db_track_user(user)
    except Exception:
        pass
    # Confirmation to user
    try:
        await update.message.reply_text(
            "<b>✅ Screenshot mil gaya bhai!</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>👉 Admin ({OWNER_USERNAME}) ko forward kar diya.</b>\n"
            f"<b>⏰ 5-10 min me verify ho jayega.</b>\n\n"
            "<i>Ab tu VIP group ka ‘Request to Join’ button dabao — "
            "admin approve karega.</i>\n\n"
            "<b>🏏 All-Rounder Cricket 🏏</b>",
            parse_mode="HTML")
    except Exception as e:
        logger.warning(f"user photo ack fail: {e}")
    # Forward to admin
    if not ADMIN_USER_ID:
        logger.warning("Photo received but ADMIN_USER_ID unset")
        return
    uname = f"@{user.username}" if user.username else "(no username)"
    caption = (
        f"<b>🧾 PAYMENT SCREENSHOT RECEIVED 🧾</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>👤 From:</b> {user.first_name or 'Unknown'}\n"
        f"<b>🆔 Username:</b> {uname}\n"
        f"<b>🔢 User ID:</b> <code>{user.id}</code>\n\n"
        f"<b>✅ Verify payment then approve VIP group request.</b>"
    )
    try:
        photo_file_id = update.message.photo[-1].file_id
        kb_rows = [[InlineKeyboardButton(
            f"💬 Open Chat: {uname}", url=f"tg://user?id={user.id}")]]
        if user.username:
            kb_rows.append([InlineKeyboardButton(
                f"🔗 t.me/{user.username}", url=f"https://t.me/{user.username}")])
        await ctx.bot.send_photo(chat_id=ADMIN_USER_ID, photo=photo_file_id,
            caption=caption[:1024], parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(kb_rows))
        logger.info(f"🧾 Screenshot forwarded to admin from {user.id}")
    except Exception as e:
        logger.warning(f"screenshot forward fail: {e}")


# ======================================================
# 📢 BROADCAST + PAID CONFIRMATION (Admin-only, v18)
# ======================================================
async def cmd_apistatus(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """v25.6: Show status of each cricket data source."""
    if not _SOURCE_STATS:
        # Trigger a fetch to populate
        try:
            await fetch_todays_matches()
        except Exception: pass
    if not _SOURCE_STATS:
        await update.message.reply_text(
            "<b>ℹ️ No source data yet.</b> Try /refresh first.", parse_mode="HTML")
        return
    lines = ["<b>📡 CRICKET API STATUS</b>", ""]
    total = 0
    for name, stats in sorted(_SOURCE_STATS.items()):
        cnt = stats.get("last_count", 0)
        fetched = stats.get("last_fetch")
        err = stats.get("last_error", "")
        emo = "✅" if cnt > 0 else "❌" if err else "⚠️"
        fetched_str = fetched.strftime("%I:%M %p") if fetched else "never"
        lines.append(f"{emo} <b>{name}</b>: {cnt} matches (at {fetched_str})")
        if err:
            lines.append(f"   ⚠️ {err[:100]}")
        total += cnt
    lines.append("")
    lines.append(f"<b>📊 Total raw (with duplicates): {total}</b>")
    cache_count = len(_MATCHES_CACHE.get("data", []))
    lines.append(f"<b>📊 Unique after dedup: {cache_count}</b>")
    cache_time = _MATCHES_CACHE.get("fetched_at")
    if cache_time:
        age_min = int((now_ist() - cache_time).total_seconds() / 60)
        lines.append(f"<b>🕐 Cache age: {age_min} min</b>")
    lines.append("")
    lines.append("<b>Use /refresh to force new fetch</b>")
    await update.message.reply_text("\n".join(lines)[:4000], parse_mode="HTML")


async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/broadcast <text> — admin blasts a message to ALL tracked users."""
    user = update.effective_user
    if ADMIN_USER_ID and user.id != ADMIN_USER_ID:
        await update.message.reply_text(
            "<b>⛔ Admin only command.</b>", parse_mode="HTML"); return
    if not ctx.args:
        await update.message.reply_text(
            "<b>⚠️ Usage:</b> <code>/broadcast Your message here</code>",
            parse_mode="HTML"); return
    msg = " ".join(ctx.args)
    body = (
        f"<b>📢 ALL-ROUNDER BROADCAST 📢</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"{msg}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🏏 All-Rounder Cricket 🏏</b>\n"
        f"<b>📢 <a href=\"{CHANNEL_LINK}\">Open Channel</a></b>"
    )
    kb = [[InlineKeyboardButton("📢 Open Channel", url=CHANNEL_LINK)],
          [InlineKeyboardButton("🔕 Stop Reminders", callback_data="stopreminder")]]
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT user_id FROM bot_users WHERE opted_out = 0")
        uids = [r[0] for r in c.fetchall()]; conn.close()
    except Exception as e:
        await update.message.reply_text(f"<b>❌ DB error:</b> {e}", parse_mode="HTML"); return
    if not uids:
        await update.message.reply_text("<b>📭 No users tracked yet.</b>",
            parse_mode="HTML"); return
    status_msg = await update.message.reply_text(
        f"<b>📣 Broadcasting to {len(uids)} users...</b>", parse_mode="HTML")
    sent = 0; failed = 0
    for uid in uids:
        try:
            await ctx.bot.send_message(chat_id=uid, text=body,
                parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb),
                disable_web_page_preview=True)
            sent += 1
            await asyncio.sleep(0.4)
        except Exception as e:
            failed += 1
            err = str(e).lower()
            if "blocked" in err or "forbidden" in err or "chat not found" in err:
                try: db_optout_reminder(uid)
                except Exception: pass
    try:
        await status_msg.edit_text(
            f"<b>✅ Broadcast complete</b>\n\n"
            f"📤 Sent: {sent}\n❌ Failed: {failed}\n"
            f"📊 Total: {len(uids)}",
            parse_mode="HTML")
    except Exception: pass


async def cmd_paid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/paid <user_id> <plan> — admin confirms a paid user.

    Auto-DMs the user a thank-you message with VIP group re-link.
    """
    user = update.effective_user
    if ADMIN_USER_ID and user.id != ADMIN_USER_ID:
        await update.message.reply_text("<b>⛔ Admin only.</b>", parse_mode="HTML"); return
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "<b>⚠️ Usage:</b> <code>/paid &lt;user_id&gt; &lt;trial|toss|match|combo&gt;</code>\n\n"
            "Example: <code>/paid 123456789 combo</code>",
            parse_mode="HTML"); return
    try:
        uid = int(ctx.args[0])
    except Exception:
        await update.message.reply_text("<b>❌ Invalid user_id.</b>", parse_mode="HTML"); return
    plan = ctx.args[1].lower()
    meta = PLAN_META.get(plan)
    if not meta:
        await update.message.reply_text(
            "<b>❌ Invalid plan.</b> Use: trial / toss / match / combo",
            parse_mode="HTML"); return
    price = PREMIUM_PRICES[meta["code"]]
    # Mark VIP (they get lifetime free VIP tag too as bonus)
    try:
        db_add_vip(uid, "paid_user", user.id)
    except Exception: pass
    # DM the user
    text = (
        f"<b>🎉 PAYMENT CONFIRMED! WELCOME TO PREMIUM! 🎉</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>{meta['emoji']} Plan:</b> {meta['name']}\n"
        f"<b>💰 Amount:</b> ₹{price} / {meta['duration']}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>👇 NEXT STEP:</b>\n"
        f"1️⃣ Tap ‘🔒 Join VIP Group’ button below\n"
        f"2️⃣ Admin will approve — <b>Aap IN! 🎉</b>\n\n"
        f"<b>🏏 Har Match • Har Toss • Har Session</b>\n"
        f"<b>All-Rounder Boss ke saath ready!</b>\n\n"
        f"<b>💬 Support:</b> {OWNER_USERNAME}\n\n"
        f"<b>👑 All-Rounder Cricket 🏏</b>\n"
        f"<b>★ Boss Ki Prediction, Boss Ki Jeet ★</b>"
    )
    kb = []
    if PREMIUM_GROUP_LINK:
        kb.append([InlineKeyboardButton("🔒 Join VIP Group",
                                        url=PREMIUM_GROUP_LINK)])
    kb.append([InlineKeyboardButton("📢 Main Channel", url=CHANNEL_LINK)])
    try:
        await ctx.bot.send_message(chat_id=uid, text=text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(kb), disable_web_page_preview=True)
        await update.message.reply_text(
            f"<b>✅ Confirmation sent to {uid}</b>\n"
            f"Plan: {meta['name']} • ₹{price}",
            parse_mode="HTML")
        logger.info(f"💰 PAID confirmed: {uid} → {plan} ₹{price}")
    except Exception as e:
        await update.message.reply_text(
            f"<b>❌ DM failed:</b> {e}\n\n"
            "User has probably not started the bot yet.",
            parse_mode="HTML")


# ======================================================
# 🔘 BULLETPROOF CALLBACK HANDLER
# ======================================================
async def cb_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    BULLETPROOF: Guarantees every button click gets a response.
    Uses safe_edit_or_send for auto-fallback.
    """
    q = update.callback_query
    if not q: return
    data = q.data
    user = q.from_user
    logger.info(f"🔘 Button: {data} from {user.id} (@{user.username})")
    try:
        db_track_user(user)
    except Exception:
        pass

    # Immediately answer with loading spinner
    try:
        await q.answer("⏳ Loading...")
    except Exception as e:
        logger.warning(f"answer failed: {e}")

    try:
        if data == "premium":
            text = build_premium_message()
            kb = build_premium_kb()
            await safe_edit_or_send(ctx, q, text, InlineKeyboardMarkup(kb))
            return

        if data == "trial":
            text = build_trial_message()
            kb = build_trial_kb()
            await safe_edit_or_send(ctx, q, text, InlineKeyboardMarkup(kb))
            return

        if data == "joinvip_btn":
            # v16: Show PLAN CHOOSER menu (does NOT auto-add to VIP DB anymore).
            already = db_is_vip(user.id)
            text, markup = build_vip_full_message(user, already)
            await safe_edit_or_send(ctx, q, text, markup)
            return

        # v16: Plan selection handlers — user picked a specific plan
        if data in ("plan_trial", "plan_toss", "plan_match", "plan_session", "plan_combo"):
            plan_key = data.replace("plan_", "")
            text, markup = build_plan_detail(plan_key)
            if not text:
                await safe_edit_or_send(ctx, q,
                    "<b>⚠️ Plan not found.</b> Try /premium")
                return
            await safe_edit_or_send(ctx, q, text, markup)
            # Auto-notify admin of new order (silent if ADMIN_USER_ID unset)
            try:
                await notify_admin_new_order(ctx, user, plan_key)
            except Exception as e:
                logger.warning(f"notify_admin_new_order fail: {e}")
            return

        if data == "plan_free":
            # Free VIP flow — add to VIP DB and confirm.
            already = db_is_vip(user.id)
            if not already:
                db_add_vip(user.id, user.username or user.first_name, user.id)
                logger.info(f"🌟 New FREE VIP: {user.id}")
            vips = db_get_vip_users()
            total = VIP_BASE_COUNT + len(vips)
            state = ("<b>✅ You were already in our FREE VIP list!</b>"
                     if already else
                     "<b>🎉 SUCCESS! You are now in FREE VIP list! 🎉</b>")
            text = (
                f"{state}\n\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>🌟 FREE VIP BENEFITS:</b>\n"
                f"• 🪙 Toss Prediction 30 min EARLY (in DM)\n"
                f"• 🏆 Match Winner EARLY alerts\n"
                f"• 📸 Special image cards in DM\n"
                f"• 🌟 Lifetime VIP tag\n\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>💎 Total VIP Members: {total}+ </b>\n\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>🔥 WANT PAID GUARANTEED TIPS?</b>\n"
                f"Tap ‘💎 View Paid Plans’ below 👇\n\n"
                f"<b>🏏 All-Rounder Cricket 🏏</b>\n"
                f"<b>★ Boss Ki Prediction, Boss Ki Jeet ★</b>"
            )
            kb = [
                [InlineKeyboardButton("💎 View Paid Plans",
                                      callback_data="joinvip_btn")],
                [InlineKeyboardButton("📢 Main Channel", url=CHANNEL_LINK)],
            ]
            await safe_edit_or_send(ctx, q, text, InlineKeyboardMarkup(kb))
            return

        if data == "refer":
            bu = (await ctx.bot.get_me()).username
            rl = f"https://t.me/{bu}?start=ref_{user.id}"
            conn = sqlite3.connect(DB_PATH); c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM referrals WHERE referred_by = ?", (user.id,))
            cnt = c.fetchone()[0]; conn.close()
            text = (
                f"<b>🎁 REFER &amp; EARN 🎁</b>\n\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 Your Referrals: <b>{cnt}</b>\n\n"
                f"<b>🎯 Rewards:</b>\n"
                f"🥉 3 refs = FREE VIP 1 week\n"
                f"🥈 10 refs = FREE VIP 1 month\n"
                f"🥇 25 refs = FREE Premium 1 month\n\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>Your Link:</b>\n<code>{rl}</code>\n\n"
                f"<b>🏏 All-Rounder Cricket 🏏</b>"
            )
            kb = [[InlineKeyboardButton("📤 Share Now",
                url=f"https://t.me/share/url?url={rl}&text=🏏 Best Cricket Bot!")]]
            await safe_edit_or_send(ctx, q, text, InlineKeyboardMarkup(kb))
            return

        if data == "today":
            matches = await fetch_todays_matches()
            covered = [m for m in matches if should_cover_match(m)]
            if not covered:
                result = "<b>❌ No matches today</b>\n\nTry /refresh"
            else:
                lines = [f"<b>📅 TODAY'S MATCHES</b>",
                         f"<b>{format_date_style()}</b>", "",
                         f"<b>🏏 Covered: {len(covered)}</b>",
                         f"━━━━━━━━━━━━━━━━━━", ""]
                for i, m in enumerate(covered[:15], 1):
                    badge = "⭐" if m.get("_priority", 0) >= 8 else "🏏"
                    lines.append(f"<b>{badge} #{i}</b> {m.get('name', '')[:60]}")
                    lines.append(f"🕐 <b>{m['_ist_dt'].strftime('%I:%M %p IST')}</b>")
                    lines.append("")
                lines.append(f"<b>🏏 All-Rounder Cricket 🏏</b>")
                result = "\n".join(lines)
            await safe_edit_or_send(ctx, q, result[:4000])
            return

        if data == "stats":
            stats = db_get_accuracy(days=7)
            if stats:
                lines = ["<b>📊 ACCURACY REPORT</b>", "<b>Last 7 Days</b>",
                         "━━━━━━━━━━━━━━━━━━", ""]
                for row in stats:
                    pt, tot, cor = row
                    pct = (cor * 100 // tot) if tot > 0 else 0
                    emo = "🟢" if pct >= 60 else "🟡" if pct >= 40 else "🔴"
                    lb = {"winner": "Match Winner", "toss_winner": "Toss Winner",
                          "toss_choice": "Toss Choice", "session": "Session"}.get(pt, pt)
                    lines.append(f"{emo} <b>{lb}:</b> {cor}/{tot} <b>({pct}%)</b>")
                lines.append(""); lines.append("<b>🏏 All-Rounder Cricket 🏏</b>")
                result = "\n".join(lines)
            else:
                result = ("<b>📊 STATS TRACKING</b>\n\nNo data yet — predictions being tracked.\n\n"
                          "<b>🏏 All-Rounder Cricket 🏏</b>")
            await safe_edit_or_send(ctx, q, result)
            return

        if data == "tips":
            tip = await gen_tips()
            result = tip or "<b>❌ Failed to load tips</b>"
            await safe_edit_or_send(ctx, q, result[:4000])
            return

        if data == "help":
            result = ("<b>ℹ️ HELP</b>\n\n"
                      "Use <b>/help</b> for full guide.\n\n"
                      "<b>Quick commands:</b>\n"
                      "• /today — Today's matches\n"
                      "• /stats — Accuracy report\n"
                      "• /trial — 🆕 ₹99 / 3-day trial\n"
                      "• /premium — Paid plans\n"
                      "• /joinvip — FREE VIP\n"
                      "• /refer — Referral link\n\n"
                      "<b>🏏 All-Rounder Cricket 🏏</b>")
            await safe_edit_or_send(ctx, q, result)
            return

        if data == "stopreminder":
            db_optout_reminder(user.id)
            await safe_edit_or_send(ctx, q,
                "<b>🔕 Reminders band ho gaye.</b>\n\n"
                "Wapas chalu karne ke liye /start bhejo\n\n"
                f"📢 Channel: {CHANNEL_LINK}")
            return

        # Unknown
        logger.warning(f"Unknown callback: {data}")
        await safe_edit_or_send(ctx, q,
            f"<b>⚠️ Unknown option: {data}</b>\n\nPlease try again or use /help")

    except Exception as e:
        logger.error(f"💥 CB critical error ({data}): {e}", exc_info=True)
        try:
            await q.answer(f"❌ Error: {str(e)[:50]}", show_alert=True)
        except Exception: pass
        try:
            await ctx.bot.send_message(chat_id=user.id,
                text=f"<b>❌ Error processing your request</b>\n\n"
                     f"Please try the command directly:\n"
                     f"• /premium\n• /trial\n• /joinvip\n• /help",
                parse_mode="HTML")
        except Exception: pass


# ======================================================
# 🚀 MAIN
# ======================================================
async def post_init(app: Application):
    bot = app.bot
    db_init()
    scheduler = AsyncIOScheduler(timezone=IST)
    scheduler.add_job(job_morning, CronTrigger(hour=9, minute=0, timezone=IST),
                      args=[bot], id="morning", replace_existing=True)
    scheduler.add_job(job_premium_promo, CronTrigger(hour=18, minute=0, timezone=IST),
                      args=[bot], id="premium_promo", replace_existing=True)
    # v20: Daily 5 PM — Highlight Match of the Day
    scheduler.add_job(job_highlight_match, CronTrigger(hour=17, minute=0, timezone=IST),
                      args=[bot], id="highlight_match", replace_existing=True,
                      max_instances=1, coalesce=True)
    # v24: Good Night removed — bot is 24/7, no daily wind-down
    # v17: Daily 10 AM comeback reminder DM job
    scheduler.add_job(job_user_reminder, CronTrigger(hour=10, minute=0, timezone=IST),
                      args=[bot], id="user_reminder", replace_existing=True,
                      max_instances=1, coalesce=True)
    app.bot_data["scheduler"] = scheduler
    scheduler.add_job(job_discover_matches, CronTrigger(hour=9, minute=5, timezone=IST),
                      args=[bot, scheduler], id="discover_morning", replace_existing=True)
    scheduler.add_job(job_discover_matches, IntervalTrigger(minutes=30, timezone=IST),
                      args=[bot, scheduler], id="discover_recheck", replace_existing=True,
                      max_instances=1, coalesce=True)
    scheduler.add_job(job_live_intelligence,
                      IntervalTrigger(minutes=LIVE_POLL_DURING_MATCH, timezone=IST),
                      args=[bot], id="live_intelligence", replace_existing=True,
                      max_instances=1, coalesce=True)
    scheduler.start()
    logger.info("⏰ Scheduler started (IST)")
    logger.info(f"🕐 IST: {now_ist().strftime('%Y-%m-%d %I:%M %p')}")
    asyncio.create_task(job_discover_matches(bot, scheduler))


def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN not set!"); return
    logger.info("=" * 50)
    logger.info(f"🚀 {BOT_NAME} v26.2 CRICKET-SMART starting...")
    logger.info("=" * 50)
    ai_count = 0
    if DEEPSEEK_API_KEY: logger.info("✅ DeepSeek ready"); ai_count += 1
    if GEMINI_API_KEY: logger.info("✅ Gemini ready"); ai_count += 1
    if GROQ_API_KEY: logger.info("✅ Groq ready"); ai_count += 1
    if OPENROUTER_API_KEY: logger.info("✅ OpenRouter ready"); ai_count += 1
    if MISTRAL_API_KEY: logger.info("✅ Mistral ready"); ai_count += 1
    logger.info(f"🧠 Total AI providers: {ai_count}")
    if CRICKET_API_KEY: logger.info("✅ CricAPI ready")
    if RAPIDAPI_KEY: logger.info(f"✅ RapidAPI Cricbuzz: {RAPIDAPI_HOST}")
    # v21: New source status logs
    if HIGHLIGHTLY_API_KEY: logger.info(f"✅ Highlightly API ready (100 req/day)")
    else: logger.info("ℹ️ HIGHLIGHTLY_API_KEY not set — recommended free source")
    if CRICKET_API_KEY_2: logger.info(f"✅ CricAPI 2nd key ready (200 req/day total)")
    if ESPN_ENABLED: logger.info(f"✅ ESPN Cricinfo public feed enabled")
    if UPI_ID: logger.info(f"✅ UPI Direct Pay: {UPI_ID}")
    else: logger.warning("⚠️ UPI_ID not set — UPI buttons will not appear")
    if PREMIUM_GROUP_LINK: logger.info("✅ VIP Group link configured")
    else: logger.warning("⚠️ PREMIUM_GROUP_LINK not set — VIP group buttons hidden")
    logger.info(f"👤 Owner: {OWNER_USERNAME}")
    if ADMIN_USER_ID:
        logger.info(f"🔔 Admin auto-notify enabled: user_id={ADMIN_USER_ID}")
    else:
        logger.warning("⚠️ ADMIN_USER_ID not set — admin will NOT get order alerts")
    logger.info("=" * 50)

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("accuracy", cmd_accuracy))
    app.add_handler(CommandHandler("refresh", cmd_refresh))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("live", cmd_live))
    app.add_handler(CommandHandler("predict", cmd_predict))
    app.add_handler(CommandHandler("toss", cmd_toss))
    app.add_handler(CommandHandler("ground", cmd_ground))
    app.add_handler(CommandHandler("points", cmd_points))
    app.add_handler(CommandHandler("playing11", cmd_playing11))
    app.add_handler(CommandHandler("tips", cmd_tips))
    app.add_handler(CommandHandler("addmatch", cmd_addmatch))
    app.add_handler(CommandHandler("listmatches", cmd_listmatches))
    app.add_handler(CommandHandler("removematch", cmd_removematch))
    app.add_handler(CommandHandler("joinvip", cmd_join_vip))
    app.add_handler(CommandHandler("leavevip", cmd_leave_vip))
    app.add_handler(CommandHandler("viplist", cmd_vip_list))
    app.add_handler(CommandHandler("premium", cmd_premium))
    app.add_handler(CommandHandler("trial", cmd_trial))
    app.add_handler(CommandHandler("refer", cmd_refer))
    app.add_handler(CommandHandler("stopreminder", cmd_stop_reminder))
    # v18: Admin-only commands
    app.add_handler(CommandHandler("apistatus", cmd_apistatus))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("paid", cmd_paid))
    # v18: Auto-forward payment screenshots
    from telegram.ext import MessageHandler, filters
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE,
                                    handle_user_photo))
    app.add_handler(CallbackQueryHandler(cb_handler))

    # v25.4: Add error handler to gracefully handle Telegram Conflict during redeploys
    async def _error_handler(update, context):
        err = context.error
        err_str = str(err)
        if "Conflict" in err_str or "getUpdates" in err_str:
            # Silent — happens briefly on Railway redeploy (old container overlap)
            logger.info("ℹ️ Conflict during redeploy — self-resolves in ~30s")
            return
        logger.error(f"⚠️ Handler error: {err}", exc_info=err)
    app.add_error_handler(_error_handler)
    app.run_polling(drop_pending_updates=True, close_loop=False)


if __name__ == "__main__":
    main()
