# Jiga Bhai Gujarati Trader Bot

Automated Telegram trade manager bot for free channel signals and VIP conversion.

## Features

- 8:00 AM premium good morning post
- 8:10 AM market poll
- 9:00 AM ready alert
- 9:16 AM first market plan
- 9:15 AM live Fyers scanner
- 1:30 PM afternoon scanner
- 10:00 PM next-day advance market plan
- Auto ATM CE/PE option selection
- Minimum 20 setup analysis before trade
- Internal AI/risk filter
- One active trade at a time
- Max 5 trades per day
- Minimum 1:3 RRR setup
- Separate live update message every 10 points
- Trade updates reply/tag original trade
- Target hit white chart
- VIP promo after trade close
- Outbound-only scheduler mode, no Telegram polling conflict
- Railway deployment ready

## Railway Start Command

```bash
PYTHONPATH=src python -m tradebot.bot
```

## Important

Use Railway worker service, not Vercel.

## Required Railway Variables

See `.env.example`.

## Risk Note

Market risk applies. No profit is guaranteed.
