# Pluang Trend Hunter Telegram Bot — Setup Guide

Bot Telegram untuk analisa 20 saham US dari basket Pluang. Chat dari HP, dapat sinyal instan.

## Tampilan Bot

Anda chat: `/cek` → bot balas:
```
📊 RANKING 20 SAHAM
2026-05-29 19:00 WIB

#  TKR   TR SKR STATUS  10D
--------------------------------
 1 NVDA  T1  85 🟢BELI   +5.2%
 2 AMZN  T1  80 🟢BELI   +1.4%
 3 AVGO  T2  80 🟢BELI   +2.3%
 4 TSLA  T1  72 🟡WATCH  -0.7%
 ...
20 BABA  T3  23 🔴JUAL  -13.5%
```

Anda chat: `/pocket` → bot balas alokasi 7 saham siap dimasukkan ke Pocket Pluang.

---

## SETUP — 3 LANGKAH (30 menit total)

### LANGKAH 1 — Bikin Bot via BotFather (5 menit)

1. Buka **Telegram di HP**, search **@BotFather**, klik chat
2. Ketik: `/newbot`
3. Bot tanya nama → ketik: **Pluang Trend Hunter**
4. Bot tanya username (harus akhiran "bot") → ketik: **pluang_trendhunter_bot**
   (kalau sudah dipakai, coba: **pluang_thunter_xxx_bot** dengan xxx = inisial Anda)
5. BotFather kasih **token** seperti ini:
   ```
   7234567890:AAH-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
6. **COPY TOKEN INI** — simpan di Notes HP, akan dipakai di Langkah 2

### LANGKAH 2 — Deploy ke Cloud (20 menit, gratis)

Pakai **Railway.app** (free tier 500 jam/bulan = cukup 24/7 selama 20 hari + restart).

#### A. Push folder ke GitHub

1. Buka **github.com** di HP atau laptop, login (kalau belum ada akun → daftar gratis)
2. Klik **+** → **New repository**
3. Nama: **pluang-trendhunter-bot**
4. Visibility: **Private** (penting — token rahasia)
5. Klik **Create repository**
6. Upload semua file dari folder `D:\Khusus Forex\Pluang\TelegramBot\`:
   - bot.py
   - requirements.txt
   - Procfile
   - runtime.txt
   - SETUP.md (file ini)

   Cara upload via web: klik **uploading an existing file** → drag semua file → commit.

#### B. Deploy via Railway

1. Buka **railway.app**, klik **Login** → **Login with GitHub**
2. Klik **New Project** → **Deploy from GitHub repo**
3. Pilih repository **pluang-trendhunter-bot**
4. Railway auto-detect Python, mulai build (tunggu 2-3 menit)
5. Setelah build selesai, klik tab **Variables**:
   - Klik **+ New Variable**
   - Name: `BOT_TOKEN`
   - Value: paste token dari BotFather (Langkah 1 step 5)
   - Klik **Add**
6. Klik tab **Settings** → scroll ke **Deploy** → klik **Restart Deployment**
7. Tunggu status jadi **Active** (warna hijau)

### LANGKAH 3 — Test Bot di HP (5 menit)

1. Buka **Telegram di HP**
2. Search nama bot Anda: **@pluang_trendhunter_bot** (atau username yang Anda pilih)
3. Klik chat → klik **Start** (atau ketik `/start`)
4. Bot balas dengan daftar commands
5. Coba: ketik `/cek` → tunggu 30-60 detik → bot balas ranking 20 saham

🎉 **Bot Anda sudah jalan 24/7!**

---

## Cara Pakai Sehari-hari

| Kapan | Command | Untuk apa |
|-------|---------|-----------|
| **Pagi sebelum US market** (20:00 WIB) | `/pocket` | Dapat alokasi 7 saham + %, langsung buat Pocket di Pluang |
| **Sebelum entry** | `/beli` | Cek detail Entry/SL/TP untuk top kandidat BELI |
| **Sebelum exit** | `/jual` | Konfirmasi saham yang harus exit |
| **Mingguan (Senin)** | `/cek` | Full ranking untuk re-balance Pocket |
| **Cek peluang besok** | `/watch` | Saham yang lagi uptrend, tunggu pullback |

---

## Troubleshooting

### Bot tidak balas
- Cek di Railway: status harus **Active** (hijau)
- Cek **Logs** di Railway: cari error message
- Pastikan `BOT_TOKEN` di Variables sudah benar (copy dari BotFather, tidak ada spasi)

### Bot bilang "Error processing"
- Yahoo Finance kemungkinan rate-limit. Tunggu 5 menit, coba lagi.
- Atau market US tutup (weekend) → data tidak update.

### Free tier Railway habis (setelah ~20 hari)
- Upgrade ke paid $5/bulan → unlimited 24/7
- Atau pindah ke **Render.com** free tier (sleeps 15 menit kalau idle, tapi reload otomatis saat ada chat baru)
- Atau jalan di laptop sendiri saat dibutuhkan: `python bot.py`

---

## Jalankan Bot Lokal (Untuk Testing)

Kalau mau test bot di laptop dulu sebelum deploy:

```bash
cd "D:/Khusus Forex/Pluang/TelegramBot"
pip install -r requirements.txt
set BOT_TOKEN=7234567890:AAH-xxxxx  # ganti dengan token Anda
python bot.py
```

Lalu chat bot di Telegram. Tutup terminal = bot mati.

---

## Keamanan

- **JANGAN share token bot** ke siapapun — siapapun yang pegang token bisa kontrol bot
- Repository GitHub harus **Private**
- Token disimpan di **Railway Variables** (encrypted), bukan di kode

---

## Customize Lebih Lanjut

Mau ubah behavior bot? Edit `bot.py` lalu push lagi ke GitHub → Railway auto-redeploy.

Contoh customize:
- Tambah command `/saham NVDA` → analisa 1 saham spesifik
- Ubah filter Pocket profile (T1 only, atau Agresif semua tier)
- Tambah auto-notif setiap pagi jam 19:00 WIB (cron job)

Tanya saya kalau mau tambah fitur.

---

**Bot siap dipakai. Selamat trading swing 1-2 minggu! 🚀**
