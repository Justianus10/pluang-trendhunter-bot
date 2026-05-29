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
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Sentiment analyzer (singleton)
_sentiment_analyzer = SentimentIntensityAnalyzer()


def get_news_sentiment(ticker: str, hours: int = 24, max_news: int = 50):
    """Fetch news headlines for ticker and score sentiment via VADER.
    Returns dict: {score, label, count, headlines}
    """
    try:
        tk = yf.Ticker(ticker)
        news = getattr(tk, "news", None) or []
        if not news:
            return {"score": 0.0, "label": "NO_DATA", "count": 0, "headlines": []}

        cutoff_ts = datetime.now().timestamp() - hours * 3600
        scored = []
        for item in news[:max_news]:
            # yfinance news format varies — handle both old/new
            ts = item.get("providerPublishTime") or item.get("pubDate") or 0
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except Exception:
                    ts = 0
            if ts and ts < cutoff_ts:
                continue
            title = item.get("title") or item.get("content", {}).get("title") if isinstance(item.get("content"), dict) else item.get("title", "")
            if not title:
                continue
            publisher = item.get("publisher") or (item.get("content", {}).get("provider", {}).get("displayName", "?") if isinstance(item.get("content"), dict) else "?")
            scores = _sentiment_analyzer.polarity_scores(title)
            scored.append({
                "title": title[:120],
                "publisher": publisher,
                "score": scores["compound"],
                "ts": ts,
            })

        if not scored:
            return {"score": 0.0, "label": "NO_RECENT", "count": 0, "headlines": []}

        avg_score = sum(s["score"] for s in scored) / len(scored)

        if avg_score >= 0.5:     label = "SANGAT BULLISH"
        elif avg_score >= 0.2:   label = "BULLISH"
        elif avg_score >= -0.2:  label = "NETRAL"
        elif avg_score >= -0.5:  label = "BEARISH"
        else:                     label = "SANGAT BEARISH"

        # Top 3 most impactful (highest absolute score)
        scored_sorted = sorted(scored, key=lambda x: abs(x["score"]), reverse=True)
        top3 = scored_sorted[:3]

        return {
            "score": avg_score,
            "label": label,
            "count": len(scored),
            "headlines": top3,
        }
    except Exception as e:
        logger.error(f"News sentiment error for {ticker}: {e}")
        return {"score": 0.0, "label": "ERROR", "count": 0, "headlines": []}

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

# Universe Global Top 10 — best picks dari analisis 234 saham US, semua available di Pluang
TICKERS_GLOBAL = [
    ("KLAC", "GS"),  # GS = Global Semiconductor
    ("AMAT", "GS"),
    ("ASML", "GS"),
    ("LRCX", "GS"),
    ("QCOM", "GS"),
    ("MU",   "GS"),
    ("TGT",  "GD"),  # GD = Global Diversified
    ("SPG",  "GD"),
    ("MS",   "GD"),
    ("NUE",  "GD"),
]

# Untuk /saham command — bisa pakai ticker dari kedua universe
ALL_TICKERS = TICKERS + TICKERS_GLOBAL

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== CACHE =====
_cache = {"data": None, "timestamp": None}
_cache_global = {"data": None, "timestamp": None}
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


async def _fetch_parallel(tickers_list, label="batch"):
    """Fetch semua saham secara paralel — 5-10x lebih cepat dari sequential."""
    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(None, analyze_ticker, sym, tier) for sym, tier in tickers_list]
    raw = await asyncio.gather(*tasks, return_exceptions=True)
    results = []
    for r in raw:
        if isinstance(r, Exception):
            logger.error(f"Fetch error in {label}: {r}")
            continue
        if r is not None:
            results.append(r)
    return results


async def get_analysis():
    """Cached analysis 20 saham Pluang basket — refresh setiap 15 menit, paralel."""
    now = datetime.now(WIB)
    if (_cache["data"] is not None and _cache["timestamp"] is not None and
        (now - _cache["timestamp"]).total_seconds() < CACHE_MINUTES * 60):
        return _cache["data"]

    logger.info("Fetching fresh analysis for 20 stocks (parallel)...")
    results = await _fetch_parallel(TICKERS, label="basket")
    results.sort(key=lambda x: x["score"], reverse=True)
    _cache["data"] = results
    _cache["timestamp"] = now
    logger.info(f"Got {len(results)} results, cached.")
    return results


