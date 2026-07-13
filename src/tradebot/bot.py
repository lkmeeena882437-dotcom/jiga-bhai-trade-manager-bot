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
from telegram import Bot, InputFile
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

    max_calls_per_day: int = env_int("MAX_CALLS_PER_DAY", 5)
    scan_interval_seconds: int = env_int("SCAN_INTERVAL_SECONDS", 60)
    trail_interval_seconds: int = env_int("TRAIL_INTERVAL_SECONDS", 15)
    max_risk_points: float = env_float("MAX_RISK_POINTS", 80)
    min_rrr: float = env_float("MIN_RRR", 3)
    close_on_target: int = env_int("CLOSE_ON_TARGET", 3)

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
    hit_targets: List[int] = field(default_factory=list)
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
            return {
                tid: Trade(**trade_data)
                for tid, trade_data in data.get("trades", {}).items()
            }
        except Exception as exc:
            log.warning("Could not load state.json: %s", exc)
            return {}

    def save(self, trades: Dict[str, Trade]) -> None:
        payload = {
            "trades": {
                tid: asdict(trade)
                for tid, trade in trades.items()
            }
        }
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
        df = pd.DataFrame(
            candles,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )

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
# MARKET TIMING + STRATEGY
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
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

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
        volume_ok = float(last["volume"]) >= 1.20 * float(last["vol_ma20"])
        candle_range = max(0.01, float(last["high"]) - float(last["low"]))
        body = float(last["close"]) - float(last["open"])
        candle_ok = body > 0.40 * candle_range

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
            candles.append(
                {
                    "time": str(row.get("datetime", "")),
                    "open": round(float(row["open"]), 2),
                    "high": round(float(row["high"]), 2),
                    "low": round(float(row["low"]), 2),
                    "close": round(float(row["close"]), 2),
                    "volume": int(row.get("volume", 0)),
                }
            )

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
            "You are a strict Indian intraday trading risk filter. "
            "Approve only clean high-momentum 1:3+ RRR setups. "
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

        response = requests.post(
            url,
            headers=headers,
            json=body,
            timeout=self.settings.ai_timeout_seconds,
        )
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


def live_call(signal: Signal) -> str:
    t = signal.targets
    return (
        f"🦅 <b>[PREMIUM SETUP] {escape(signal.display_name)}</b> 🦅\n\n"
        f"📊 <b>Instrument:</b> {escape(signal.display_name)}\n"
        f"🎯 <b>Smart Entry:</b> <b>{fmt(signal.entry)}</b>\n"
        f"📈 <b>Momentum Targets:</b> {fmt(t[0])} (1:3) | {fmt(t[1])} (1:4) | {fmt(t[2])}+ (JACKPOT)\n"
        f"🛡 <b>Strict SL:</b> {fmt(signal.stop_loss)} ({fmt(signal.risk_points)} pts Risk - No Emotions)\n\n"
        "🔍 <i>Chart indicates a powerful momentum breakout. High RRR setup activated.</i>"
    )


def trailing_update(trade: Trade, ltp: float, status_line: Optional[str] = None) -> str:
    t = trade.targets

    if status_line is None:
        if ltp >= t[2]:
            status_line = "JACKPOT target zone reached 🎯"
        elif ltp >= t[1]:
            status_line = "Target 2 reached, trail hard 🎯"
        elif ltp >= t[0]:
            status_line = "Target 1 reached 🎯"
        elif ltp <= trade.stop_loss:
            status_line = "Strict SL triggered"
        else:
            status_line = "Perfect Execution 🎯"

    return (
        f"🦅 <b>[PREMIUM SETUP] {escape(trade.display_name)}</b> 🦅\n\n"
        f"📊 <b>Instrument:</b> {escape(trade.display_name)}\n"
        f"🎯 <b>Smart Entry:</b> <b>{fmt(trade.entry)}</b>\n"
        f"📈 <b>Momentum Targets:</b> {fmt(t[0])} (1:3) | {fmt(t[1])} (1:4) | {fmt(t[2])}+ (JACKPOT)\n"
        f"🛡 <b>Strict SL:</b> {fmt(trade.stop_loss)} ({fmt(trade.risk_points)} pts Risk - No Emotions)\n\n"
        f"🚀 <b>LIVE UPDATE: {fmt(trade.entry)} ➡️ {fmt(ltp)} ({escape(status_line)})</b>\n"
        "🔥 <i>Moving aggressively towards Target 1! Safe traders book partial profits NOW and trail SL to cost. Let the trend work for you!</i>"
    )


