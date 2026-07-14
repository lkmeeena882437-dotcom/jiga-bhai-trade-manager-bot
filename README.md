# Jiga Bhai Gujarati Trader Bot

Automated Telegram trade manager bot for Indian market signals.

## Features

- 8:00 AM premium good morning post
- 8:10 AM market poll
- 9:00 AM ready alert
- Live Fyers market scan from 9:15 AM
- Auto ATM CE/PE option selection
- Minimum 20 setup analysis before trade
- AI confirmation filter
- One active trade at a time
- Max 5 trades per day
- Minimum 1:3 RRR setup
- Separate live update message every 10 points
- Target hit white chart
- VIP promo after trade close
- Railway deployment ready

## Railway Start Command

PYTHONPATH=src python -m tradebot.bot

## Required Railway Variables

TELEGRAM_BOT_TOKEN=your_real_bot_token
TELEGRAM_CHAT_ID=-100xxxxxxxxxx

BROKER=fyers
FYERS_CLIENT_ID=YOUR_APP_ID-100
FYERS_ACCESS_TOKEN=TODAY_VALID_FYERS_ACCESS_TOKEN

AUTO_ATM_ENABLED=true
UNDERLYING_SYMBOLS=NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX
UNDERLYING_NAMES=NIFTY,BANKNIFTY
OPTION_EXPIRIES=24JUL,24JUL
OPTION_STRIKE_STEPS=50,100
OPTION_TYPES=CE,PE
OPTION_SYMBOL_TEMPLATE=NSE:{UNDERLYING}{EXPIRY}{STRIKE}{TYPE}
ATM_STRIKE_RANGE=5
MIN_SETUP_CANDIDATES=20
MAX_ANALYSIS_CANDIDATES=40

POST_AUTHOR_NAME=JIGA BHAI TRADER

MAX_CALLS_PER_DAY=5
MIN_CALLS_PER_DAY=2
SCAN_INTERVAL_SECONDS=60
TRAIL_INTERVAL_SECONDS=15
MAX_RISK_POINTS=80
MIN_RRR=3
CLOSE_ON_TARGET=3
POINT_UPDATE_STEP=10

VIP_LINK=https://t.me/your_vip_link
VIP_PROMO_DELAY_MINUTES=10

AI_FILTER_ENABLED=true
AI_API_KEY=your_real_ai_api_key
AI_API_BASE_URL=https://openrouter.ai/api/v1
AI_MODEL=openai/gpt-4o-mini
AI_MIN_CONFIDENCE=70
AI_FAIL_CLOSED=true
AI_TIMEOUT_SECONDS=20

GENERATE_CHARTS=true
NIXPACKS_PYTHON_VERSION=3.11

## Daily Schedule

- 08:00 AM: Good morning post
- 08:10 AM: Market poll
- 09:00 AM: Ready alert
- 09:15 AM - 11:30 AM: Morning scanner
- 01:30 PM - 03:15 PM: Afternoon scanner

## Important

If BROKER=mock, bot can send random testing trades.
For real work use only BROKER=fyers.

OPTION_EXPIRIES and OPTION_SYMBOL_TEMPLATE must match Fyers symbol master.

## Risk Note

No trading bot can guarantee profits or fixed win rate. Use proper risk management.
