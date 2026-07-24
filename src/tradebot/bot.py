from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, time
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from telegram import Bot, InputFile, ReplyParameters
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationBuilder, ContextTypes, Defaults

load_dotenv()

IST = ZoneInfo("Asia/Kolkata")
STATE_FILE = Path("state.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tradebot")


def env(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


def env_bool(name: str, default: bool = False) -> bool:
    return env(name, str(default)).lower() in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int) -> int:
    return int(env(name, str(default)))


def env_float(name: str, default: float) -> float:
    return float(env(name, str(default)))


def env_csv(name: str, default: str = "") -> List[str]:
    return [x.strip() for x in env(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = env("TELEGRAM_BOT_TOKEN", env("BOT_TOKEN", ""))
    telegram_chat_id: str = env("TELEGRAM_CHAT_ID", "")

    broker: str = env("BROKER", "mock").lower()
    fyers_client_id: str = env("FYERS_CLIENT_ID", "")
    fyers_access_token: str = env("FYERS_ACCESS_TOKEN", "")

    scan_symbols: List[str] = field(default_factory=lambda: env_csv("SCAN_SYMBOLS", "NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX"))
    display_names: List[str] = field(default_factory=lambda: env_csv("DISPLAY_NAMES", "NIFTY 50 OPTIONS,BANKNIFTY OPTIONS"))

    auto_atm_enabled: bool = env_bool("AUTO_ATM_ENABLED", False)
    underlying_symbols: List[str] = field(default_factory=lambda: env_csv("UNDERLYING_SYMBOLS", "NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX"))
    underlying_names: List[str] = field(default_factory=lambda: env_csv("UNDERLYING_NAMES", "NIFTY,BANKNIFTY"))
    option_expiries: List[str] = field(default_factory=lambda: env_csv("OPTION_EXPIRIES", ""))
    option_strike_steps: List[str] = field(default_factory=lambda: env_csv("OPTION_STRIKE_STEPS", "50,100"))
    option_types: List[str] = field(default_factory=lambda: env_csv("OPTION_TYPES", "CE,PE"))
    option_symbol_template: str = env("OPTION_SYMBOL_TEMPLATE", "NSE:{UNDERLYING}{EXPIRY}{STRIKE}{TYPE}")
    atm_strike_range: int = env_int("ATM_STRIKE_RANGE", 5)
    min_setup_candidates: int = env_int("MIN_SETUP_CANDIDATES", 20)
    max_analysis_candidates: int = env_int("MAX_ANALYSIS_CANDIDATES", 40)

    post_author_name: str = env("POST_AUTHOR_NAME", "JIGA BHAI TRADER")

    max_calls_per_day: int = env_int("MAX_CALLS_PER_DAY", 5)
    min_calls_per_day: int = env_int("MIN_CALLS_PER_DAY", 2)
    scan_interval_seconds: int = env_int("SCAN_INTERVAL_SECONDS", 60)
    trail_interval_seconds: int = env_int("TRAIL_INTERVAL_SECONDS", 15)
    max_risk_points: float = env_float("MAX_RISK_POINTS", 80)
    min_rrr: float = env_float("MIN_RRR", 3)
    close_on_target: int = env_int("CLOSE_ON_TARGET", 3)
    point_update_step: float = env_float("POINT_UPDATE_STEP", 10)

    vip_link: str = env("VIP_LINK", "https://t.me/your_vip_link")
    vip_promo_delay_minutes: int = env_int("VIP_PROMO_DELAY_MINUTES", 10)
    generate_charts: bool = env_bool("GENERATE_CHARTS", True)

    ai_filter_enabled: bool = env_bool("AI_FILTER_ENABLED", False)
    ai_api_key: str = env("AI_API_KEY", "")
    ai_api_base_url: str = env("AI_API_BASE_URL", "https://openrouter.ai/api/v1")
    ai_model: str = env("AI_MODEL", "openai/gpt-4o-mini")
    ai_min_confidence: int = env_int("AI_MIN_CONFIDENCE", 70)
    ai_fail_closed: bool = env_bool("AI_FAIL_CLOSED", True)
    ai_timeout_seconds: int = env_int("AI_TIMEOUT_SECONDS", 20)

    def validate(self) -> None:
        missing = []
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN or BOT_TOKEN")
        if not self.telegram_chat_id:
            missing.append("TELEGRAM_CHAT_ID")
        if self.broker not in {"mock", "fyers"}:
            raise RuntimeError("BROKER must be mock or fyers")
        if self.broker == "fyers":
            if not self.fyers_client_id:
                missing.append("FYERS_CLIENT_ID")
            if not self.fyers_access_token:
                missing.append("FYERS_ACCESS_TOKEN")
        if missing:
            raise RuntimeError("Missing required env variables: " + ", ".join(missing))
        if self.min_rrr < 3:
            raise RuntimeError("MIN_RRR must be 3 or higher")
        if not (1 <= self.close_on_target <= 3):
            raise RuntimeError("CLOSE_ON_TARGET must be 1, 2, or 3")
        if self.point_update_step <= 0:
            raise RuntimeError("POINT_UPDATE_STEP must be greater than 0")
        if self.auto_atm_enabled and not self.option_expiries:
            raise RuntimeError("AUTO_ATM_ENABLED=true requires OPTION_EXPIRIES")


@dataclass
class Quote:
    symbol: str
    ltp: float
    timestamp: datetime


@dataclass
class Signal:
    symbol: str
    display_name: str
    direction: str
    entry: float
    stop_loss: float
    targets: List[float]
    risk_points: float
    reason: str
    rrr: float
    score: float = 0.0


@dataclass
class Trade:
    id: str
    symbol: str
    display_name: str
    direction: str
    entry: float
    stop_loss: float
    targets: List[float]
    risk_points: float
    reason: str
    telegram_message_id: int
    status: str = "ACTIVE"
    opened_at: str = field(default_factory=lambda: datetime.now(IST).isoformat())
    closed_at: Optional[str] = None
    highest_price: float = 0.0
    last_price: float = 0.0
    last_update_price: float = 0.0
    hit_targets: List[int] = field(default_factory=list)
    chart_sent_targets: List[int] = field(default_factory=list)
    promo_sent: bool = False


class StateStore:
    def load(self) -> Dict[str, Trade]:
        if not STATE_FILE.exists():
            return {}
        try:
            data = json.loads(STATE_FILE.read_text())
            trades: Dict[str, Trade] = {}
            for tid, trade_data in data.get("trades", {}).items():
                trade_data.setdefault("last_update_price", trade_data.get("entry", 0.0))
                trade_data.setdefault("chart_sent_targets", [])
                trades[tid] = Trade(**trade_data)
            return trades
        except Exception as exc:
            log.warning("Could not load state.json: %s", exc)
            return {}

    def save(self, trades: Dict[str, Trade]) -> None:
        STATE_FILE.write_text(json.dumps({"trades": {tid: asdict(t) for tid, t in trades.items()}}, indent=2))

    def active(self, trades: Dict[str, Trade]) -> List[Trade]:
        return [t for t in trades.values() if t.status == "ACTIVE"]


class Broker:
    def quotes(self, symbols: List[str]) -> Dict[str, Quote]:
        raise NotImplementedError

    def history(self, symbol: str, resolution: str = "5", days: int = 5) -> pd.DataFrame:
        raise NotImplementedError


class FyersBroker(Broker):
    def __init__(self, settings: Settings) -> None:
        from fyers_apiv3 import fyersModel
        self.client_id = settings.fyers_client_id.strip()
        self.access_token = self._normalize_access_token(settings.fyers_access_token, self.client_id)
        log.info("Fyers init: client_id=%s token_len=%s token_prefix=%s", self.client_id, len(self.access_token), self.access_token[:3] + "***" if self.access_token else "EMPTY")
        self.fyers = fyersModel.FyersModel(client_id=self.client_id, token=self.access_token, is_async=False, log_path="")

    @staticmethod
    def _normalize_access_token(token: str, client_id: str) -> str:
        token = (token or "").strip().strip('"').strip("'")
        token = token.replace("\n", "").replace("\r", "").replace(" ", "")
        if token.lower().startswith("bearer"):
            token = token[6:].strip()
        if client_id and token.startswith(client_id + ":"):
            token = token.split(":", 1)[1]
        return token

    def _raise_fyers_error(self, where: str, response: Dict[str, Any]) -> None:
        code = response.get("code")
        if code in {-15, -16}:
            raise RuntimeError(f"Fyers auth failed in {where}: invalid/expired token or app permission issue. Response={response}")
        raise RuntimeError(f"Fyers {where} error: {response}")

    def quotes(self, symbols: List[str]) -> Dict[str, Quote]:
        response = self.fyers.quotes(data={"symbols": ",".join(symbols)})
        if response.get("s") != "ok":
            self._raise_fyers_error("quote", response)
        now = datetime.now(IST)
        out: Dict[str, Quote] = {}
        for row in response.get("d", []):
            sym = row.get("n")
            values = row.get("v", {})
            ltp = values.get("lp") or values.get("ltp")
            if sym and ltp is not None:
                out[sym] = Quote(sym, float(ltp), now)
        return out

    def history(self, symbol: str, resolution: str = "5", days: int = 5) -> pd.DataFrame:
        to_dt = datetime.now(IST)
        from_dt = to_dt - timedelta(days=days)
        payload = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": from_dt.strftime("%Y-%m-%d"),
            "range_to": to_dt.strftime("%Y-%m-%d"),
            "cont_flag": "1",
        }
        response = self.fyers.history(data=payload)
        if response.get("s") != "ok":
            self._raise_fyers_error(f"history for {symbol}", response)
        df = pd.DataFrame(response.get("candles", []), columns=["timestamp", "open", "high", "low", "close", "volume"])
        if df.empty:
            return df
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert(IST)
        return df[["datetime", "open", "high", "low", "close", "volume"]]


class MockBroker(Broker):
    def __init__(self) -> None:
        self.prices: Dict[str, float] = {}

    def quotes(self, symbols: List[str]) -> Dict[str, Quote]:
        now = datetime.now(IST)
        out = {}
        for sym in symbols:
            if sym not in self.prices:
                self.prices[sym] = 220 + random.random() * 80
            self.prices[sym] = max(10, self.prices[sym] + random.uniform(-2.5, 7.5))
            out[sym] = Quote(sym, round(self.prices[sym], 2), now)
        return out

    def history(self, symbol: str, resolution: str = "5", days: int = 5) -> pd.DataFrame:
        now = datetime.now(IST).replace(second=0, microsecond=0)
        periods = max(100, days * 75)
        price = self.prices.get(symbol, 220 + random.random() * 80)
        rows = []
        for i in range(periods):
            dt = now - timedelta(minutes=5 * (periods - i))
            move = math.sin(i / 8) * 1.2 + random.uniform(-2, 3) + (8 if i == periods - 1 else 0)
            op = price
            cl = max(5, op + move)
            hi = max(op, cl) + random.uniform(0.5, 2.5)
            lo = min(op, cl) - random.uniform(0.5, 2.5)
            vol = random.randint(15000, 90000) * (3 if i == periods - 1 else 1)
            rows.append([dt, op, hi, lo, cl, vol])
            price = cl
        self.prices[symbol] = rows[-1][4]
        return pd.DataFrame(rows, columns=["datetime", "open", "high", "low", "close", "volume"])


def make_broker(settings: Settings) -> Broker:
    return FyersBroker(settings) if settings.broker == "fyers" else MockBroker()


class DailyLimit:
    def __init__(self) -> None:
        self.date = ""
        self.calls = 0
        self.symbols: set[str] = set()

    def reset_if_new_day(self) -> None:
        today = datetime.now(IST).strftime("%Y-%m-%d")
        if self.date != today:
            self.date = today
            self.calls = 0
            self.symbols.clear()


def is_weekday() -> bool:
    return datetime.now(IST).weekday() < 5


def in_market_window() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    cur = now.time()
    return time(9, 15) <= cur <= time(11, 30) or time(13, 30) <= cur <= time(15, 15)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()
    typical = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap"] = (typical * df["volume"]).cumsum() / df["volume"].replace(0, np.nan).cumsum()
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    return df


class Strategy:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.limit = DailyLimit()

    def scan_one(self, symbol: str, display: str, df: pd.DataFrame) -> Optional[Signal]:
        self.limit.reset_if_new_day()
        if self.limit.calls >= self.settings.max_calls_per_day or symbol in self.limit.symbols or len(df) < 50:
            return None
        df = add_indicators(df).dropna().reset_index(drop=True)
        if len(df) < 30:
            return None
        last, prev = df.iloc[-1], df.iloc[-2]
        lookback = df.iloc[-25:-1]
        resistance = float(lookback["high"].max())
        swing_low = float(df.iloc[-10:]["low"].min())
        entry = float(last["close"])
        atr = float(last["atr14"])
        breakout = entry > resistance and float(prev["close"]) <= resistance
        trend_ok = entry > float(last["ema9"]) > float(last["ema21"]) and entry > float(last["vwap"])
        volume_ratio = float(last["volume"]) / max(1.0, float(last["vol_ma20"]))
        candle_range = max(0.01, float(last["high"]) - float(last["low"]))
        body_ratio = (float(last["close"]) - float(last["open"])) / candle_range
        if not (breakout and trend_ok and volume_ratio >= 1.20 and body_ratio > 0.40):
            return None
        sl = min(entry - atr * 0.75, swing_low)
        risk = round(entry - sl, 2)
        if risk <= 0 or risk > self.settings.max_risk_points:
            return None
        targets = [round(entry + risk * r, 2) for r in (3, 4, 5)]
        rrr = round((targets[0] - entry) / risk, 2)
        if rrr < self.settings.min_rrr:
            return None
        score = round((volume_ratio * 25) + (body_ratio * 25) + (max(0, (entry - resistance) / risk) * 30) + (rrr * 5), 2)
        return Signal(symbol, display, "BUY", round(entry, 2), round(sl, 2), targets, risk, "Momentum breakout with volume and trend confirmation", rrr, score)

    def mark_posted(self, signal: Signal) -> None:
        self.limit.reset_if_new_day()
        self.limit.calls += 1
        self.limit.symbols.add(signal.symbol)


class AITradeFilter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def review(self, signal: Signal, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if not self.settings.ai_filter_enabled:
            return True, {"decision": "SKIPPED"}
        if not self.settings.ai_api_key:
            return (False, {"decision": "REJECT", "reason": "AI key missing"}) if self.settings.ai_fail_closed else (True, {"decision": "APPROVE"})
        try:
            candles = []
            for _, row in df.tail(15).iterrows():
                candles.append({"open": round(float(row["open"]), 2), "high": round(float(row["high"]), 2), "low": round(float(row["low"]), 2), "close": round(float(row["close"]), 2), "volume": int(row.get("volume", 0))})
            setup = {"instrument": signal.display_name, "entry": signal.entry, "sl": signal.stop_loss, "targets": signal.targets, "rrr": signal.rrr, "score": signal.score, "candles": candles}
            url = self.settings.ai_api_base_url.rstrip("/") + "/chat/completions"
            body = {
                "model": self.settings.ai_model,
                "messages": [
                    {"role": "system", "content": "Strict intraday risk filter. Return JSON only. Approve only clean 1:3+ momentum setups. No profit promises."},
                    {"role": "user", "content": "Return JSON keys: decision APPROVE/REJECT, confidence 0-100, reason. Setup:\n" + json.dumps(setup)},
                ],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            }
            r = requests.post(url, headers={"Authorization": f"Bearer {self.settings.ai_api_key}", "Content-Type": "application/json"}, json=body, timeout=self.settings.ai_timeout_seconds)
            r.raise_for_status()
            data = json.loads(r.json()["choices"][0]["message"]["content"])
            ok = str(data.get("decision", "REJECT")).upper() == "APPROVE" and int(data.get("confidence", 0)) >= self.settings.ai_min_confidence
            return ok, data
        except Exception as exc:
            log.exception("AI review failed: %s", exc)
            return (False, {"decision": "REJECT", "reason": str(exc)}) if self.settings.ai_fail_closed else (True, {"decision": "APPROVE"})


def fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.2f}"


def brand_signature() -> str:
    return f"\n\n<b>— {escape(env('POST_AUTHOR_NAME', 'JIGA BHAI TRADER'))}</b>"


def good_morning_message() -> str:
    return "🌞 <b>GOOD MORNING TRADERS</b> 🌞\n\n👑 <b>JIGA BHAI GUJARATI TRADER</b> 👑\n\nAaj ka kaam simple hai — <b>level wait karo, move pakdo, profit protect karo.</b>\n\n🚩 Focus: Nifty | Banknifty | Strong Momentum\n🎯 Setup: High RRR only\n🛡 Rule: Entry ke baad emotion nahi, sirf discipline\n\n🔔 Channel active rakho. Best move fast aata hai." + brand_signature()


def ready_alert_message() -> str:
    return "⚡️ <b>READY ALERT</b> ⚡️\n\nMarket open hone wala hai. Ab bas levels aur price action par focus.\n\n🚩 9:15 ke baad first plan\n📊 Clean breakout ka wait\n🎯 Target clear, SL strict\n🔥 Late entry avoid\n\nPhone ready rakho. Jiga Bhai desk active hai." + brand_signature()


def next_trading_day_label() -> str:
    now = datetime.now(IST)
    nxt = now + timedelta(days=3 if now.weekday() == 4 else 2 if now.weekday() == 5 else 1)
    return nxt.strftime("%d %b")


def levels_text(level_rows: List[Dict[str, Any]], mode: str) -> str:
    if not level_rows:
        return "Live levels price action ke saath update honge."
    rows = []
    for r in level_rows:
        if mode == "open":
            rows.append(f"🚩 <b>{escape(r['name'])}</b>\nBuy Above 🔸 <b>{fmt(r['breakout'])}</b>\nWeak Below 🔸 <b>{fmt(r['breakdown'])}</b>\nKey Zone 🔸 <b>{fmt(r['support'])}</b> - <b>{fmt(r['resistance'])}</b>")
        else:
            rows.append(f"🚩 <b>{escape(r['name'])}</b>\nResistance 🔸 <b>{fmt(r['resistance'])}</b>\nSupport 🔸 <b>{fmt(r['support'])}</b>\nBreakout 🔸 <b>{fmt(r['breakout'])}+</b> | Breakdown 🔸 <b>{fmt(r['breakdown'])}-</b>")
    return "\n\n".join(rows)


def opening_plan_message(level_rows: List[Dict[str, Any]], vip_link: str) -> str:
    return f"🚨 <b>FIRST MARKET PLAN</b> 🚨\n⏰ <b>9:15 - 9:30 WINDOW</b>\n\n{levels_text(level_rows, 'open')}\n\n🎯 Clean breakout mila to entry.\n🛡 Confirmation nahi to no trade.\n🔥 Capital safe, profit aggressive.\n\n👑 VIP Desk: {escape(vip_link)}" + brand_signature()


def next_day_plan_message(level_rows: List[Dict[str, Any]], vip_link: str) -> str:
    return f"🌙 <b>NEXT DAY LEVELS - {escape(next_trading_day_label())}</b> 🌙\n\n{levels_text(level_rows, 'next')}\n\n🎯 First 15 min wait. Fake move me entry nahi.\n🚀 Breakout + volume = action zone.\n🛡 SL strict. Overtrade zero.\n\n👑 VIP Priority Levels: {escape(vip_link)}" + brand_signature()


def live_call(signal: Signal) -> str:
    t = signal.targets
    return f"🚩 <b>{escape(signal.display_name)} - {datetime.now(IST).strftime('%d %b').upper()}</b>\n\nPLAN ABOVE 🔸 <b>{fmt(signal.entry)}</b>\n\nTarget 🔸 <b>{fmt(t[0])}</b> 🚩<b>{fmt(t[1])}</b> 🚩<b>{fmt(t[2])}</b>\n\nSL 🔸 <b>{fmt(signal.stop_loss)}</b>  PREMIUM 📊✅✅📊\n\nInquiry for VIP :- {escape(env('VIP_LINK', 'https://jigabhaivip.com'))}" + brand_signature()


def point_update_message(trade: Trade, ltp: float, points: float) -> str:
    return f"🐂 <b>{escape(trade.display_name)} - {datetime.now(IST).strftime('%d %b').upper()}</b> 🐂\n\n↗️ Range..  <b>{fmt(trade.entry)} TO {fmt(ltp)}</b> 🚀🚀\n✅ Running: <b>+{fmt(points)} pts</b>" + brand_signature()


def target_hit_caption(trade: Trade, target_no: int, ltp: float) -> str:
    return f"🎯 <b>TARGET {target_no} HIT</b> 🎯\n\n🐂 <b>{escape(trade.display_name)}</b>\nRange.. <b>{fmt(trade.entry)} TO {fmt(ltp)}</b> 🚀\n\n💰 Book profit. Remaining trail." + brand_signature()


def closed_message(trade: Trade, ltp: float, reason: str) -> str:
    return f"✅ <b>TRADE CLOSED</b> ✅\n\n🚩 <b>{escape(trade.display_name)}</b>\nEntry 🔸 <b>{fmt(trade.entry)}</b>\nExit 🔸 <b>{fmt(ltp)}</b>\nResult 🔸 <b>{escape(reason)}</b>\n\nNext clean setup ka wait." + brand_signature()


def vip_promo(vip_link: str) -> str:
    return f"🔥 <b>VIP DESK OPEN</b> 🔥\n\n🎁 <b>Rs 8000/-</b> 01 Month - Basic Strike\n🎁 <b>Rs 15000/-</b> 01 Month - Pro Hunter\n🎁 <b>Rs 28000/-</b> 01 Month - Elite Sniper\n🎁 <b>Rs 50000/-</b> 01 Month - Inner Circle\n\nDetails 👉 {escape(vip_link)}\n\n✅ Nifty | Banknifty | Stock Setups\n✅ Entry + Target + SL\n✅ Live Guidance\n✅ Serious traders only\n\n⚠️ Market risk rahega. Discipline mandatory.\n\n👇👇👇\nDM NOW 👉 ON WHATSAPP NOW 👈" + brand_signature()


def save_chart(df: pd.DataFrame, signal_or_trade: Signal | Trade, title_suffix: str = "Target Hit") -> Optional[str]:
    try:
        import mplfinance as mpf
        import matplotlib.pyplot as plt
    except Exception:
        return None
    try:
        if df.empty:
            return None
        Path("charts").mkdir(exist_ok=True)
        chart_df = df.tail(90).copy()
        chart_df["datetime"] = pd.to_datetime(chart_df["datetime"])
        chart_df = chart_df.set_index("datetime")
        mc = mpf.make_marketcolors(up="#16a34a", down="#dc2626", edge="inherit", wick="inherit", volume="in")
        style = mpf.make_mpf_style(base_mpf_style="yahoo", marketcolors=mc, facecolor="#ffffff", figcolor="#ffffff", gridcolor="#e5e7eb", gridstyle="--")
        hlines = dict(hlines=[signal_or_trade.entry, signal_or_trade.stop_loss, *signal_or_trade.targets], colors=["#ca8a04", "#dc2626", "#16a34a", "#0d9488", "#7c3aed"], linestyle=["-", "--", "--", "--", "--"], linewidths=[1.4, 1.2, 1.2, 1.2, 1.2])
        path = f"charts/{signal_or_trade.symbol.replace(':', '_').replace('/', '_')}_{datetime.now(IST).strftime('%H%M%S')}.png"
        fig, _ = mpf.plot(chart_df, type="candle", style=style, volume=True, hlines=hlines, title=f"\n{signal_or_trade.display_name} | {title_suffix}", figsize=(16, 9), returnfig=True, tight_layout=True)
        fig.savefig(path, dpi=200, bbox_inches="tight", facecolor="#ffffff")
        plt.close(fig)
        return path
    except Exception as exc:
        log.warning("Chart generation failed: %s", exc)
        return None


class TradeManager:
    def __init__(self, settings: Settings, broker: Broker) -> None:
        self.settings = settings
        self.broker = broker
        self.strategy = Strategy(settings)
        self.ai_filter = AITradeFilter(settings)
        self.store = StateStore()
        self.trades = self.store.load()

    def _atm_strike(self, spot: float, step: float) -> int:
        return int(round(spot / step) * step)

    async def build_scan_candidates(self) -> List[Tuple[str, str]]:
        if not self.settings.auto_atm_enabled:
            return [(s, self.settings.display_names[i] if i < len(self.settings.display_names) else s) for i, s in enumerate(self.settings.scan_symbols)]
        quotes = await asyncio.to_thread(self.broker.quotes, self.settings.underlying_symbols)
        candidates: List[Tuple[str, str]] = []
        offsets = [0]
        for i in range(1, self.settings.atm_strike_range + 1):
            offsets.extend([-i, i])
        for idx, usym in enumerate(self.settings.underlying_symbols):
            q = quotes.get(usym)
            if not q:
                continue
            uname = self.settings.underlying_names[idx] if idx < len(self.settings.underlying_names) else usym.replace("NSE:", "").replace("-INDEX", "")
            expiry = self.settings.option_expiries[idx] if idx < len(self.settings.option_expiries) else self.settings.option_expiries[0]
            step = float(self.settings.option_strike_steps[idx] if idx < len(self.settings.option_strike_steps) else self.settings.option_strike_steps[0])
            atm = self._atm_strike(float(q.ltp), step)
            for off in offsets:
                strike = int(atm + off * step)
                if strike <= 0:
                    continue
                for typ in self.settings.option_types:
                    typ = typ.upper().strip()
                    sym = self.settings.option_symbol_template.format(UNDERLYING=uname, EXPIRY=expiry, STRIKE=strike, TYPE=typ)
                    candidates.append((sym, f"{uname} {strike} {typ}"))
        candidates = candidates[: self.settings.max_analysis_candidates]
        log.info("Auto ATM candidates prepared: %s candidates, first=%s", len(candidates), candidates[:5])
        return candidates

    async def calculate_level_rows(self, limit: int = 2) -> List[Dict[str, Any]]:
        rows = []
        symbols = self.settings.underlying_symbols if self.settings.auto_atm_enabled else self.settings.scan_symbols
        names = self.settings.underlying_names if self.settings.auto_atm_enabled else self.settings.display_names
        for idx, sym in enumerate(symbols[:limit]):
            name = names[idx] if idx < len(names) else sym
            try:
                df = await asyncio.to_thread(self.broker.history, sym, "D", 15)
                if df.empty or len(df) < 3:
                    df = await asyncio.to_thread(self.broker.history, sym, "5", 5)
                if df.empty or len(df) < 3:
                    continue
                data = df.tail(20)
                recent = data.tail(5)
                resistance = float(recent["high"].max())
                support = float(recent["low"].min())
                avg_range = float((data["high"] - data["low"]).tail(10).mean())
                expected = max(avg_range, abs(resistance - support) / 2)
                rows.append({"name": name, "resistance": round(resistance, 2), "support": round(support, 2), "breakout": round(resistance + expected * 0.10, 2), "breakdown": round(support - expected * 0.10, 2)})
            except Exception as exc:
                log.warning("Level calculation failed for %s: %s", sym, exc)
        return rows

    async def good_morning_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if is_weekday():
            await context.bot.send_message(self.settings.telegram_chat_id, good_morning_message(), parse_mode=ParseMode.HTML)

    async def market_poll_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if is_weekday():
            await context.bot.send_poll(chat_id=self.settings.telegram_chat_id, question="📊 Aaj market ka mood kya lag raha hai?", options=["🚀 Bullish Breakout", "🔻 Bearish Breakdown", "⚖️ Sideways Trap", "🦅 Jiga Bhai ka signal wait"], is_anonymous=False)

    async def ready_alert_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if is_weekday():
            await context.bot.send_message(self.settings.telegram_chat_id, ready_alert_message(), parse_mode=ParseMode.HTML)

    async def opening_plan_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if is_weekday():
            rows = await self.calculate_level_rows(2)
            await context.bot.send_message(self.settings.telegram_chat_id, opening_plan_message(rows, self.settings.vip_link), parse_mode=ParseMode.HTML)

    async def next_day_plan_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if datetime.now(IST).weekday() == 5:
            return
        rows = await self.calculate_level_rows(2)
        await context.bot.send_message(self.settings.telegram_chat_id, next_day_plan_message(rows, self.settings.vip_link), parse_mode=ParseMode.HTML)

    async def scan_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not in_market_window() and self.settings.broker != "mock":
            return
        if self.store.active(self.trades):
            return
        today = datetime.now(IST).strftime("%Y-%m-%d")
        if len([t for t in self.trades.values() if t.opened_at[:10] == today]) >= self.settings.max_calls_per_day:
            return
        try:
            candidates = await self.build_scan_candidates()
        except Exception as exc:
            log.error("Scan candidate build failed: %s", exc)
            return
        analyzed = 0
        valid: List[Tuple[Signal, pd.DataFrame]] = []
        for sym, display in candidates:
            try:
                df = await asyncio.to_thread(self.broker.history, sym, "5", 5)
                analyzed += 1
                sig = self.strategy.scan_one(sym, display, df)
                if sig:
                    valid.append((sig, df))
            except Exception as exc:
                analyzed += 1
                log.warning("Candidate analysis failed for %s: %s", sym, exc)
        if analyzed < self.settings.min_setup_candidates:
            log.warning("Only %s setups analyzed, minimum required is %s. No trade posted.", analyzed, self.settings.min_setup_candidates)
            return
        if not valid:
            log.info("Analyzed %s setups. No valid high-RRR setup found.", analyzed)
            return
        valid.sort(key=lambda x: x[0].score, reverse=True)
        for sig, df in valid:
            ok, review = await asyncio.to_thread(self.ai_filter.review, sig, df)
            if not ok:
                log.info("Internal filter rejected %s score=%s: %s", sig.symbol, sig.score, review)
                continue
            await self.post_signal(context.bot, sig)
            self.strategy.mark_posted(sig)
            break

    async def post_signal(self, bot: Bot, sig: Signal) -> None:
        msg = await bot.send_message(chat_id=self.settings.telegram_chat_id, text=live_call(sig), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        trade = Trade(str(uuid.uuid4()), sig.symbol, sig.display_name, sig.direction, sig.entry, sig.stop_loss, sig.targets, sig.risk_points, sig.reason, msg.message_id, highest_price=sig.entry, last_price=sig.entry, last_update_price=sig.entry)
        self.trades[trade.id] = trade
        self.store.save(self.trades)
        log.info("Posted signal trade_id=%s message_id=%s", trade.id, msg.message_id)

    async def trailing_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        active = self.store.active(self.trades)
        if not active:
            return
        try:
            quotes = await asyncio.to_thread(self.broker.quotes, [t.symbol for t in active])
        except Exception as exc:
            log.exception("Quote fetch failed: %s", exc)
            return
        for t in active:
            q = quotes.get(t.symbol)
            if not q:
                continue
            ltp = round(float(q.ltp), 2)
            t.last_price = ltp
            t.highest_price = max(t.highest_price, ltp)
            points = round(ltp - t.entry, 2)
            if points >= self.settings.point_update_step:
                milestone = round(t.entry + math.floor(points / self.settings.point_update_step) * self.settings.point_update_step, 2)
                if milestone > t.last_update_price:
                    await context.bot.send_message(chat_id=self.settings.telegram_chat_id, text=point_update_message(t, ltp, points), parse_mode=ParseMode.HTML, reply_parameters=ReplyParameters(message_id=t.telegram_message_id))
                    t.last_update_price = milestone
            for i, target in enumerate(t.targets, 1):
                if ltp >= target and i not in t.hit_targets:
                    t.hit_targets.append(i)
                    await self._send_target_hit(context, t, i, ltp)
            close_reason = None
            if ltp <= t.stop_loss:
                close_reason = "Strict SL triggered."
            elif ltp >= t.targets[self.settings.close_on_target - 1]:
                close_reason = f"Target {self.settings.close_on_target} hit successfully."
            if close_reason:
                t.status = "CLOSED"
                t.closed_at = datetime.now(IST).isoformat()
                await context.bot.send_message(chat_id=self.settings.telegram_chat_id, text=closed_message(t, ltp, close_reason), parse_mode=ParseMode.HTML, reply_parameters=ReplyParameters(message_id=t.telegram_message_id))
                context.job_queue.run_once(self.vip_job, when=self.settings.vip_promo_delay_minutes * 60, data={"trade_id": t.id}, name=f"vip-{t.id}")
        self.store.save(self.trades)

    async def _send_target_hit(self, context: ContextTypes.DEFAULT_TYPE, trade: Trade, target_no: int, ltp: float) -> None:
        if target_no in trade.chart_sent_targets:
            return
        if self.settings.generate_charts:
            try:
                df = await asyncio.to_thread(self.broker.history, trade.symbol, "5", 5)
                chart = await asyncio.to_thread(save_chart, df, trade, f"Target {target_no} Hit")
                if chart and Path(chart).exists():
                    await context.bot.send_photo(chat_id=self.settings.telegram_chat_id, photo=InputFile(chart), caption=target_hit_caption(trade, target_no, ltp), parse_mode=ParseMode.HTML, reply_parameters=ReplyParameters(message_id=trade.telegram_message_id))
                else:
                    await context.bot.send_message(chat_id=self.settings.telegram_chat_id, text=target_hit_caption(trade, target_no, ltp), parse_mode=ParseMode.HTML, reply_parameters=ReplyParameters(message_id=trade.telegram_message_id))
            except Exception as exc:
                log.warning("Target chart failed: %s", exc)
                await context.bot.send_message(chat_id=self.settings.telegram_chat_id, text=target_hit_caption(trade, target_no, ltp), parse_mode=ParseMode.HTML, reply_parameters=ReplyParameters(message_id=trade.telegram_message_id))
        else:
            await context.bot.send_message(chat_id=self.settings.telegram_chat_id, text=target_hit_caption(trade, target_no, ltp), parse_mode=ParseMode.HTML, reply_parameters=ReplyParameters(message_id=trade.telegram_message_id))
        trade.chart_sent_targets.append(target_no)

    async def vip_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        trade_id = context.job.data.get("trade_id") if context.job and context.job.data else None
        trade = self.trades.get(trade_id) if trade_id else None
        if trade and trade.promo_sent:
            return
        await context.bot.send_message(chat_id=self.settings.telegram_chat_id, text=vip_promo(self.settings.vip_link), parse_mode=ParseMode.HTML)
        if trade:
            trade.promo_sent = True
            self.store.save(self.trades)


async def post_init(app: Application) -> None:
    m: TradeManager = app.bot_data["manager"]
    app.job_queue.run_daily(m.good_morning_job, time=time(8, 0), days=(1, 2, 3, 4, 5), name="good-morning")
    app.job_queue.run_daily(m.market_poll_job, time=time(8, 10), days=(1, 2, 3, 4, 5), name="market-poll")
    app.job_queue.run_daily(m.ready_alert_job, time=time(9, 0), days=(1, 2, 3, 4, 5), name="ready-alert")
    app.job_queue.run_daily(m.opening_plan_job, time=time(9, 16), days=(1, 2, 3, 4, 5), name="first-sniper-plan")
    app.job_queue.run_daily(m.next_day_plan_job, time=time(22, 0), days=(0, 1, 2, 3, 4, 5), name="next-day-plan")
    app.job_queue.run_repeating(m.scan_job, interval=m.settings.scan_interval_seconds, first=5, name="scanner")
    app.job_queue.run_repeating(m.trailing_job, interval=m.settings.trail_interval_seconds, first=10, name="trailing")
    log.info("Jobs scheduled: daily 08:00/08:10/09:00/09:16/22:00 IST, scan=%ss trail=%ss", m.settings.scan_interval_seconds, m.settings.trail_interval_seconds)


async def run_service() -> None:
    settings = Settings()
    settings.validate()
    broker = make_broker(settings)
    manager = TradeManager(settings, broker)
    app = ApplicationBuilder().token(settings.telegram_bot_token).defaults(Defaults(tzinfo=IST)).build()
    app.bot_data["manager"] = manager
    await app.initialize()
    await post_init(app)
    await app.start()
    log.info("Starting Jiga Bhai Gujarati Trader automated trade manager bot")
    log.info("Bot is running in outbound-only scheduler mode. Polling/getUpdates is disabled.")
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await app.stop()
        await app.shutdown()


def main() -> None:
    asyncio.run(run_service())


if __name__ == "__main__":
    main()