def closed_update(trade: Trade, ltp: float, reason: str) -> str:
    return trailing_update(trade, ltp, reason) + f"\n\n✅ <b>TRADE CLOSED:</b> {escape(reason)}"


def vip_promo(vip_link: str) -> str:
    return (
        "🛑 <b>KAB TAK DUSRO KE PROFIT SCREENSHOTS DEKHTE RAHOGE?</b> 🛑\n\n"
        "Free channel me hum sirf limited setups dete hain, par asli compounding VIP me chal rahi hai! "
        "Hamara VIP system strictly small capital compounding (the 1,00,000 to 50,00,000 blueprint) par focused hai. "
        "No random trades, only high-accuracy sniper entries. 1:3,4,5,6, unlimited.\n\n"
        "Agar market me sach me paisa banana hai aur account blow nahi karna hai, toh 1:1 wale trades chodo aur high RRR system follow karo.\n\n"
        "👑 <b>WHAT YOU GET IN VIP:</b>\n"
        "⚡️ 3-5 Prime Setups Daily (Minimum 1:3 RRR)\n"
        "⚡️ Exact Entry, Exit &amp; Live Trailing Support\n"
        "⚡️ Full Hand-Holding Mentorship\n\n"
        f"💎 <b>UPGRADE TO VIP NOW:</b> {escape(vip_link)}\n"
        "⏳ <i>WARNING: Only 10 seats left for today's batch. Time is money, act fast!</i>\n\n"
        "⚠️ <i>Market risk applies. Past performance does not guarantee future returns.</i>"
    )


# =========================
# CHART
# =========================

