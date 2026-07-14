from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, time
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import pytz
import requests
from dotenv import load_dotenv
from telegram import Bot, InputFile, ReplyParameters
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationBuilder, ContextTypes

load_dotenv()

IST = pytz.timezone("Asia/Kolkata")
STATE_FILE = Path("state.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("tradebot")


# =========================
# ENV HELPERS
# =========================

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


# =========================
# CONFIG
# =========================

@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = env("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = env("TELEGRAM_CHAT_ID", "")

    broker: str = env("BROKER", "mock").lower()
    fyers_client_id: str = env("FYERS_CLIENT_ID", "")
    fyers_access_token: str = env("FYERS_ACCESS_TOKEN", "")

    scan_symbols: List[str] = field(default_factory=lambda: env_csv(
        "SCAN_SYMBOLS",
        "NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX"
    ))
    display_names: List[str] = field(default_factory=lambda: env_csv(
        "DISPLAY_NAMES",
        "NIFTY 50 OPTIONS,BANKNIFTY OPTIONS"
    ))

    # Auto ATM option mode. If enabled, bot calculates ATM strike from underlying quote
    # and builds CE/PE symbols with OPTION_SYMBOL_TEMPLATE.
    auto_atm_enabled: bool = env_bool("AUTO_ATM_ENABLED", False)
    underlying_symbols: List[str] = field(default_factory=lambda: env_csv(
        "UNDERLYING_SYMBOLS",
        "NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX"
    ))
    underlying_names: List[str] = field(default_factory=lambda: env_csv(
        "UNDERLYING_NAMES",
        "NIFTY,BANKNIFTY"
    ))
    option_expiries: List[str] = field(default_factory=lambda: env_csv(
        "OPTION_EXPIRIES",
        ""
    ))
    option_strike_steps: List[str] = field(default_factory=lambda: env_csv(
        "OPTION_STRIKE_STEPS",
        "50,100"
    ))
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
            missing.append("TELEGRAM_BOT_TOKEN")
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
            raise RuntimeError("AUTO_ATM_ENABLED=true requires OPTION_EXPIRIES. Example: 24JUL or 25724 as per Fyers symbol format.")


# =========================
# MODELS
# =========================

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


# =========================
# STATE
# =========================

class StateStore:
    def load(self) -> Dict[str, Trade]:
        if not STATE_FILE.exists():
            return {}
        try:
            data = json.loads(STATE_FILE.read_text())
            trades: Dict[str, Trade] = {}
            for tid, trade_data in data.get("trades", {}).items():
                # Backward-compatible defaults if old state.json exists.
                trade_data.setdefault("last_update_price", trade_data.get("entry", 0.0))
                trade_data.setdefault("chart_sent_targets", [])
                trades[tid] = Trade(**trade_data)
            return trades
        except Exception as exc:
            log.warning("Could not load state.json: %s", exc)
            return {}

    def save(self, trades: Dict[str, Trade]) -> None:
        payload = {"trades": {tid: asdict(trade) for tid, trade in trades.items()}}
        STATE_FILE.write_text(json.dumps(payload, indent=2))

    def active(self, trades: Dict[str, Trade]) -> List[Trade]:
        return [trade for trade in trades.values() if trade.status == "ACTIVE"]


# =========================
# BROKERS
# =========================

class Broker:
    def quotes(self, symbols: List[str]) -> Dict[str, Quote]:
        raise NotImplementedError

    def history(self, symbol: str, resolution: str = "5", days: int = 5) -> pd.DataFrame:
        raise NotImplementedError


class FyersBroker(Broker):
    def __init__(self, settings: Settings) -> None:
        from fyers_apiv3 import fyersModel
        self.fyers = fyersModel.FyersModel(
            client_id=settings.fyers_client_id,
            token=settings.fyers_access_token,
            is_async=False,
            log_path="",
        )

    def quotes(self, symbols: List[str]) -> Dict[str, Quote]:
        response = self.fyers.quotes(data={"symbols": ",".join(symbols)})
        if response.get("s") != "ok":
            raise RuntimeError(f"Fyers quote error: {response}")
        now = datetime.now(IST)
        result: Dict[str, Quote] = {}
        for row in response.get("d", []):
            symbol = row.get("n")
            values = row.get("v", {})
            ltp = values.get("lp") or values.get("ltp")
            if symbol and ltp is not None:
                result[symbol] = Quote(symbol, float(ltp), now)
        return result

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
            raise RuntimeError(f"Fyers history error for {symbol}: {response}")
        candles = response.get("candles", [])
        df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
        if df.empty:
            return df
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert(IST)
        return df[["datetime", "open", "high", "low", "close", "volume"]]


class MockBroker(Broker):
    def __init__(self) -> None:
        self.prices: Dict[str, float] = {}

    def quotes(self, symbols: List[str]) -> Dict[str, Quote]:
        now = datetime.now(IST)
        result: Dict[str, Quote] = {}
        for symbol in symbols:
            if symbol not in self.prices:
                self.prices[symbol] = 220 + random.random() * 80
            move = random.uniform(-2.5, 7.5)
            self.prices[symbol] = max(10, self.prices[symbol] + move)
            result[symbol] = Quote(symbol, round(self.prices[symbol], 2), now)
        return result

    def history(self, symbol: str, resolution: str = "5", days: int = 5) -> pd.DataFrame:
        now = datetime.now(IST).replace(second=0, microsecond=0)
        periods = max(100, days * 75)
        price = self.prices.get(symbol, 220 + random.random() * 80)
        rows = []
        for i in range(periods):
            dt = now - timedelta(minutes=5 * (periods - i))
            breakout_boost = 8 if i == periods - 1 else 0
            move = math.sin(i / 8) * 1.2 + random.uniform(-2.0, 3.0) + breakout_boost
            open_price = price
            close = max(5, open_price + move)
            high = max(open_price, close) + random.uniform(0.5, 2.5)
            low = min(open_price, close) - random.uniform(0.5, 2.5)
            volume = random.randint(15000, 90000)
            if i == periods - 1:
                volume = int(volume * 2.5)
            rows.append([dt, open_price, high, low, close, volume])
            price = close
        self.prices[symbol] = rows[-1][4]
        return pd.DataFrame(rows, columns=["datetime", "open", "high", "low", "close", "volume"])


def make_broker(settings: Settings) -> Broker:
    if settings.broker == "fyers":
        return FyersBroker(settings)
    return MockBroker()


# =========================
# TIMING + STRATEGY
# =========================

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
    current = now.time()
    morning = time(9, 15) <= current <= time(11, 30)
    afternoon = time(13, 30) <= current <= time(15, 15)
    return morning or afternoon


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()
    typical = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap"] = (typical * df["volume"]).cumsum() / df["volume"].replace(0, np.nan).cumsum()
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    return df


class Strategy:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.limit = DailyLimit()

    def scan_one(self, symbol: str, display_name: str, df: pd.DataFrame) -> Optional[Signal]:
        self.limit.reset_if_new_day()
        if self.limit.calls >= self.settings.max_calls_per_day:
            return None
        if symbol in self.limit.symbols:
            return None
        if len(df) < 50:
            return None
        df = add_indicators(df).dropna().reset_index(drop=True)
        if len(df) < 30:
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2]
        lookback = df.iloc[-25:-1]
        resistance = float(lookback["high"].max())
        swing_low = float(df.iloc[-10:]["low"].min())
        entry = float(last["close"])
        atr = float(last["atr14"])

        breakout = entry > resistance and float(prev["close"]) <= resistance
        trend_ok = entry > float(last["ema9"]) > float(last["ema21"]) and entry > float(last["vwap"])
        volume_ratio = float(last["volume"]) / max(1.0, float(last["vol_ma20"]))
        volume_ok = volume_ratio >= 1.20
        candle_range = max(0.01, float(last["high"]) - float(last["low"]))
        body = float(last["close"]) - float(last["open"])
        body_ratio = body / candle_range
        candle_ok = body_ratio > 0.40

        if not (breakout and trend_ok and volume_ok and candle_ok):
            return None

        stop_loss = min(entry - atr * 0.75, swing_low)
        risk = round(entry - stop_loss, 2)
        if risk <= 0 or risk > self.settings.max_risk_points:
            return None

        targets = [round(entry + risk * r, 2) for r in (3, 4, 5)]
        min_rrr = round((targets[0] - entry) / risk, 2)
        if min_rrr < self.settings.min_rrr:
            return None

        breakout_strength = max(0.0, (entry - resistance) / risk)
        trend_gap = max(0.0, (float(last["ema9"]) - float(last["ema21"])) / risk)
        score = round(
            (volume_ratio * 25)
            + (body_ratio * 25)
            + (breakout_strength * 30)
            + (trend_gap * 20)
            + (min_rrr * 5),
            2,
        )

        return Signal(
            symbol=symbol,
            display_name=display_name,
            direction="BUY",
            entry=round(entry, 2),
            stop_loss=round(stop_loss, 2),
            targets=targets,
            risk_points=risk,
            reason="Momentum breakout above resistance with EMA, VWAP and volume confirmation",
            rrr=min_rrr,
            score=score,
        )

    def mark_posted(self, signal: Signal) -> None:
        self.limit.reset_if_new_day()
        self.limit.calls += 1
        self.limit.symbols.add(signal.symbol)


# =========================
# AI FILTER
# =========================

class AITradeFilter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def review(self, signal: Signal, df: pd.DataFrame) -> Tuple[bool, Dict[str, Any]]:
        if not self.settings.ai_filter_enabled:
            return True, {"decision": "SKIPPED", "reason": "AI filter disabled"}
        if not self.settings.ai_api_key:
            if self.settings.ai_fail_closed:
                return False, {"decision": "REJECT", "reason": "AI enabled but AI_API_KEY missing"}
            return True, {"decision": "APPROVE", "reason": "AI key missing but fail-open enabled"}
        try:
            setup = self._build_setup(signal, df)
            result = self._call_ai(setup)
            decision = str(result.get("decision", "REJECT")).upper()
            confidence = int(result.get("confidence", 0))
            approved = decision == "APPROVE" and confidence >= self.settings.ai_min_confidence
            return approved, result
        except Exception as exc:
            log.exception("AI review failed: %s", exc)
            if self.settings.ai_fail_closed:
                return False, {"decision": "REJECT", "reason": f"AI error: {exc}"}
            return True, {"decision": "APPROVE", "reason": f"AI error ignored: {exc}"}

    def _build_setup(self, signal: Signal, df: pd.DataFrame) -> Dict[str, Any]:
        candles = []
        for _, row in df.tail(15).iterrows():
            candles.append({
                "time": str(row.get("datetime", "")),
                "open": round(float(row["open"]), 2),
                "high": round(float(row["high"]), 2),
                "low": round(float(row["low"]), 2),
                "close": round(float(row["close"]), 2),
                "volume": int(row.get("volume", 0)),
            })
        return {
            "instrument": signal.display_name,
            "symbol": signal.symbol,
            "direction": signal.direction,
            "entry": signal.entry,
            "stop_loss": signal.stop_loss,
            "targets": signal.targets,
            "risk_points": signal.risk_points,
            "minimum_rrr": signal.rrr,
            "strategy_reason": signal.reason,
            "recent_candles": candles,
        }

    def _call_ai(self, setup: Dict[str, Any]) -> Dict[str, Any]:
        url = self.settings.ai_api_base_url.rstrip("/") + "/chat/completions"
        system_prompt = (
            "You are a strict Indian intraday trading risk filter. Use AI as a smart mind only. "
            "Do not create trades. Approve only clean high-momentum 1:3+ RRR setups. "
            "Reject choppy, late, overextended, low volume, unclear setups. "
            "Never promise profit. Return JSON only."
        )
        user_prompt = (
            "Review this setup and return JSON only with keys: "
            "decision APPROVE or REJECT, confidence 0-100, reason, risk_notes, suggested_action.\n\n"
            f"SETUP:\n{json.dumps(setup, ensure_ascii=False)}"
        )
        headers = {
            "Authorization": f"Bearer {self.settings.ai_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://railway.app",
            "X-Title": "Jiga Bhai Gujarati Trader Bot",
        }
        body = {
            "model": self.settings.ai_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        response = requests.post(url, headers=headers, json=body, timeout=self.settings.ai_timeout_seconds)
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)


# =========================
# MESSAGES
# =========================

def fmt(x: float) -> str:
    if float(x).is_integer():
        return str(int(x))
    return f"{x:.2f}"


def brand_signature() -> str:
    # Branding signature. Do not use this to misrepresent automation; it keeps posts channel-branded.
    return f"\n\n<b>— {escape(env('POST_AUTHOR_NAME', 'JIGA BHAI TRADER'))}</b>"


def good_morning_message() -> str:
    return (
        "🌞 <b>GOOD MORNING TRADERS</b> 🌞\n\n"
        "🦅 <b>JIGA BHAI DESK LIVE</b> 🦅\n\n"
        "Aaj sirf premium setup. No random entry.\n"
        "🎯 Focus: Breakout + Volume + 1:3 RRR\n"
        "⚔️ Rule: Patience first, execution fast.\n\n"
        "🔔 Channel active rakho. Move miss hua to entry miss."
        + brand_signature()
    )


def ready_alert_message() -> str:
    return (
        "⚡️ <b>READY ALERT</b> ⚡️\n\n"
        "9:15 ke baad scanner active.\n"
        "📊 Only clean setup.\n"
        "🎯 Target aggressive.\n"
        "🛡 SL strict.\n\n"
        "Phone side me mat rakhna. Sniper move kabhi bhi aa sakta hai 🚀"
        + brand_signature()
    )


def next_trading_day_label() -> str:
    now = datetime.now(IST)
    wd = now.weekday()
    if wd == 4:
        nxt = now + timedelta(days=3)
    elif wd == 5:
        nxt = now + timedelta(days=2)
    else:
        nxt = now + timedelta(days=1)
    return nxt.strftime("%d %b")


def next_day_plan_message(level_rows: List[Dict[str, Any]], vip_link: str) -> str:
    rows_text = []
    for row in level_rows:
        rows_text.append(
            f"🚩 <b>{escape(row['name'])}</b>\n"
            f"R 🔸 <b>{fmt(row['resistance'])}</b> | S 🔸 <b>{fmt(row['support'])}</b>\n"
            f"BO 🔸 <b>{fmt(row['breakout'])}+</b> | BD 🔸 <b>{fmt(row['breakdown'])}-</b>"
        )

    levels = "\n\n".join(rows_text) if rows_text else "Fresh levels live market me update honge."

    return (
        f"🌙 <b>NEXT DAY PLAN - {escape(next_trading_day_label())}</b> 🌙\n\n"
        f"{levels}\n\n"
        "🎯 <b>PLAN:</b> First 15 min wait. Breakout + volume confirm, then entry.\n"
        "🛡 No 1:1 trap. Only high RRR.\n\n"
        "🔥 Active raho. Free channel limited hai. VIP me live hand-holding + priority levels.\n\n"
        f"👑 <b>VIP:</b> {escape(vip_link)}"
        + brand_signature()
    )


def _trade_header(name: str) -> str:
    return f"🚩 <b>{escape(name)} - {datetime.now(IST).strftime('%d %b').upper()}</b>"


def live_call(signal: Signal) -> str:
    t = signal.targets
    return (
        f"{_trade_header(signal.display_name)}\n\n"
        f"PLAN ABOVE 🔸 <b>{fmt(signal.entry)}</b>\n\n"
        f"Target 🔸 <b>{fmt(t[0])}</b> 🚩<b>{fmt(t[1])}</b> 🚩<b>{fmt(t[2])}</b>\n\n"
        f"SL 🔸 <b>{fmt(signal.stop_loss)}</b>  PREMIUM 📊✅✅📊\n\n"
        f"Inquiry for VIP :- {escape(env('VIP_LINK', 'https://jigabhaivip.com'))}"
        + brand_signature()
    )


def point_update_message(trade: Trade, ltp: float, points: float) -> str:
    return (
        f"🐂 <b>{escape(trade.display_name)} - {datetime.now(IST).strftime('%d %b').upper()}</b> 🐂\n\n"
        f"↗️ Range..  <b>{fmt(trade.entry)} TO {fmt(ltp)}</b> 🚀🚀\n"
        f"✅ Running: <b>+{fmt(points)} pts</b>"
        + brand_signature()
    )


def target_hit_caption(trade: Trade, target_no: int, ltp: float) -> str:
    return (
        f"🎯 <b>TARGET {target_no} HIT</b> 🎯\n\n"
        f"🐂 <b>{escape(trade.display_name)}</b>\n"
        f"Range.. <b>{fmt(trade.entry)} TO {fmt(ltp)}</b> 🚀\n\n"
        "💰 Book profit. Trail remaining."
        + brand_signature()
    )


def closed_message(trade: Trade, ltp: float, reason: str) -> str:
    return (
        f"✅ <b>TRADE CLOSED</b> ✅\n\n"
        f"🚩 <b>{escape(trade.display_name)}</b>\n"
        f"Entry 🔸 <b>{fmt(trade.entry)}</b>\n"
        f"Exit 🔸 <b>{fmt(ltp)}</b>\n"
        f"Result 🔸 <b>{escape(reason)}</b>\n\n"
        "Next sniper setup ka wait."
        + brand_signature()
    )


def vip_promo(vip_link: str) -> str:
    return (
        "🔥 <b>BIGGEST DISCOUNT IN VIP CHANNEL</b> 🔥🔥\n\n"
        "🎁 <b>Rs 8000/-</b> 01 Month - Basic Strike\n"
        "🎁 <b>Rs 15000/-</b> 01 Month - Pro Hunter\n"
        "🎁 <b>Rs 28000/-</b> 01 Month - Elite Sniper\n"
        "🎁 <b>Rs 50000/-</b> 01 Month - Inner Circle\n\n"
        f"More details For VIP ----> {escape(vip_link)}\n\n"
        "✅ Banknifty Calls | Nifty Calls | Stock Calls\n"
        "✅ Minimum Capital: 10k-50k\n"
        "✅ Daily 4-5 Prime Setups\n"
        "✅ Target/SL + Live Guidance\n"
        "✅ Full Support + Hand-Holding\n\n"
        "⚠️ Profit guaranteed nahi hota. Discipline + risk management zaroori hai.\n\n"
        "👇👇👇\n"
        "DM NOW 👉 ON WHATSAPP NOW 👈"
        + brand_signature()
    )


# =========================
# WHITE CHART ON TARGET HIT
# =========================

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

        market_colors = mpf.make_marketcolors(
            up="#16a34a",
            down="#dc2626",
            edge="inherit",
            wick="inherit",
            volume="in",
        )
        style = mpf.make_mpf_style(
            base_mpf_style="yahoo",
            marketcolors=market_colors,
            facecolor="#ffffff",
            figcolor="#ffffff",
            gridcolor="#e5e7eb",
            gridstyle="--",
        )
        hlines = dict(
            hlines=[signal_or_trade.entry, signal_or_trade.stop_loss, *signal_or_trade.targets],
            colors=["#ca8a04", "#dc2626", "#16a34a", "#0d9488", "#7c3aed"],
            linestyle=["-", "--", "--", "--", "--"],
            linewidths=[1.4, 1.2, 1.2, 1.2, 1.2],
        )
        safe_symbol = signal_or_trade.symbol.replace(":", "_").replace("/", "_")
        path = f"charts/{safe_symbol}_{datetime.now(IST).strftime('%H%M%S')}.png"
        fig, _ = mpf.plot(
            chart_df,
            type="candle",
            style=style,
            volume=True,
            hlines=hlines,
            title=f"\n{signal_or_trade.display_name} | {title_suffix} | Book Profits",
            figsize=(16, 9),
            returnfig=True,
            tight_layout=True,
        )
        fig.savefig(path, dpi=200, bbox_inches="tight", facecolor="#ffffff")
        plt.close(fig)
        return path
    except Exception as exc:
        log.warning("Chart generation failed: %s", exc)
        return None


# =========================
# TELEGRAM TRADE MANAGER
# =========================

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
        """
        Returns [(tradable_symbol, display_name)].
        Manual mode uses SCAN_SYMBOLS. Auto ATM mode calculates ATM CE/PE from underlying LTP.
        OPTION_SYMBOL_TEMPLATE placeholders:
        {UNDERLYING}, {EXPIRY}, {STRIKE}, {TYPE}
        Example output depends on your Fyers symbol format.
        """
        if not self.settings.auto_atm_enabled:
            return [
                (
                    symbol,
                    self.settings.display_names[idx] if idx < len(self.settings.display_names) else symbol,
                )
                for idx, symbol in enumerate(self.settings.scan_symbols)
            ]

        quotes = await asyncio.to_thread(self.broker.quotes, self.settings.underlying_symbols)
        candidates: List[Tuple[str, str]] = []

        offsets = [0]
        for i in range(1, self.settings.atm_strike_range + 1):
            offsets.extend([-i, i])

        for idx, underlying_symbol in enumerate(self.settings.underlying_symbols):
            quote = quotes.get(underlying_symbol)
            if not quote:
                log.warning("No underlying quote received for %s", underlying_symbol)
                continue

            underlying_name = (
                self.settings.underlying_names[idx]
                if idx < len(self.settings.underlying_names)
                else underlying_symbol.replace("NSE:", "").replace("-INDEX", "")
            )
            expiry = self.settings.option_expiries[idx] if idx < len(self.settings.option_expiries) else self.settings.option_expiries[0]
            step_raw = self.settings.option_strike_steps[idx] if idx < len(self.settings.option_strike_steps) else self.settings.option_strike_steps[0]
            step = float(step_raw)
            atm_strike = self._atm_strike(float(quote.ltp), step)

            for offset in offsets:
                strike = int(atm_strike + offset * step)
                if strike <= 0:
                    continue
                for opt_type in self.settings.option_types:
                    opt_type = opt_type.upper().strip()
                    symbol = self.settings.option_symbol_template.format(
                        UNDERLYING=underlying_name,
                        EXPIRY=expiry,
                        STRIKE=strike,
                        TYPE=opt_type,
                    )
                    display = f"{underlying_name} {strike} {opt_type}"
                    candidates.append((symbol, display))

        candidates = candidates[: self.settings.max_analysis_candidates]
        log.info("Auto ATM candidates prepared: %s candidates, first=%s", len(candidates), candidates[:5])
        return candidates

    async def next_day_plan_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Post after-market advance plan at 10 PM IST. Skip Saturday night.
        now = datetime.now(IST)
        if now.weekday() == 5:
            return

        level_rows: List[Dict[str, Any]] = []
        symbols = self.settings.underlying_symbols if self.settings.auto_atm_enabled else self.settings.scan_symbols
        names = self.settings.underlying_names if self.settings.auto_atm_enabled else self.settings.display_names

        for idx, symbol in enumerate(symbols[:2]):
            name = names[idx] if idx < len(names) else symbol.replace("NSE:", "").replace("-INDEX", "")
            try:
                df = await asyncio.to_thread(self.broker.history, symbol, "D", 15)
                if df.empty or len(df) < 3:
                    df = await asyncio.to_thread(self.broker.history, symbol, "5", 5)
                if df.empty or len(df) < 3:
                    continue

                data = df.tail(20).copy()
                recent = data.tail(5)
                last = data.iloc[-1]
                resistance = float(recent["high"].max())
                support = float(recent["low"].min())
                close = float(last["close"])
                avg_range = float((data["high"] - data["low"]).tail(10).mean())
                expected_move = max(avg_range, abs(resistance - support) / 2)

                level_rows.append({
                    "name": name,
                    "resistance": round(resistance, 2),
                    "support": round(support, 2),
                    "breakout": round(resistance + expected_move * 0.10, 2),
                    "breakdown": round(support - expected_move * 0.10, 2),
                    "expected_move": round(expected_move, 2),
                    "close": round(close, 2),
                })
            except Exception as exc:
                log.warning("Next day level calculation failed for %s: %s", symbol, exc)

        await context.bot.send_message(
            chat_id=self.settings.telegram_chat_id,
            text=next_day_plan_message(level_rows, self.settings.vip_link),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )

    async def good_morning_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_weekday():
            return
        await context.bot.send_message(
            chat_id=self.settings.telegram_chat_id,
            text=good_morning_message(),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    async def market_poll_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_weekday():
            return
        await context.bot.send_poll(
            chat_id=self.settings.telegram_chat_id,
            question="📊 Aaj market ka mood kya lag raha hai?",
            options=["🚀 Bullish Breakout", "🔻 Bearish Breakdown", "⚖️ Sideways Trap", "🦅 Jiga Bhai ka signal wait"],
            is_anonymous=False,
        )

    async def ready_alert_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_weekday():
            return
        await context.bot.send_message(
            chat_id=self.settings.telegram_chat_id,
            text=ready_alert_message(),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    async def scan_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Real market scanning only in windows. Mock mode allowed outside window for testing.
        if not in_market_window() and self.settings.broker != "mock":
            return
        if self.store.active(self.trades):
            return

        today = datetime.now(IST).strftime("%Y-%m-%d")
        todays_trades = [t for t in self.trades.values() if t.opened_at[:10] == today]
        if len(todays_trades) >= self.settings.max_calls_per_day:
            return

        candidates = await self.build_scan_candidates()
        analyzed_count = 0
        valid_setups: List[Tuple[Signal, pd.DataFrame]] = []

        # First analyze many candidates, then choose the best scored setup.
        # This prevents posting the first random-looking setup.
        for symbol, display_name in candidates:
            try:
                df = await asyncio.to_thread(self.broker.history, symbol, "5", 5)
                analyzed_count += 1
                signal = self.strategy.scan_one(symbol, display_name, df)
                if signal:
                    valid_setups.append((signal, df))
            except Exception as exc:
                analyzed_count += 1
                log.warning("Candidate analysis failed for %s: %s", symbol, exc)

        if analyzed_count < self.settings.min_setup_candidates:
            log.warning(
                "Only %s setups analyzed, minimum required is %s. No trade posted.",
                analyzed_count,
                self.settings.min_setup_candidates,
            )
            return

        if not valid_setups:
            log.info("Analyzed %s setups. No valid high-RRR setup found.", analyzed_count)
            return

        valid_setups.sort(key=lambda item: item[0].score, reverse=True)
        log.info(
            "Analyzed %s setups, valid=%s, top=%s score=%s",
            analyzed_count,
            len(valid_setups),
            valid_setups[0][0].display_name,
            valid_setups[0][0].score,
        )

        # AI reviews best scored setups one by one. First approved setup is posted.
        for signal, df in valid_setups:
            try:
                approved, ai_review = await asyncio.to_thread(self.ai_filter.review, signal, df)
                if not approved:
                    log.info("AI rejected %s score=%s: %s", signal.symbol, signal.score, ai_review)
                    continue

                log.info("Signal approved %s score=%s: %s", signal.symbol, signal.score, ai_review)
                await self.post_signal(context.bot, signal)
                self.strategy.mark_posted(signal)
                break
            except Exception as exc:
                log.exception("Final setup approval/post failed for %s: %s", signal.symbol, exc)

    async def post_signal(self, bot: Bot, signal: Signal) -> None:
        msg = await bot.send_message(
            chat_id=self.settings.telegram_chat_id,
            text=live_call(signal),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        trade = Trade(
            id=str(uuid.uuid4()),
            symbol=signal.symbol,
            display_name=signal.display_name,
            direction=signal.direction,
            entry=signal.entry,
            stop_loss=signal.stop_loss,
            targets=signal.targets,
            risk_points=signal.risk_points,
            reason=signal.reason,
            telegram_message_id=msg.message_id,
            highest_price=signal.entry,
            last_price=signal.entry,
            last_update_price=signal.entry,
        )
        self.trades[trade.id] = trade
        self.store.save(self.trades)
        log.info("Posted signal trade_id=%s message_id=%s", trade.id, msg.message_id)

    async def trailing_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        active_trades = self.store.active(self.trades)
        if not active_trades:
            return
        try:
            quotes = await asyncio.to_thread(self.broker.quotes, [trade.symbol for trade in active_trades])
        except Exception as exc:
            log.exception("Quote fetch failed: %s", exc)
            return

        for trade in active_trades:
            quote = quotes.get(trade.symbol)
            if not quote:
                continue

            ltp = round(float(quote.ltp), 2)
            trade.last_price = ltp
            trade.highest_price = max(trade.highest_price, ltp)

            # 10-point space live updates as separate messages, not edits.
            profit_points = round(ltp - trade.entry, 2)
            if profit_points >= self.settings.point_update_step:
                milestone_steps = math.floor(profit_points / self.settings.point_update_step)
                milestone_price = round(trade.entry + milestone_steps * self.settings.point_update_step, 2)
                if milestone_price > trade.last_update_price:
                    await context.bot.send_message(
                        chat_id=self.settings.telegram_chat_id,
                        text=point_update_message(trade, ltp, profit_points),
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                        reply_parameters=ReplyParameters(message_id=trade.telegram_message_id),
                    )
                    trade.last_update_price = milestone_price

            # Target hit celebration with white chart.
            newly_hit_targets: List[int] = []
            for i, target in enumerate(trade.targets, start=1):
                if ltp >= target and i not in trade.hit_targets:
                    trade.hit_targets.append(i)
                    newly_hit_targets.append(i)

            for target_no in newly_hit_targets:
                if target_no not in trade.chart_sent_targets:
                    if self.settings.generate_charts:
                        try:
                            df = await asyncio.to_thread(self.broker.history, trade.symbol, "5", 5)
                            chart_path = await asyncio.to_thread(save_chart, df, trade, f"Target {target_no} Hit")
                            if chart_path and Path(chart_path).exists():
                                await context.bot.send_photo(
                                    chat_id=self.settings.telegram_chat_id,
                                    photo=InputFile(chart_path),
                                    caption=target_hit_caption(trade, target_no, ltp),
                                    parse_mode=ParseMode.HTML,
                                    reply_parameters=ReplyParameters(message_id=trade.telegram_message_id),
                                )
                            else:
                                await context.bot.send_message(
                                    chat_id=self.settings.telegram_chat_id,
                                    text=target_hit_caption(trade, target_no, ltp),
                                    parse_mode=ParseMode.HTML,
                                    reply_parameters=ReplyParameters(message_id=trade.telegram_message_id),
                                )
                        except Exception as exc:
                            log.warning("Target chart/message failed: %s", exc)
                            await context.bot.send_message(
                                chat_id=self.settings.telegram_chat_id,
                                text=target_hit_caption(trade, target_no, ltp),
                                parse_mode=ParseMode.HTML,
                                reply_parameters=ReplyParameters(message_id=trade.telegram_message_id),
                            )
                    else:
                        await context.bot.send_message(
                            chat_id=self.settings.telegram_chat_id,
                            text=target_hit_caption(trade, target_no, ltp),
                            parse_mode=ParseMode.HTML,
                            reply_parameters=ReplyParameters(message_id=trade.telegram_message_id),
                        )
                    trade.chart_sent_targets.append(target_no)

            close_reason = None
            if ltp <= trade.stop_loss:
                close_reason = "Strict SL triggered. No emotions, wait for next high-RRR setup."
            elif ltp >= trade.targets[self.settings.close_on_target - 1]:
                close_reason = f"Target {self.settings.close_on_target} hit successfully."

            if close_reason:
                trade.status = "CLOSED"
                trade.closed_at = datetime.now(IST).isoformat()
                await context.bot.send_message(
                    chat_id=self.settings.telegram_chat_id,
                    text=closed_message(trade, ltp, close_reason),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                    reply_parameters=ReplyParameters(message_id=trade.telegram_message_id),
                )
                context.job_queue.run_once(
                    self.vip_job,
                    when=self.settings.vip_promo_delay_minutes * 60,
                    data={"trade_id": trade.id},
                    name=f"vip-{trade.id}",
                )

        self.store.save(self.trades)

    async def vip_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        trade_id = None
        if context.job and context.job.data:
            trade_id = context.job.data.get("trade_id")
        trade = self.trades.get(trade_id) if trade_id else None
        if trade and trade.promo_sent:
            return
        await context.bot.send_message(
            chat_id=self.settings.telegram_chat_id,
            text=vip_promo(self.settings.vip_link),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )
        if trade:
            trade.promo_sent = True
            self.store.save(self.trades)


# =========================
# APP START
# =========================

async def post_init(app: Application) -> None:
    manager: TradeManager = app.bot_data["manager"]

    app.job_queue.run_daily(
        manager.good_morning_job,
        time=time(8, 0, tzinfo=IST),
        days=(0, 1, 2, 3, 4),
        name="good-morning",
    )
    app.job_queue.run_daily(
        manager.market_poll_job,
        time=time(8, 10, tzinfo=IST),
        days=(0, 1, 2, 3, 4),
        name="market-poll",
    )
    app.job_queue.run_daily(
        manager.ready_alert_job,
        time=time(9, 0, tzinfo=IST),
        days=(0, 1, 2, 3, 4),
        name="ready-alert",
    )
    app.job_queue.run_daily(
        manager.next_day_plan_job,
        time=time(22, 0, tzinfo=IST),
        days=(0, 1, 2, 3, 4, 6),
        name="next-day-plan",
    )

    app.job_queue.run_repeating(
        manager.scan_job,
        interval=manager.settings.scan_interval_seconds,
        first=5,
        name="scanner",
    )
    app.job_queue.run_repeating(
        manager.trailing_job,
        interval=manager.settings.trail_interval_seconds,
        first=10,
        name="trailing",
    )

    log.info(
        "Jobs scheduled: daily 08:00/08:10/09:00/22:00 IST, scan=%ss trail=%ss",
        manager.settings.scan_interval_seconds,
        manager.settings.trail_interval_seconds,
    )


async def run_service() -> None:
    """
    Outbound-only scheduler mode.

    Why required:
    This bot does not need incoming Telegram commands. It only sends scheduled
    channel posts, trade updates, charts, and VIP promos. Therefore polling
    getUpdates is unnecessary and can create 409 Conflict errors when another
    Telegram bot instance accidentally uses the same token.
    """
    settings = Settings()
    settings.validate()
    broker = make_broker(settings)
    manager = TradeManager(settings, broker)

    app = ApplicationBuilder().token(settings.telegram_bot_token).build()
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
        log.info("Stopping bot service...")
        await app.stop()
        await app.shutdown()


def main() -> None:
    asyncio.run(run_service())


if __name__ == "__main__":
    main()