async def get_analysis_global():
    """Cached analysis untuk universe Global Top 10 — paralel."""
    now = datetime.now(WIB)
    if (_cache_global["data"] is not None and _cache_global["timestamp"] is not None and
        (now - _cache_global["timestamp"]).total_seconds() < CACHE_MINUTES * 60):
        return _cache_global["data"]

    logger.info("Fetching fresh global analysis (parallel)...")
    results = await _fetch_parallel(TICKERS_GLOBAL, label="global")
    results.sort(key=lambda x: x["score"], reverse=True)
    _cache_global["data"] = results
    _cache_global["timestamp"] = now
    logger.info(f"Got {len(results)} global results, cached.")
    return results


# ===== TELEGRAM HANDLERS =====

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 *Selamat datang di Pluang Trend Hunter Bot!*\n\n"
        "Bot analisa saham US untuk swing 1-2 minggu.\n\n"
        "📊 *PLUANG BASKET — 20 saham populer:*\n"
        "/cek — ranking 20 saham\n"
        "/beli — rekomendasi BELI (skor ≥75)\n"
        "/watch — HOLD + trend UP\n"
        "/jual — rekomendasi JUAL\n"
        "/pocket — top 7 + alokasi %\n\n"
        "🌍 *GLOBAL TOP 10 — best picks dari 234 saham US:*\n"
        "/global — ranking 10 saham global pilihan\n"
        "/global\\_beli — rekomendasi BELI dari global\n"
        "/global\\_top5 — TOP 5 BEST EXECUTION (filtered + diversified) ⭐\n"
        "/global\\_pocket — alokasi Pocket 10 saham\n\n"
        "🔍 *Saham spesifik (30 saham):*\n"
        "/saham NVDA — analisa detail (teknikal + sentiment)\n"
        "/news NVDA — berita 24 jam + skor sentiment\n\n"
        "🔔 *Auto-notif harian:*\n"
        "/subscribe — notif /pocket otomatis jam 19:00 WIB\n"
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


