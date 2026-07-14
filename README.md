# Automated Telegram Trade Manager Bot

Channel: **Jiga Bhai Gujarati Trader**  
Stack: Python 3, `python-telegram-bot`, Fyers free API adapter, mplfinance charts.

> Important: This bot is automation software. No algorithm can guarantee a 70-80% win rate or profits. Forward-test and comply with SEBI/Indian market-advisory rules before posting live trade calls. Market risk applies.

## What is included

- Scans only during IST market windows:
  - 09:15-11:30
  - 13:30-15:15
- 2-5 max public calls/day via `MAX_CALLS_PER_DAY`.
- Momentum breakout + price-action filter:
  - breakout above recent resistance
  - EMA 9 > EMA 21
  - price above VWAP
  - volume expansion
  - strong candle body
- Strict high-RRR construction:
  - Target 1 = 1:3
  - Target 2 = 1:4
  - Target 3 = 1:5
  - 1:1 and 1:2 setups are blocked.
- Live trailing with **Telegram `edit_message_text`** only; no repeated price-update spam.
- VIP promo automatically sent exactly `VIP_PROMO_DELAY_MINUTES` after trade close.
- Dark-mode HD mplfinance chart generation.
- Fyers live-data adapter plus local mock broker for testing.
- Optional AI confirmation filter through an OpenAI-compatible API.
- Render, Railway, and GitHub Actions deployment files.

## Files

```text
telegram_trade_manager_bot/
├── src/tradebot/
│   ├── ai_filter.py    # Optional AI confirmation filter
│   ├── bot.py          # Telegram app, scanner, trailing, VIP scheduler
│   ├── broker.py       # Fyers + mock broker adapters
│   ├── charts.py       # mplfinance dark chart generator
│   ├── config.py       # environment config
│   ├── messages.py     # hardcoded Telegram HTML templates
│   ├── models.py       # Signal/Trade data models
│   ├── state.py        # JSON persistence
│   └── strategy.py     # high-RRR breakout scanner
├── .env.example
├── requirements.txt
├── Procfile
├── railway.json
├── render.yaml
└── .github/workflows/run-bot.yml
```

## Setup locally

```bash
cd telegram_trade_manager_bot
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=your_botfather_token
TELEGRAM_CHAT_ID=-100xxxxxxxxxx
BROKER=fyers
FYERS_CLIENT_ID=YOUR_APP_ID-100
FYERS_ACCESS_TOKEN=YOUR_DAILY_ACCESS_TOKEN
SCAN_SYMBOLS=NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX
DISPLAY_NAMES=NIFTY 50 OPTIONS,BANKNIFTY OPTIONS
VIP_LINK=https://t.me/your_vip_link
```

Run:

```bash
PYTHONPATH=src python -m tradebot.bot
```

For dry-run without Fyers credentials:

```env
BROKER=mock
```

## Optional AI confirmation filter

The bot now has an optional AI layer. Flow:

```text
Market data → Rule-based high-RRR strategy → AI review → Telegram signal
```

The AI does **not** create random trades. It only approves or rejects a setup already created by the strict 1:3+ strategy.

Example `.env` using OpenRouter or another OpenAI-compatible provider:

```env
AI_FILTER_ENABLED=true
AI_API_KEY=your_ai_api_key
AI_API_BASE_URL=https://openrouter.ai/api/v1
AI_MODEL=openai/gpt-4o-mini
AI_MIN_CONFIDENCE=70
AI_FAIL_CLOSED=true
```

Other OpenAI-compatible examples:

```env
# Groq example
AI_API_BASE_URL=https://api.groq.com/openai/v1
AI_MODEL=llama-3.1-8b-instant

# OpenAI example
AI_API_BASE_URL=https://api.openai.com/v1
AI_MODEL=gpt-4o-mini
```

`AI_FAIL_CLOSED=true` means if the AI API fails, the trade is rejected for safety. Set it to `false` only if you want the rule-based signal to continue even when AI is down.

