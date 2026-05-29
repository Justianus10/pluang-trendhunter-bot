"""
Pluang Trend Hunter — Telegram Bot
Bot Telegram untuk dapat sinyal swing 1-2 minggu dari 20 saham US Pluang.

Commands:
  /start  — sambutan
  /cek    — full ranking 20 saham (BELI/WATCH/HOLD/JUAL)
  /beli   — hanya rekomendasi BELI (skor >=75)
  /watch  — saham HOLD + trend UP (tunggu pullback)
  /jual   — hanya rekomendasi JUAL (skor <=30)
  /pocket — top 7 saham untuk Pocket Pluang dengan alokasi %
  /help   — daftar commands
"""

import os
import json
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta, time as dt_time
import asyncio

WIB = timezone(timedelta(hours=7))


def now_wib():
    return datetime.now(WIB)

import yfinance as yf
import pandas as pd
import numpy as np
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ===== SUBSCRIBERS — persist chat_id untuk auto-notif harian =====
CHATS_FILE = Path("chats.json")
SUBSCRIBED: set = set()


def load_subscribers():
    global SUBSCRIBED
    if CHATS_FILE.exists():
        try:
            SUBSCRIBED = set(json.loads(CHATS_FILE.read_text()))
        except Exception:
            SUBSCRIBED = set()


def save_subscribers():
    try:
        CHATS_FILE.write_text(json.dumps(list(SUBSCRIBED)))
    except Exception as e:
        logging.error(f"Failed to save subscribers: {e}")

# ===== CONFIG =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "PASTE_TOKEN_HERE")

TICKERS = [
    ("NVDA",  "T1"), ("TSLA",  "T1"), ("PLTR",  "T1"), ("AMZN",  "T1"),
    ("INTC",  "T1"), ("AAPL",  "T1"),
    ("AVGO",  "T2"), ("META",  "T2"), ("GOOGL", "T2"), ("MSFT",  "T2"),
    ("NFLX",  "T2"), ("CRWD",  "T2"), ("JPM",   "T2"),
    ("NIO",   "T3"), ("XPEV",  "T3"), ("SNAP",  "T3"), ("HOOD",  "T3"),
    ("RBLX",  "T3"), ("BABA",  "T3"), ("AMC",   "T3"),
]

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== CACHE =====
_cache = {"data": None, "timestamp": None}
CACHE_MINUTES = 15


def analyze_ticker(symbol: str, tier: str):
    try:
        df = yf.Ticker(symbol).history(period="6mo")
        if df.empty or len(df) < 60:
            return None

        close = df["Close"]
        vol   = df["Volume"]
        high, low = df["High"], df["Low"]

        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()

        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rsi   = 100 - 100 / (1 + gain / loss)

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd  = ema12 - ema26
        sig   = macd.ewm(span=9, adjust=False).mean()
        hist  = macd - sig

        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        vol_sma20 = vol.rolling(20).mean()

        price  = close.iloc[-1]
        e20    = ema20.iloc[-1]
        e50    = ema50.iloc[-1]
        rsi_v  = rsi.iloc[-1]
        rsi_p  = rsi.iloc[-2]
        hist_v = hist.iloc[-1]
        hist_p = hist.iloc[-2]
        atr_v  = atr.iloc[-1]
        vol_v  = vol.iloc[-1]
        vs_v   = vol_sma20.iloc[-1]

        # Pine signal logic
        trend_up    = e20 > e50
        trend_dn    = e20 < e50
        rsi_buy_x   = rsi_v > 50 and rsi_p <= 50
        rsi_sell_x  = rsi_v < 50 and rsi_p >= 50
        macd_buy_x  = hist_v > 0 and hist_p <= 0
        macd_sell_x = hist_v < 0 and hist_p >= 0
        vol_surge   = vol_v > vs_v * 1.2

        pine_buy  = trend_up and rsi_buy_x  and macd_buy_x  and vol_surge
        pine_sell = trend_dn and rsi_sell_x and macd_sell_x and vol_surge

        # Scoring 0-100
        trend_diff_pct = (e20 - e50) / price * 100
        trend_score = max(0.0, min(40.0, 20.0 + trend_diff_pct * 5.0))

        if 50 <= rsi_v <= 65:   rsi_score = 25.0
        elif 45 <= rsi_v < 50:  rsi_score = 18.0
        elif 65 < rsi_v <= 75:  rsi_score = 18.0
        elif 35 <= rsi_v < 45:  rsi_score = 10.0
        else:                    rsi_score = 5.0

        if hist_v > 0 and hist_v > hist_p:    macd_score = 20.0
        elif hist_v > 0:                       macd_score = 15.0
        elif hist_v < 0 and hist_v > hist_p:  macd_score = 8.0
        else:                                  macd_score = 0.0

        vol_score = min(15.0, (vol_v / max(vs_v, 1.0)) * 7.5)

        score = round(trend_score + rsi_score + macd_score + vol_score)

        # Status
        if pine_buy or score >= 75:
            status = "BELI"
        elif pine_sell or score <= 30:
            status = "JUAL"
        elif trend_up and 31 <= score <= 74:
            status = "WATCH"
        else:
            status = "HOLD"

        # 10-day price change
        chg_10d = (price / close.iloc[-11] - 1) * 100 if len(close) >= 11 else 0

        return {
            "ticker": symbol, "tier": tier, "price": price, "score": score,
            "status": status, "rsi": rsi_v, "atr": atr_v,
            "sl": price - 2.0 * atr_v,
            "tp1": price + 2.0 * atr_v,
            "tp2": price + 4.0 * atr_v,
            "trend": "UP" if trend_up else "DOWN",
            "chg_10d": chg_10d,
        }
    except Exception as e:
        logger.error(f"Error analyzing {symbol}: {e}")
        return None