def save_chart(df: pd.DataFrame, signal: Signal) -> Optional[str]:
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
            up="#00ff9c",
            down="#ff4d6d",
            edge="inherit",
            wick="inherit",
            volume="in",
        )

        style = mpf.make_mpf_style(
            base_mpf_style="nightclouds",
            marketcolors=market_colors,
            facecolor="#05070f",
            figcolor="#05070f",
            gridcolor="#1f2a44",
            gridstyle="--",
        )

        hlines = dict(
            hlines=[signal.entry, signal.stop_loss, *signal.targets],
            colors=["#ffd166", "#ff4d6d", "#00ff9c", "#2dd4bf", "#a78bfa"],
            linestyle=["-", "--", "--", "--", "--"],
            linewidths=[1.4, 1.2, 1.2, 1.2, 1.2],
        )

        path = f"charts/{signal.symbol.replace(':', '_').replace('/', '_')}.png"

        fig, _ = mpf.plot(
            chart_df,
            type="candle",
            style=style,
            volume=True,
            hlines=hlines,
            title=f"\n{signal.display_name} | Premium Momentum Breakout | RRR 1:3+",
            figsize=(16, 9),
            returnfig=True,
            tight_layout=True,
        )

        fig.savefig(path, dpi=200, bbox_inches="tight", facecolor="#05070f")
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
        self.edit_locks: Dict[int, asyncio.Lock] = {}

    def lock_for(self, message_id: int) -> asyncio.Lock:
        if message_id not in self.edit_locks:
            self.edit_locks[message_id] = asyncio.Lock()
        return self.edit_locks[message_id]

    async def scan_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not in_market_window() and self.settings.broker != "mock":
            return

        if self.store.active(self.trades):
            return

        today = datetime.now(IST).strftime("%Y-%m-%d")
        todays_trades = [t for t in self.trades.values() if t.opened_at[:10] == today]
        if len(todays_trades) >= self.settings.max_calls_per_day:
            return

        for idx, symbol in enumerate(self.settings.scan_symbols):
            display_name = (
                self.settings.display_names[idx]
                if idx < len(self.settings.display_names)
                else symbol
            )

            try:
                df = await asyncio.to_thread(self.broker.history, symbol, "5", 5)
                signal = self.strategy.scan_one(symbol, display_name, df)

                if not signal:
                    continue

                approved, ai_review = await asyncio.to_thread(self.ai_filter.review, signal, df)
                if not approved:
                    log.info("AI rejected %s: %s", symbol, ai_review)
                    continue

                log.info("Signal approved %s: %s", symbol, ai_review)
                await self.post_signal(context.bot, signal, df)
                self.strategy.mark_posted(signal)
                break

            except Exception as exc:
                log.exception("Scan failed for %s: %s", symbol, exc)

    async def post_signal(self, bot: Bot, signal: Signal, df: pd.DataFrame) -> None:
        msg = await bot.send_message(
            chat_id=self.settings.telegram_chat_id,
            text=live_call(signal),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

        if self.settings.generate_charts:
            chart_path = await asyncio.to_thread(save_chart, df, signal)
            if chart_path and Path(chart_path).exists():
                await bot.send_photo(
                    chat_id=self.settings.telegram_chat_id,
                    photo=InputFile(chart_path),
                    caption="📊 Premium dark-mode breakout chart",
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
        )

        self.trades[trade.id] = trade
        self.store.save(self.trades)
        log.info("Posted signal trade_id=%s message_id=%s", trade.id, msg.message_id)

    async def trailing_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        active_trades = self.store.active(self.trades)
        if not active_trades:
            return

        try:
            quotes = await asyncio.to_thread(
                self.broker.quotes,
                [trade.symbol for trade in active_trades],
            )
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

            for i, target in enumerate(trade.targets, start=1):
                if ltp >= target and i not in trade.hit_targets:
                    trade.hit_targets.append(i)

            close_reason = None

            if ltp <= trade.stop_loss:
                close_reason = "Strict SL triggered. No emotions, wait for next high-RRR setup."
            elif ltp >= trade.targets[self.settings.close_on_target - 1]:
                close_reason = f"Target {self.settings.close_on_target} hit successfully."

            async with self.lock_for(trade.telegram_message_id):
                try:
                    if close_reason:
                        trade.status = "CLOSED"
                        trade.closed_at = datetime.now(IST).isoformat()

                        await context.bot.edit_message_text(
                            chat_id=self.settings.telegram_chat_id,
                            message_id=trade.telegram_message_id,
                            text=closed_update(trade, ltp, close_reason),
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True,
                        )

                        context.job_queue.run_once(
                            self.vip_job,
                            when=self.settings.vip_promo_delay_minutes * 60,
                            data={"trade_id": trade.id},
                            name=f"vip-{trade.id}",
                        )

                    else:
                        await context.bot.edit_message_text(
                            chat_id=self.settings.telegram_chat_id,
                            message_id=trade.telegram_message_id,
                            text=trailing_update(trade, ltp),
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True,
                        )

                except Exception as exc:
                    if "not modified" not in str(exc).lower():
                        log.exception("Telegram edit failed: %s", exc)

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
        "Jobs scheduled: scan=%ss trail=%ss",
        manager.settings.scan_interval_seconds,
        manager.settings.trail_interval_seconds,
    )


def main() -> None:
    settings = Settings()
    settings.validate()

    broker = make_broker(settings)
    manager = TradeManager(settings, broker)

    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .build()
    )

    app.bot_data["manager"] = manager

    log.info("Starting Jiga Bhai Gujarati Trader automated trade manager bot")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