## Telegram behavior

Initial signal is sent with `send_message`. The bot stores that message ID. Every live update then calls:

```python
context.bot.edit_message_text(...)
```

This satisfies the no-spam trailing requirement. A chart may be sent once as a separate photo when the trade starts; it is not used for live price updates.

## Message formatting note

Telegram supports bold/italic via MarkdownV2 or HTML, but it does **not** allow forcing text color such as black. The templates are hardcoded with Telegram HTML parse mode and bold premium emoji formatting.

## Fyers symbols

Use exact symbols accepted by your Fyers account. Index examples:

```text
NSE:NIFTY50-INDEX
NSE:NIFTYBANK-INDEX
NSE:SENSEX-INDEX
NSE:RELIANCE-EQ
```

For options, use the exact current contract symbol from Fyers. If you want true ATM-option auto-selection, add an option-chain module or provide a daily list of option symbols in `SCAN_SYMBOLS`.

## Railway deployment

Railway files included:

- `railway.json` with start command
- `Procfile` with worker command

Steps:

1. Push this project to GitHub.
2. Open Railway → New Project → Deploy from GitHub repo.
3. Select the repository.
4. Railway should detect Python/Nixpacks and install `requirements.txt`.
5. Add variables in Railway → Service → Variables.
6. Deploy/redeploy the service.

Required Railway variables:

```env
TELEGRAM_BOT_TOKEN=your_botfather_token
TELEGRAM_CHAT_ID=-100xxxxxxxxxx
BROKER=fyers
FYERS_CLIENT_ID=YOUR_APP_ID-100
FYERS_ACCESS_TOKEN=YOUR_DAILY_ACCESS_TOKEN
SCAN_SYMBOLS=NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX
DISPLAY_NAMES=NIFTY 50 OPTIONS,BANKNIFTY OPTIONS
VIP_LINK=https://t.me/your_vip_link
```

Optional AI variables:

```env
AI_FILTER_ENABLED=true
AI_API_KEY=your_ai_api_key
AI_API_BASE_URL=https://openrouter.ai/api/v1
AI_MODEL=openai/gpt-4o-mini
AI_MIN_CONFIDENCE=70
AI_FAIL_CLOSED=true
AI_TIMEOUT_SECONDS=20
```

Other optional variables:

```env
MAX_CALLS_PER_DAY=5
MIN_CALLS_PER_DAY=2
SCAN_INTERVAL_SECONDS=60
TRAIL_INTERVAL_SECONDS=15
MAX_RISK_POINTS=80
MIN_RRR=3
CLOSE_ON_TARGET=3
VIP_PROMO_DELAY_MINUTES=10
GENERATE_CHARTS=true
```

Important: Fyers access token may expire and usually needs refreshing. If the service is running but signals are not coming, check Railway logs first.

## Render deployment

1. Push this folder to GitHub.
2. Create a new Render service from `render.yaml`.
3. Add secret environment variables:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `FYERS_CLIENT_ID`
   - `FYERS_ACCESS_TOKEN`
   - `VIP_LINK`
4. Start the worker.

Render free availability can change and free services may sleep/restart. For serious live-market usage, use a paid always-on worker or a VPS.

## GitHub Actions deployment

The workflow starts around the two market windows on weekdays. Add repository secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `FYERS_CLIENT_ID`
- `FYERS_ACCESS_TOKEN`
- `VIP_LINK`

Add repository variables:

- `SCAN_SYMBOLS`
- `DISPLAY_NAMES`

Limitations: GitHub runners are not designed as permanent trading infrastructure. They can stop, be delayed, or miss live ticks.

## Safety checklist before live use

- Backtest and forward-test for at least 2-4 weeks.
- Keep `MAX_RISK_POINTS` realistic for options volatility.
- Use paper/live-small mode before public calls.
- Do not advertise guaranteed returns.
- Replace the VIP link and ensure promotional claims are compliant and truthful.
