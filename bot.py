import os
import time
import sqlite3
import logging
import requests
import feedparser
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from bs4 import BeautifulSoup

# ─── CONFIG ────────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "ISI_TOKEN_BOT_KAMU")
CHANNEL_ID  = os.environ.get("CHANNEL_ID", "@nama_channel_kamu")  # contoh: @cryptoalertid
CHECK_EVERY = 5   # menit
DB_PATH     = "seen.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ─── KEYWORDS ──────────────────────────────────────────────────────────────────
KEYWORDS = [
    # Delisting
    "delist", "delisting", "will delist",
    "removal", "remove trading pair",
    # Migration & Contract
    "migration", "migrate", "token migration",
    "contract change", "contract address",
    "token swap", "token rebranding",
    # Ticker & Symbol
    "ticker change", "ticker symbol", "symbol change",
    # Network & Upgrade
    "network upgrade", "mainnet upgrade", "mainnet launch",
    "hard fork", "hardfork", "hard-fork",
    "chain upgrade", "protocol upgrade",
    "snapshot", "airdrop snapshot",
]

# ─── CEX SOURCES ───────────────────────────────────────────────────────────────
# Format: { "name": str, "type": "rss"|"api"|"scrape", "url": str, "logo": str }
SOURCES = [
    {
        "name": "Binance",
        "type": "rss",
        "url": "https://www.binance.com/en/support/announcement/rss",
        "logo": "🟡",
    },
    {
        "name": "Bybit",
        "type": "rss",
        "url": "https://announcements.bybit.com/en-US/rss/",
        "logo": "🟠",
    },
    {
        "name": "OKX",
        "type": "rss",
        "url": "https://www.okx.com/help-center/rss/announcements.xml",
        "logo": "⚫",
    },
    {
        "name": "KuCoin",
        "type": "rss",
        "url": "https://www.kucoin.com/news/rss",
        "logo": "🟢",
    },
    {
        "name": "Gate.io",
        "type": "rss",
        "url": "https://www.gate.io/en/rss/articles",
        "logo": "🔵",
    },
    {
        "name": "MEXC",
        "type": "scrape",
        "url": "https://www.mexc.com/support/sections/360000030572",  # Delisting section
        "logo": "🔷",
    },
    {
        "name": "BingX",
        "type": "scrape",
        "url": "https://bingx.com/en-us/support/articles/",
        "logo": "🟣",
    },
]

# ─── DATABASE ──────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            id   TEXT PRIMARY KEY,
            seen_at TEXT
        )
    """)
    con.commit()
    con.close()

def is_seen(uid: str) -> bool:
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT 1 FROM seen WHERE id=?", (uid,)).fetchone()
    con.close()
    return row is not None

def mark_seen(uid: str):
    con = sqlite3.connect(DB_PATH)
    con.execute("INSERT OR IGNORE INTO seen VALUES (?,?)", (uid, datetime.utcnow().isoformat()))
    con.commit()
    con.close()

# ─── KEYWORD CHECK ─────────────────────────────────────────────────────────────
def is_relevant(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in KEYWORDS)

# ─── TELEGRAM SENDER ───────────────────────────────────────────────────────────
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        log.info("✅ Pesan terkirim ke channel")
    except Exception as e:
        log.error(f"❌ Gagal kirim ke Telegram: {e}")

def format_message(logo: str, cex: str, title: str, link: str) -> str:
    return (
        f"{logo} <b>[{cex}]</b>\n"
        f"{title}\n"
        f"🔗 <a href='{link}'>Lihat Announcement</a>"
    )

# ─── FETCHERS ──────────────────────────────────────────────────────────────────
def fetch_rss(source: dict):
    log.info(f"📡 Cek RSS: {source['name']}")
    try:
        feed = feedparser.parse(source["url"])
        for entry in feed.entries:
            title = entry.get("title", "")
            link  = entry.get("link", "")
            uid   = entry.get("id", link)

            if not uid or not is_relevant(title):
                continue
            if is_seen(uid):
                continue

            mark_seen(uid)
            msg = format_message(source["logo"], source["name"], title, link)
            send_telegram(msg)
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error RSS {source['name']}: {e}")


def fetch_scrape(source: dict):
    log.info(f"🕷️  Scrape: {source['name']}")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; CryptoAlertBot/1.0)"}
    try:
        r = requests.get(source["url"], headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        # Ambil semua tag <a> yang punya teks panjang (judul artikel)
        links = soup.find_all("a", href=True)
        for a in links:
            title = a.get_text(strip=True)
            href  = a["href"]

            if len(title) < 15:
                continue
            if not is_relevant(title):
                continue

            # Pastikan link absolut
            if href.startswith("/"):
                base = "/".join(source["url"].split("/")[:3])
                href = base + href

            uid = href
            if is_seen(uid):
                continue

            mark_seen(uid)
            msg = format_message(source["logo"], source["name"], title, href)
            send_telegram(msg)
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error scrape {source['name']}: {e}")

# ─── MAIN JOB ──────────────────────────────────────────────────────────────────
def check_all():
    log.info("🔄 Mulai pengecekan semua CEX...")
    for source in SOURCES:
        if source["type"] == "rss":
            fetch_rss(source)
        elif source["type"] == "scrape":
            fetch_scrape(source)
    log.info("✅ Selesai pengecekan.")

# ─── TEST MESSAGE ──────────────────────────────────────────────────────────────
def send_test_message():
    msg = (
        "🤖 <b>Crypto CEX Alarm Bot Aktif!</b>\n\n"
        "✅ Bot berhasil terhubung ke channel ini.\n"
        "📡 Memantau: Binance, Bybit, OKX, KuCoin, Gate.io, MEXC, BingX\n"
        "⏱ Pengecekan setiap <b>5 menit</b> otomatis.\n\n"
        "🔔 Kamu akan dapat notif saat ada:\n"
        "• Delisting koin\n"
        "• Token migration\n"
        "• Contract change\n"
        "• Ticker/symbol change\n\n"
        "<i>Bot siap jalan 24 jam!</i> 🚀"
    )
    send_telegram(msg)

# ─── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    log.info("🚀 Bot dimulai!")

    # Kirim pesan test ke channel
    send_test_message()

    # Jalankan sekali langsung saat start
    check_all()

    # Jadwalkan setiap 5 menit
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(check_all, "interval", minutes=CHECK_EVERY)
    scheduler.start()