async def get_analysis():
    """Cached analysis — refresh setiap 15 menit."""
    now = datetime.now(WIB)
    if (_cache["data"] is not None and _cache["timestamp"] is not None and
        (now - _cache["timestamp"]).total_seconds() < CACHE_MINUTES * 60):
        return _cache["data"]

    logger.info("Fetching fresh analysis for 20 stocks...")
    loop = asyncio.get_event_loop()
    results = []
    for sym, tier in TICKERS:
        r = await loop.run_in_executor(None, analyze_ticker, sym, tier)
        if r:
            results.append(r)

    results.sort(key=lambda x: x["score"], reverse=True)
    _cache["data"] = results
    _cache["timestamp"] = now
    return results


# ===== TELEGRAM HANDLERS =====

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 *Selamat datang di Pluang Trend Hunter Bot!*\n\n"
        "Bot ini analisa 20 saham US dari basket Pluang untuk swing 1-2 minggu.\n\n"
        "*Commands utama:*\n"
        "/cek — ranking 20 saham\n"
        "/beli — rekomendasi BELI (skor ≥75)\n"
        "/watch — saham HOLD + trend UP\n"
        "/jual — rekomendasi JUAL (skor ≤30)\n"
        "/pocket — top 7 untuk Pocket Pluang + alokasi %\n\n"
        "*Saham spesifik:*\n"
        "/saham NVDA — analisa 1 saham detail\n"
        "(ganti NVDA dengan ticker manapun di universe 20 saham)\n\n"
        "*Auto-notif harian:*\n"
        "/subscribe — aktifkan notif /pocket otomatis jam 19:00 WIB (1.5 jam sebelum US market buka)\n"
        "/unsubscribe — matikan notif\n\n"
        "/help — bantuan\n\n"
        "_Data refresh setiap 15 menit. Sumber: Yahoo Finance._"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_cek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Memproses 20 saham...")
    results = await get_analysis()

    emoji = {"BELI": "🟢", "WATCH": "🟡", "HOLD": "⚪", "JUAL": "🔴"}
    msg = f"📊 *RANKING 20 SAHAM*\n_{now_wib().strftime('%Y-%m-%d %H:%M WIB')}_\n\n"
    msg += "```\n"
    msg += f"{'#':>2} {'TKR':5} {'TR':2} {'SKR':>3} {'STATUS':6} {'10D':>6}\n"
    msg += "-" * 32 + "\n"
    for i, r in enumerate(results, 1):
        msg += f"{i:>2} {r['ticker']:5} {r['tier']:2} {r['score']:>3} {emoji[r['status']]}{r['status']:5} {r['chg_10d']:>+5.1f}%\n"
    msg += "```\n"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_beli(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Cari rekomendasi BELI...")
    results = await get_analysis()
    buy = [r for r in results if r["status"] == "BELI"]
    if not buy:
        await update.message.reply_text("🟢 *REKOMENDASI BELI*\n\nTidak ada sinyal BELI hari ini.\nCek /watch untuk saham HOLD+UP (tunggu pullback).",
                                          parse_mode="Markdown")
        return

    msg = f"🟢 *REKOMENDASI BELI HARI INI*\n_{now_wib().strftime('%Y-%m-%d %H:%M WIB')}_\n\n"
    for i, r in enumerate(buy[:7], 1):
        msg += (
            f"*{i}. {r['ticker']}* ({r['tier']}) — Skor {r['score']} ⭐\n"
            f"   Entry: `${r['price']:.2f}`\n"
            f"   SL:    `${r['sl']:.2f}` (-{(1-r['sl']/r['price'])*100:.1f}%)\n"
            f"   TP1:   `${r['tp1']:.2f}` (+{(r['tp1']/r['price']-1)*100:.1f}%)\n"
            f"   TP2:   `${r['tp2']:.2f}` (+{(r['tp2']/r['price']-1)*100:.1f}%)\n"
            f"   RSI: {r['rsi']:.0f} | 10D: {r['chg_10d']:+.1f}%\n\n"
        )
    msg += "_Hold 1-2 minggu | R:R 1:2 di TP2_"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Cari saham WATCH UP...")
    results = await get_analysis()
    watch = [r for r in results if r["status"] == "WATCH"]
    if not watch:
        await update.message.reply_text("🟡 *WATCH UP*\n\nTidak ada saham WATCH UP hari ini.", parse_mode="Markdown")
        return

    msg = f"🟡 *WATCH UP — TREND NAIK*\n_{now_wib().strftime('%Y-%m-%d %H:%M WIB')}_\n"
    msg += "_Strategi: tunggu pullback ke EMA20 baru beli_\n\n"
    for i, r in enumerate(watch[:7], 1):
        msg += (
            f"*{i}. {r['ticker']}* ({r['tier']}) — Skor {r['score']}\n"
            f"   Harga: `${r['price']:.2f}` | RSI {r['rsi']:.0f}\n"
            f"   10D: {r['chg_10d']:+.1f}%\n\n"
        )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_jual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Cari rekomendasi JUAL...")
    results = await get_analysis()
    sell = [r for r in results if r["status"] == "JUAL"]
    if not sell:
        await update.message.reply_text("🔴 *REKOMENDASI JUAL*\n\nTidak ada sinyal JUAL hari ini.", parse_mode="Markdown")
        return

    msg = f"🔴 *REKOMENDASI JUAL HARI INI*\n_{now_wib().strftime('%Y-%m-%d %H:%M WIB')}_\n"
    msg += "_Action: Exit posisi / Hindari entry baru_\n\n"
    for i, r in enumerate(sell, 1):
        msg += (
            f"*{i}. {r['ticker']}* ({r['tier']}) — Skor {r['score']} ⚠\n"
            f"   Harga: `${r['price']:.2f}`\n"
            f"   10D: {r['chg_10d']:+.1f}% | RSI {r['rsi']:.0f}\n\n"
        )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_pocket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Generate Pocket Recommender...")
    results = await get_analysis()

    # Filter: skor >=65 + trend UP + tier T1/T2 (balanced default)
    candidates = [r for r in results
                   if r["score"] >= 65 and r["trend"] == "UP" and r["tier"] in ("T1", "T2")]
    candidates = candidates[:7]

    if not candidates:
        await update.message.reply_text("💼 *POCKET RECOMMENDER*\n\nBelum cukup kandidat hari ini.\nTunggu sinyal berikutnya.",
                                          parse_mode="Markdown")
        return

    total_score = sum(r["score"] for r in candidates)

    msg = f"💼 *POCKET RECOMMENDER — 1-2 MINGGU*\n"
    msg += f"_{now_wib().strftime('%Y-%m-%d %H:%M WIB')} | Profil: Balanced (T1+T2)_\n\n"
    msg += "```\n"
    msg += f"{'#':>2} {'TKR':5} {'SKR':>3} {'ALOKASI':>8}\n"
    msg += "-" * 24 + "\n"
    for i, r in enumerate(candidates, 1):
        alloc = r["score"] / total_score * 100
        msg += f"{i:>2} {r['ticker']:5} {r['score']:>3} {alloc:>7.1f}%\n"
    msg += "```\n"
    msg += "\n*Cara pakai di Pluang:*\n"
    msg += "1. Buka Pluang → menu Pocket → + Buat Pocket\n"
    msg += "2. Nama: 'Trend Hunter Mingguan'\n"
    msg += "3. Pilih saham di atas sesuai alokasi\n"
    msg += "4. Re-balance setiap Senin pagi"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_saham(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Analisa 1 saham spesifik. Cara pakai: /saham NVDA"""
    valid_tickers = {t[0]: t[1] for t in TICKERS}

    if not context.args:
        tickers_list = ", ".join(valid_tickers.keys())
        await update.message.reply_text(
            f"Cara pakai: `/saham NVDA`\n\n"
            f"Saham yang tersedia (20 saham):\n{tickers_list}",
            parse_mode="Markdown"
        )
        return

    ticker = context.args[0].upper()
    if ticker not in valid_tickers:
        await update.message.reply_text(
            f"❌ Saham *{ticker}* tidak ada di universe Pluang Top 20.\n\n"
            f"Pakai salah satu dari:\n{', '.join(valid_tickers.keys())}",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(f"⏳ Menganalisa {ticker}...")

    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, analyze_ticker, ticker, valid_tickers[ticker])

    if r is None:
        await update.message.reply_text(f"❌ Gagal fetch data {ticker}. Coba lagi nanti.")
        return

    emoji = {"BELI": "🟢", "WATCH": "🟡", "HOLD": "⚪", "JUAL": "🔴"}
    sl_pct  = (r["sl"]  / r["price"] - 1) * 100
    tp1_pct = (r["tp1"] / r["price"] - 1) * 100
    tp2_pct = (r["tp2"] / r["price"] - 1) * 100

    msg = f"{emoji[r['status']]} *ANALISA {r['ticker']}* (Tier {r['tier']})\n"
    msg += f"_{now_wib().strftime('%Y-%m-%d %H:%M WIB')}_\n\n"
    msg += f"*Status:* {r['status']}  |  *Skor:* {r['score']}/100\n\n"
    msg += f"📊 *Data Teknikal:*\n"
    msg += f"  Harga: `${r['price']:.2f}`\n"
    msg += f"  RSI(14): {r['rsi']:.0f}\n"
    msg += f"  Trend EMA20 vs EMA50: {r['trend']}\n"
    msg += f"  Move 10 hari: {r['chg_10d']:+.1f}%\n\n"
    msg += f"💰 *Level Trading (ATR-based):*\n"
    msg += f"  Entry: `${r['price']:.2f}`\n"
    msg += f"  SL:    `${r['sl']:.2f}` ({sl_pct:+.1f}%)\n"
    msg += f"  TP1:   `${r['tp1']:.2f}` ({tp1_pct:+.1f}%)\n"
    msg += f"  TP2:   `${r['tp2']:.2f}` ({tp2_pct:+.1f}%)\n\n"

    if r["status"] == "BELI":
        msg += "✅ *Action:* Entry sekarang. Pasang SL ketat. Risk 1-2% modal."
    elif r["status"] == "WATCH":
        msg += "⏸ *Action:* Trend uptrend, tapi entry kurang ideal. Tunggu pullback ke EMA20."
    elif r["status"] == "JUAL":
        msg += "🚨 *Action:* Exit posisi yang ada. Hindari entry baru."
    else:
        msg += "⏸ *Action:* Netral. Hold posisi yang ada / tunggu setup berikutnya."

    msg += "\n\n_Hold target: 1-2 minggu | R:R 1:2 di TP2_"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Aktifkan auto-notif harian jam 07:00 WIB."""
    chat_id = update.effective_chat.id
    if chat_id in SUBSCRIBED:
        await update.message.reply_text(
            "✅ Anda sudah berlangganan auto-notif.\n\n"
            "Setiap sore jam *19:00 WIB* bot akan kirim rekomendasi Pocket otomatis "
            "(1.5 jam sebelum US market buka).\n\n"
            "Untuk berhenti: /unsubscribe",
            parse_mode="Markdown"
        )
        return

    SUBSCRIBED.add(chat_id)
    save_subscribers()
    await update.message.reply_text(
        "🔔 *BERLANGGANAN AKTIF!*\n\n"
        "Setiap sore jam *19:00 WIB* bot otomatis kirim:\n"
        "- Top 7 saham untuk Pocket\n"
        "- Alokasi % per saham\n"
        "- Status sinyal harian\n\n"
        "Timing pas: 1.5 jam sebelum US market buka (20:30 WIB), "
        "Anda sempat review & siapkan order di Pluang.\n\n"
        "Untuk berhenti: /unsubscribe",
        parse_mode="Markdown"
    )


async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Matikan auto-notif harian."""
    chat_id = update.effective_chat.id
    if chat_id not in SUBSCRIBED:
        await update.message.reply_text(
            "❌ Anda belum berlangganan auto-notif.\n\nUntuk aktifkan: /subscribe"
        )
        return

    SUBSCRIBED.discard(chat_id)
    save_subscribers()
    await update.message.reply_text(
        "🔕 *Berhenti berlangganan.*\n\n"
        "Tidak akan terima auto-notif harian lagi.\n"
        "Untuk aktifkan lagi: /subscribe",
        parse_mode="Markdown"
    )


async def daily_pocket_notif(context: ContextTypes.DEFAULT_TYPE):
    """Auto-notif Pocket Recommender setiap pagi jam 07:00 WIB."""
    if not SUBSCRIBED:
        logger.info("No subscribers — skip daily notif")
        return

    logger.info(f"Sending daily notif to {len(SUBSCRIBED)} subscribers...")
    results = await get_analysis()

    candidates = [r for r in results
                   if r["score"] >= 65 and r["trend"] == "UP" and r["tier"] in ("T1", "T2")]
    candidates = candidates[:7]

    if not candidates:
        msg = (
            "🌆 *NOTIFIKASI PRE-MARKET US*\n"
            f"_{now_wib().strftime('%Y-%m-%d %H:%M WIB')}_\n\n"
            "Belum cukup kandidat Pocket malam ini.\n"
            "Tunggu sinyal berikutnya — cek /cek atau /watch untuk peluang."
        )
    else:
        total_score = sum(r["score"] for r in candidates)
        msg = "🌆 *NOTIFIKASI PRE-MARKET US — POCKET MALAM INI*\n"
        msg += f"_{now_wib().strftime('%Y-%m-%d %H:%M WIB')}_\n\n"
        msg += "```\n"
        msg += f"{'#':>2} {'TKR':5} {'SKR':>3} {'ALOKASI':>8}\n"
        msg += "-" * 24 + "\n"
        for i, r in enumerate(candidates, 1):
            alloc = r["score"] / total_score * 100
            msg += f"{i:>2} {r['ticker']:5} {r['score']:>3} {alloc:>7.1f}%\n"
        msg += "```\n"
        msg += "\n💼 US market buka jam 20:30 WIB — Anda masih punya 1.5 jam untuk siapkan order di Pluang.\n"
        msg += "\n_Chat /beli untuk detail SL/TP, /unsubscribe untuk berhenti notif._"

    failed = []
    for chat_id in list(SUBSCRIBED):
        try:
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed to send to {chat_id}: {e}")
            failed.append(chat_id)

    # Clean up dead subscribers
    for chat_id in failed:
        SUBSCRIBED.discard(chat_id)
    if failed:
        save_subscribers()


def main():
    if BOT_TOKEN == "PASTE_TOKEN_HERE":
        print("ERROR: Set BOT_TOKEN environment variable atau edit langsung di bot.py")
        return

    load_subscribers()
    logger.info(f"Loaded {len(SUBSCRIBED)} subscribers from file")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("cek",         cmd_cek))
    app.add_handler(CommandHandler("beli",        cmd_beli))
    app.add_handler(CommandHandler("watch",       cmd_watch))
    app.add_handler(CommandHandler("jual",        cmd_jual))
    app.add_handler(CommandHandler("pocket",      cmd_pocket))
    app.add_handler(CommandHandler("saham",       cmd_saham))
    app.add_handler(CommandHandler("subscribe",   cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))

    # Schedule daily Pocket notif at 19:00 WIB (1.5 jam sebelum US market buka)
    job_queue = app.job_queue
    if job_queue is not None:
        job_queue.run_daily(
            daily_pocket_notif,
            time=dt_time(hour=19, minute=0, tzinfo=WIB),
            name="daily_pocket"
        )
        logger.info("Scheduled daily Pocket notif at 19:00 WIB (pre-market US)")
    else:
        logger.warning("JobQueue not available — daily notif disabled")

    logger.info("Bot started. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