async def cmd_global(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ranking 10 saham Global Top Picks."""
    await update.message.reply_text("⏳ Memproses 10 saham Global Top...")
    try:
        results = await get_analysis_global()
        if not results:
            await update.message.reply_text("❌ Gagal fetch data. Coba lagi sebentar.")
            return

        emoji = {"BELI": "🟢", "WATCH": "🟡", "HOLD": "⚪", "JUAL": "🔴"}
        # PLAIN TEXT (no parse_mode) — paling aman, tidak ada masalah escape
        msg = "🌍 GLOBAL TOP 10 — BEST PICKS\n"
        msg += now_wib().strftime("%Y-%m-%d %H:%M WIB") + "\n\n"
        for i, r in enumerate(results, 1):
            sec = "Semi" if r["tier"] == "GS" else "Div"
            stat = r.get("status", "?")
            score = r.get("score", 0)
            chg = r.get("chg_10d", 0)
            em = emoji.get(stat, "⚪")
            msg += f"{i:>2}. {r['ticker']:5} [{sec}] Skor {score} {em}{stat}  10D: {chg:+.1f}%\n"
        msg += "\nGS = Semiconductor | GD = Diversified\n"
        msg += "Semua available di Pluang.\n"
        msg += "Chat /global_beli untuk detail Entry/SL/TP.\n"
        msg += "Chat /global_pocket untuk alokasi Pocket."
        await update.message.reply_text(msg)
    except Exception as e:
        logger.exception("cmd_global error")
        await update.message.reply_text(f"❌ Error: {str(e)[:200]}")


async def cmd_global_beli(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rekomendasi BELI dari 10 saham Global."""
    await update.message.reply_text("⏳ Cari rekomendasi BELI dari Global Top...")
    try:
        results = await get_analysis_global()
        buy = [r for r in results if r["status"] in ("BELI", "WATCH")]
        if not buy:
            await update.message.reply_text("🌍 GLOBAL BELI\n\nTidak ada sinyal BELI/WATCH hari ini.")
            return

        emoji = {"BELI": "🟢", "WATCH": "🟡"}
        msg = "🌍 GLOBAL TOP — REKOMENDASI BELI\n"
        msg += now_wib().strftime("%Y-%m-%d %H:%M WIB") + "\n\n"
        for i, r in enumerate(buy[:8], 1):
            sec = "Semi" if r["tier"] == "GS" else "Diversifikasi"
            stat = r.get("status", "?")
            em = emoji.get(stat, "⚪")
            msg += (
                f"#{i} {r['ticker']} [{sec}] {em}{stat} - Skor {r['score']}\n"
                f"   Entry: ${r['price']:.2f}\n"
                f"   SL:    ${r['sl']:.2f} (-{(1-r['sl']/r['price'])*100:.1f}%)\n"
                f"   TP1:   ${r['tp1']:.2f} (+{(r['tp1']/r['price']-1)*100:.1f}%)\n"
                f"   TP2:   ${r['tp2']:.2f} (+{(r['tp2']/r['price']-1)*100:.1f}%)\n"
                f"   RSI: {r['rsi']:.0f} | 10D: {r['chg_10d']:+.1f}%\n\n"
            )
        msg += "Hold 1-2 minggu | Semua available di Pluang\n"
        msg += "TIP: Chat /global_top5 untuk versi simplified."
        await update.message.reply_text(msg)
    except Exception as e:
        logger.exception("cmd_global_beli error")
        await update.message.reply_text(f"❌ Error: {str(e)[:200]}")


async def cmd_global_top5(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Top 5 dari Global 10 — filter extended move + sektor diversifikasi."""
    await update.message.reply_text("⏳ Memilih top 5 best execution picks...")
    try:
        results = await get_analysis_global()
        if not results:
            await update.message.reply_text("❌ Gagal fetch data. Coba lagi.")
            return

        # Filter: skip extended move (>13% 10D) untuk hindari chasing
        clean = [r for r in results if r.get("chg_10d", 0) < 13]
        # Sort by score desc
        clean.sort(key=lambda r: r.get("score", 0), reverse=True)

        # Diversifikasi: max 3 Semi (GS), sisanya Div (GD)
        top5 = []
        semi_count = 0
        div_count = 0
        for r in clean:
            if r["tier"] == "GS" and semi_count < 3:
                top5.append(r)
                semi_count += 1
            elif r["tier"] == "GD" and div_count < 3:
                top5.append(r)
                div_count += 1
            if len(top5) == 5:
                break

        # Fallback kalau tidak cukup diversifikasi
        if len(top5) < 5:
            for r in clean:
                if r not in top5:
                    top5.append(r)
                if len(top5) == 5:
                    break

        # Alokasi dinamis: weighted by score, dengan max 25% min 15%
        total_score = sum(r["score"] for r in top5)
        allocations = []
        for r in top5:
            base = r["score"] / total_score * 100
            base = max(15, min(25, base))  # clamp 15-25%
            allocations.append(base)
        # Normalize to exactly 100%
        total = sum(allocations)
        allocations = [a / total * 100 for a in allocations]

        msg = "🎯 TOP 5 BEST EXECUTION PICKS\n"
        msg += now_wib().strftime("%Y-%m-%d %H:%M WIB") + "\n"
        msg += "Filter: skip extended (>13% 10D) + diversifikasi sektor\n\n"

        emoji = {"BELI": "🟢", "WATCH": "🟡", "HOLD": "⚪", "JUAL": "🔴"}
        for i, (r, alloc) in enumerate(zip(top5, allocations), 1):
            sec = "Semi" if r["tier"] == "GS" else "Div"
            stat = r.get("status", "?")
            em = emoji.get(stat, "⚪")
            sl_pct  = (r["sl"]  / r["price"] - 1) * 100
            tp1_pct = (r["tp1"] / r["price"] - 1) * 100
            tp2_pct = (r["tp2"] / r["price"] - 1) * 100

            msg += f"#{i} {r['ticker']} [{sec}] {em}{stat}\n"
            msg += f"   Skor: {r['score']}/100 | RSI: {r.get('rsi', 0):.0f} | 10D: {r['chg_10d']:+.1f}%\n"
            msg += f"   💼 ALOKASI: {alloc:.0f}%\n"
            msg += f"   Entry: ${r['price']:.2f}\n"
            msg += f"   SL:    ${r['sl']:.2f} ({sl_pct:+.1f}%)\n"
            msg += f"   TP1:   ${r['tp1']:.2f} ({tp1_pct:+.1f}%)\n"
            msg += f"   TP2:   ${r['tp2']:.2f} ({tp2_pct:+.1f}%)\n\n"

        msg += "═══ TOTAL ALOKASI: 100% ═══\n\n"
        msg += "📋 CARA EKSEKUSI di PLUANG:\n"
        msg += "1. Buka Pluang → Pocket → + Buat Pocket\n"
        msg += "2. Nama: 'Trend Hunter Top 5'\n"
        msg += "3. Add 5 saham dengan alokasi di atas\n"
        msg += "4. Set modal (saran Rp 3-10jt awal)\n"
        msg += "5. Set TradingView alert SL untuk masing-masing\n"
        msg += "6. Tunggu US market buka 20:30 WIB\n\n"
        msg += "Hold target: 1-2 minggu | R:R 1:2 di TP2"
        await update.message.reply_text(msg)
    except Exception as e:
        logger.exception("cmd_global_top5 error")
        await update.message.reply_text(f"❌ Error: {str(e)[:200]}")


async def cmd_global_pocket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Alokasi Pocket dari 10 saham Global Top dengan weighting Semi-Heavy."""
    await update.message.reply_text("⏳ Generate Global Pocket allocation...")
    try:
        results = await get_analysis_global()
        if not results:
            await update.message.reply_text("❌ Gagal fetch data. Coba lagi.")
            return

        FIXED_ALLOCATION = {
            "KLAC": 15, "AMAT": 12, "ASML": 12, "LRCX": 10,
            "QCOM": 6,  "MU":   5,
            "TGT":  12, "SPG":  10, "MS":   10, "NUE":  8,
        }

        emoji = {"BELI": "🟢", "WATCH": "🟡", "HOLD": "⚪", "JUAL": "🔴"}
        msg = "💼 GLOBAL POCKET — SEMI HEAVY (10 saham, 1-2 minggu)\n"
        msg += now_wib().strftime("%Y-%m-%d %H:%M WIB") + "\n\n"

        sorted_results = sorted(results, key=lambda r: FIXED_ALLOCATION.get(r["ticker"], 0), reverse=True)
        for i, r in enumerate(sorted_results, 1):
            alloc = FIXED_ALLOCATION.get(r["ticker"], 0)
            sec = "Semi" if r["tier"] == "GS" else "Div"
            stat = r.get("status", "?")
            em = emoji.get(stat, "⚪")
            msg += f"{i:>2}. {r['ticker']:5} [{sec}] {alloc:>2}% {em}{stat}\n"

        msg += "\n📊 Komposisi:\n"
        msg += "  • 60% Semiconductor (AI boom)\n"
        msg += "  • 40% Diversifikasi (Retail/REIT/Finance/Materials)\n\n"
        msg += "📋 Cara pakai di Pluang:\n"
        msg += "1. Pluang → Pocket → + Buat Pocket\n"
        msg += "2. Nama: 'Trend Hunter Global'\n"
        msg += "3. Add 10 saham dengan alokasi di atas\n"
        msg += "4. Set modal (saran: Rp 3-10jt awal)\n"
        msg += "5. Re-balance tiap Senin pagi\n\n"
        msg += "⚠️ Set TradingView alert SL — Pluang tidak auto-SL.\n\n"
        msg += "TIP: Chat /global_top5 untuk versi simplified (5 saham terbaik)."
        await update.message.reply_text(msg)
    except Exception as e:
        logger.exception("cmd_global_pocket error")
        await update.message.reply_text(f"❌ Error: {str(e)[:200]}")


async def cmd_saham(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Analisa 1 saham spesifik. Cara pakai: /saham NVDA"""
    valid_tickers = {t[0]: t[1] for t in ALL_TICKERS}

    if not context.args:
        basket = ", ".join(t[0] for t in TICKERS)
        global_ = ", ".join(t[0] for t in TICKERS_GLOBAL)
        await update.message.reply_text(
            f"Cara pakai: `/saham NVDA`\n\n"
            f"*Pluang Basket (20):*\n{basket}\n\n"
            f"*Global Top 10:*\n{global_}",
            parse_mode="Markdown"
        )
        return

    ticker = context.args[0].upper()
    if ticker not in valid_tickers:
        await update.message.reply_text(
            f"❌ Saham *{ticker}* tidak ada di universe (20 Pluang + 10 Global).\n\n"
            f"Pakai salah satu dari:\n{', '.join(valid_tickers.keys())}",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(f"⏳ Menganalisa {ticker} + berita 24h...")

    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, analyze_ticker, ticker, valid_tickers[ticker])

    if r is None:
        await update.message.reply_text(f"❌ Gagal fetch data {ticker}. Coba lagi nanti.")
        return

    sentiment = await loop.run_in_executor(None, get_news_sentiment, ticker)

    emoji = {"BELI": "🟢", "WATCH": "🟡", "HOLD": "⚪", "JUAL": "🔴"}
    sl_pct  = (r["sl"]  / r["price"] - 1) * 100
    tp1_pct = (r["tp1"] / r["price"] - 1) * 100
    tp2_pct = (r["tp2"] / r["price"] - 1) * 100

    msg = f"{emoji[r['status']]} *ANALISA {r['ticker']}* (Tier {r['tier']})\n"
    msg += f"_{now_wib().strftime('%Y-%m-%d %H:%M WIB')}_\n\n"
    msg += f"*Status Teknikal:* {r['status']}  |  *Skor:* {r['score']}/100\n\n"
    msg += f"📊 *Data Teknikal:*\n"
    msg += f"  Harga: `${r['price']:.2f}`\n"
    msg += f"  RSI(14): {r['rsi']:.0f}\n"
    msg += f"  Trend EMA20 vs EMA50: {r['trend']}\n"
    msg += f"  Move 10 hari: {r['chg_10d']:+.1f}%\n\n"

    # Sentiment block
    sent_emoji = "🟢" if sentiment["score"] >= 0.2 else "🔴" if sentiment["score"] <= -0.2 else "⚪"
    msg += f"📰 *Sentiment Berita 24h* {sent_emoji}\n"
    if sentiment["label"] in ("NO_DATA", "NO_RECENT", "ERROR"):
        msg += f"  _Tidak ada berita terkini_\n\n"
    else:
        msg += f"  Skor: `{sentiment['score']:+.2f}` ({sentiment['label']})\n"
        msg += f"  Berita dianalisa: {sentiment['count']} artikel\n"
        if sentiment["headlines"]:
            msg += f"\n  *Headlines penting:*\n"
            for h in sentiment["headlines"]:
                tone = "✅" if h["score"] >= 0.2 else "⚠️" if h["score"] <= -0.2 else "▫️"
                msg += f"  {tone} _{h['title'][:80]}_ (`{h['score']:+.2f}`)\n"
        msg += "\n"

    msg += f"💰 *Level Trading (ATR-based):*\n"
    msg += f"  Entry: `${r['price']:.2f}`\n"
    msg += f"  SL:    `${r['sl']:.2f}` ({sl_pct:+.1f}%)\n"
    msg += f"  TP1:   `${r['tp1']:.2f}` ({tp1_pct:+.1f}%)\n"
    msg += f"  TP2:   `${r['tp2']:.2f}` ({tp2_pct:+.1f}%)\n\n"

    # Confluence check
    tech_bullish = r["status"] in ("BELI", "WATCH")
    tech_bearish = r["status"] == "JUAL"
    sent_bullish = sentiment["score"] >= 0.2
    sent_bearish = sentiment["score"] <= -0.2
    sent_strong_bull = sentiment["score"] >= 0.5
    sent_strong_bear = sentiment["score"] <= -0.5
    sent_available = sentiment["label"] not in ("NO_DATA", "NO_RECENT", "ERROR")

    # Extended move detection (chasing risk)
    extended_up   = r["chg_10d"] >= 15
    extended_down = r["chg_10d"] <= -15

    tech_neutral = r["status"] == "HOLD"

    msg += "🎯 *Confluence Check:*\n"
    if tech_bullish and sent_bullish:
        msg += "  ✅ Teknikal + Sentiment **ALIGN BULLISH** → confidence TINGGI\n"
    elif tech_bearish and sent_bearish:
        msg += "  ✅ Teknikal + Sentiment **ALIGN BEARISH** → confidence TINGGI\n"
    elif tech_bullish and sent_bearish:
        msg += "  ⚠️ Teknikal bullish tapi Sentiment bearish → **HATI-HATI**, mungkin trap\n"
    elif tech_bearish and sent_bullish:
        msg += "  ⚠️ Teknikal bearish tapi Sentiment bullish → **CAMPUR**, skip atau cek manual\n"
    elif tech_neutral and sent_bullish:
        msg += f"  💡 Teknikal HOLD tapi Sentiment **BULLISH** ({sentiment['score']:+.2f}) → **watch breakout**\n"
    elif tech_neutral and sent_bearish:
        msg += f"  💡 Teknikal HOLD tapi Sentiment **BEARISH** ({sentiment['score']:+.2f}) → **watch breakdown**\n"
    elif not sent_available:
        msg += "  ⚪ Sentiment tidak tersedia — pakai teknikal saja\n"
    else:
        msg += "  ⚪ Sentiment netral — momentum belum konfirmasi\n"

    # Extended move warning
    if extended_up:
        msg += f"  ⚠️ *EXTENDED:* Sudah pump +{r['chg_10d']:.1f}% dalam 10 hari → risk chasing tinggi\n"
    elif extended_down:
        msg += f"  ⚠️ *EXTENDED:* Sudah drop {r['chg_10d']:.1f}% dalam 10 hari → risk catching falling knife\n"

    # Smart Action message — context-aware
    msg += "\n"
    if r["status"] == "BELI":
        if extended_up and not sent_strong_bull:
            msg += ("⚠️ *Action:* Skor BELI tapi sudah pump +" + f"{r['chg_10d']:.1f}%" +
                    " dan sentiment tidak konfirmasi kuat. "
                    "**TUNGGU PULLBACK** ke EMA20, atau entry dengan 50% position size saja.")
        elif extended_up and sent_strong_bull:
            msg += ("✅ *Action:* Entry boleh, tapi sudah extended. Pakai 70% position size, "
                    "SL ketat dekat EMA20.")
        elif tech_bullish and sent_bearish:
            msg += ("⚠️ *Action:* Skor teknikal BELI tapi sentiment bearish — "
                    "**SKIP** atau tunggu sentiment netral/positif.")
        elif sent_available and not sent_bullish:
            msg += ("⏸ *Action:* Skor BELI tapi sentiment belum konfirmasi. "
                    "Entry dengan 70% position size, atau tunggu sentiment naik ≥+0.2.")
        else:
            msg += "✅ *Action:* Entry sekarang. Pasang SL ketat. Risk 1-2% modal."

    elif r["status"] == "WATCH":
        if extended_up:
            msg += ("⏸ *Action:* WATCH tapi sudah pump +" + f"{r['chg_10d']:.1f}%" +
                    ". **JANGAN FOMO** — tunggu pullback ke EMA20 baru entry.")
        elif sent_bullish:
            msg += ("✅ *Action:* WATCH + sentiment bullish = setup berkembang. "
                    "Tunggu pullback ke EMA20 untuk entry optimal.")
        elif sent_bearish:
            msg += ("⚠️ *Action:* WATCH tapi sentiment bearish — "
                    "trend bisa reverse. **SKIP** sampai sentiment netral.")
        else:
            msg += "⏸ *Action:* Trend uptrend, tapi entry kurang ideal. Tunggu pullback ke EMA20."

    elif r["status"] == "JUAL":
        if extended_down and sent_strong_bear:
            msg += ("🚨 *Action:* JUAL + sentiment sangat bearish + sudah drop besar. "
                    "**EXIT SEGERA**, jangan tunggu bounce.")
        elif sent_bullish and r["rsi"] <= 40:
            msg += ("⚡ *Action:* **POTENTIAL CONTRARIAN SETUP!** "
                    "JUAL teknikal TAPI sentiment bullish + RSI oversold "
                    f"({r['rsi']:.0f}). Pasar mungkin over-panik.\n\n"
                    "❌ JANGAN entry sekarang (technical belum konfirmasi).\n"
                    "👀 WATCH 3-5 hari ke depan:\n"
                    "  • Tunggu RSI cross up ≥40\n"
                    "  • + 1 bullish candle kuat\n"
                    "  • + sentiment tetap ≥+0.2\n"
                    "  Baru entry contrarian dengan SL ketat di low recent.")
        elif sent_bullish:
            msg += ("⚠️ *Action:* JUAL teknikal tapi sentiment bullish — "
                    "**CAMPUR**. Skip entry, tapi tunggu reversal pattern. "
                    "Watch ketat 3-5 hari.")
        elif extended_down:
            msg += ("⚠️ *Action:* JUAL tapi sudah drop besar — mungkin akan bounce. "
                    "Exit di rally kecil, atau tunggu reversal pattern.")
        else:
            msg += "🚨 *Action:* Exit posisi yang ada. Hindari entry baru."

    else:  # HOLD
        if sent_strong_bull:
            msg += ("👀 *Action:* HOLD teknikal tapi sentiment **SANGAT BULLISH** — "
                    "watch ketat, mungkin breakout sebentar lagi. Set price alert di resistance.")
        elif sent_bullish and extended_down:
            msg += (f"⚡ *Action:* HOLD + sentiment BULLISH ({sentiment['score']:+.2f}) + "
                    f"sudah drop {r['chg_10d']:.1f}% → **VALUE/CONTRARIAN SETUP**. "
                    "Watch RSI cross up + bullish candle baru entry kecil.")
        elif sent_bullish:
            msg += (f"💡 *Action:* HOLD tapi sentiment BULLISH ({sentiment['score']:+.2f}) — "
                    "setup berkembang. Watch breakout di resistance dekat. "
                    "Belum entry, tapi siap-siap.")
        elif sent_strong_bear:
            msg += ("👀 *Action:* HOLD teknikal tapi sentiment **SANGAT BEARISH** — "
                    "kalau punya posisi, siap-siap reduce.")
        elif sent_bearish:
            msg += (f"⚠️ *Action:* HOLD + sentiment bearish ({sentiment['score']:+.2f}) — "
                    "tekanan jual mulai muncul. Hindari entry, hold posisi jangan tambah.")
        elif extended_down:
            msg += (f"⚠️ *Action:* HOLD + sudah drop {r['chg_10d']:.1f}% — "
                    "falling knife risk. Tunggu stabilisasi sebelum entry.")
        else:
            msg += "⏸ *Action:* Netral. Hold posisi yang ada / tunggu setup berikutnya."

    msg += "\n\n_Hold target: 1-2 minggu | R:R 1:2 di TP2_"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tampilkan full berita 24 jam untuk 1 saham. /news NVDA"""
    valid_tickers = {t[0]: t[1] for t in ALL_TICKERS}
    if not context.args:
        await update.message.reply_text(
            "Cara pakai: `/news NVDA`\n\n"
            f"Saham tersedia (30): {', '.join(valid_tickers.keys())}",
            parse_mode="Markdown"
        )
        return

    ticker = context.args[0].upper()
    if ticker not in valid_tickers:
        await update.message.reply_text(
            f"❌ {ticker} tidak ada di universe.\n\nPakai: {', '.join(valid_tickers.keys())}",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(f"⏳ Fetch berita {ticker} 24 jam terakhir...")

    loop = asyncio.get_event_loop()
    sentiment = await loop.run_in_executor(None, get_news_sentiment, ticker, 24, 20)

    if sentiment["label"] in ("NO_DATA", "NO_RECENT", "ERROR"):
        await update.message.reply_text(
            f"📰 *BERITA {ticker}*\n_{now_wib().strftime('%Y-%m-%d %H:%M WIB')}_\n\n"
            "Tidak ada berita 24 jam terakhir.",
            parse_mode="Markdown"
        )
        return

    sent_emoji = "🟢" if sentiment["score"] >= 0.2 else "🔴" if sentiment["score"] <= -0.2 else "⚪"
    msg = f"📰 *BERITA {ticker} — 24 Jam Terakhir*\n"
    msg += f"_{now_wib().strftime('%Y-%m-%d %H:%M WIB')}_\n\n"
    msg += f"*Sentiment Agregat:* {sent_emoji} `{sentiment['score']:+.2f}` ({sentiment['label']})\n"
    msg += f"*Total artikel:* {sentiment['count']}\n\n"
    msg += "*Top headlines (impact tertinggi):*\n\n"

    for i, h in enumerate(sentiment["headlines"], 1):
        tone = "✅" if h["score"] >= 0.2 else "⚠️" if h["score"] <= -0.2 else "▫️"
        msg += f"{i}. {tone} _{h['title']}_\n"
        msg += f"   📊 Skor: `{h['score']:+.2f}` | Sumber: {h['publisher']}\n\n"

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
    app.add_handler(CommandHandler("global",       cmd_global))
    app.add_handler(CommandHandler("global_beli",  cmd_global_beli))
    app.add_handler(CommandHandler("global_top5",  cmd_global_top5))
    app.add_handler(CommandHandler("global_pocket", cmd_global_pocket))
    app.add_handler(CommandHandler("saham",       cmd_saham))
    app.add_handler(CommandHandler("news",        cmd_news))
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
