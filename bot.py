import os
import time
import sqlite3
import logging
import requests
import feedparser
from datetime import datetime, timezone
from apscheduler.schedulers.blocking import BlockingScheduler
from bs4 import BeautifulSoup

# ─── CONFIG ────────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "8933095824:AAGpH6FkvPiMOsdwmo64j_wb5drqqwrhqDg")
CHANNEL_ID  = os.environ.get("CHANNEL_ID", "@cex_alertbot")
CHECK_EVERY = 5
DB_PATH     = "seen.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ─── KEYWORDS ──────────────────────────────────────────────────────────────────
KEYWORDS = [
    # Delisting
    "delist", "delisting", "will delist", "to delist",
    "removal", "remove trading pair", "trading pair removal",
    "cease trading", "suspend trading", "discontinue", "cease support",
    # Migration & Contract
    "migration", "migrate", "token migration",
    "contract change", "contract address", "new contract",
    "token swap", "token rebranding", "rebrand",
    # Ticker & Symbol
    "ticker change", "ticker symbol", "symbol change",
    "rename", "rebranding",
    # Network & Upgrade
    "network upgrade", "network support termination",
    "mainnet upgrade", "mainnet launch",
    "hard fork", "hardfork", "hard-fork",
    "chain upgrade", "protocol upgrade",
    "software upgrade", "node upgrade",
    # Snapshot
    "snapshot", "airdrop snapshot",
    # Notice
    "notice of removal", "important notice",
]

# ─── CEX SOURCES ───────────────────────────────────────────────────────────────
SOURCES = [
    # Binance: API resmi - list 161 (delisting) & 157 (network/wallet)
    {
        "name": "Binance",
        "type": "binance_api",
        "url": "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20&catalogId=161",
        "logo": "🟡",
        "base_link": "https://www.binance.com/en/support/announcement/",
    },
    {
        "name": "Binance",
        "type": "binance_api",
        "url": "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20&catalogId=157",
        "logo": "🟡",
        "base_link": "https://www.binance.com/en/support/announcement/",
    },
    # Bybit: scrape halaman semua announcement
    {
        "name": "Bybit",
        "type": "scrape",
        "url": "https://announcements.bybit.com/en/?category=&page=1",
        "logo": "🟠",
    },
    # OKX: scrape halaman latest announcements
    {
        "name": "OKX",
        "type": "scrape",
        "url": "https://www.okx.com/help/section/announcements-latest-announcements",
        "logo": "⚫",
    },
    # KuCoin: scrape halaman announcement
    {
        "name": "KuCoin",
        "type": "kucoin_api",
        "url": "https://api.kucoin.com/api/ua/v1/market/announcement?annType=latest-announcements&lang=en_US&page=1&pageSize=20",
        "logo": "🟢",
    },
    # Gate.io: scrape halaman announcement
    {
        "name": "Gate.io",
        "type": "scrape",
        "url": "https://www.gate.com/announcements/lastest",
        "logo": "🔵",
    },
    # MEXC: scrape halaman delisting
    {
        "name": "MEXC",
        "type": "scrape",
        "url": "https://www.mexc.com/support/sections/360000030572",
        "logo": "🔷",
    },
    # BingX: scrape halaman announcement
    {
        "name": "BingX",
        "type": "scrape",
        "url": "https://bingx.com/en/support/categories/360002065274",
        "logo": "🟣",
    },
    # Poloniex: scrape halaman latest announcements
    {
        "name": "Poloniex",
        "type": "scrape",
        "url": "https://support.poloniex.com/hc/en-us/sections/360006455114-Latest-Announcements",
        "logo": "🔴",
    },
    # HTX: scrape halaman support/announcement
    {
        "name": "HTX",
        "type": "scrape",
        "url": "https://www.htx.com/support/",
        "logo": "🟤",
    },
]

# ─── DATABASE ──────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            id      TEXT PRIMARY KEY,
            seen_at TEXT
        )
    """)
    con.commit()
    con.close()

def is_seen(uid):
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT 1 FROM seen WHERE id=?", (uid,)).fetchone()
    con.close()
    return row is not None

def mark_seen(uid):
    con = sqlite3.connect(DB_PATH)
    con.execute("INSERT OR IGNORE INTO seen VALUES (?,?)", (uid, datetime.now(timezone.utc).isoformat()))
    con.commit()
    con.close()

# ─── KEYWORD CHECK ─────────────────────────────────────────────────────────────
def is_relevant(text):
    return any(kw in text.lower() for kw in KEYWORDS)

# ─── TELEGRAM SENDER ───────────────────────────────────────────────────────────
def send_telegram(message):
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

def format_message(logo, cex, title, link):
    return f"{logo} <b>[{cex}]</b>\n{title}\n🔗 <a href='{link}'>Lihat Announcement</a>"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ─── FETCHERS ──────────────────────────────────────────────────────────────────

def fetch_binance_api(source):
    log.info(f"🔌 Cek API: {source['name']} ({source['url'][-3:]})")
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=15)
        articles = r.json().get("data", {}).get("articles", [])
        for article in articles:
            title = article.get("title", "")
            code  = article.get("code", "")
            if not code or not is_relevant(title):
                continue
            if is_seen(code):
                continue
            mark_seen(code)
            link = f"{source['base_link']}{code}"
            send_telegram(format_message(source["logo"], source["name"], title, link))
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error API {source['name']}: {e}")


def fetch_kucoin_api(source):
    log.info(f"🔌 Cek API: {source['name']}")
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=15)
        data = r.json()
        items = data.get("data", {}).get("list", [])
        for item in items:
            title = item.get("title", "")
            uid   = str(item.get("id", ""))
            url   = item.get("url", f"https://www.kucoin.com/announcement/{uid}")
            if not uid or not is_relevant(title):
                continue
            if is_seen(uid):
                continue
            mark_seen(uid)
            send_telegram(format_message(source["logo"], source["name"], title, url))
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error API {source['name']}: {e}")


def fetch_scrape(source):
    log.info(f"🕷️  Scrape: {source['name']}")
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            title = a.get_text(strip=True)
            href  = a["href"]
            if len(title) < 15 or not is_relevant(title):
                continue
            if href.startswith("/"):
                base = "/".join(source["url"].split("/")[:3])
                href = base + href
            elif not href.startswith("http"):
                continue
            if is_seen(href):
                continue
            mark_seen(href)
            send_telegram(format_message(source["logo"], source["name"], title, href))
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error scrape {source['name']}: {e}")

# ─── MAIN JOB ──────────────────────────────────────────────────────────────────
def check_all():
    log.info("🔄 Mulai pengecekan semua CEX...")
    for source in SOURCES:
        t = source["type"]
        if t == "binance_api":
            fetch_binance_api(source)
        elif t == "kucoin_api":
            fetch_kucoin_api(source)
        elif t == "scrape":
            fetch_scrape(source)
    log.info("✅ Selesai pengecekan.")

# ─── TEST MESSAGE ──────────────────────────────────────────────────────────────
def send_test_message():
    msg = (
        "🤖 <b>Crypto CEX Alarm Bot Aktif!</b>\n\n"
        "✅ Bot berhasil terhubung ke channel ini.\n"
        "📡 Memantau: Binance, Bybit, OKX, KuCoin, Gate.io, MEXC, BingX, Poloniex, HTX\n"
        "⏱ Pengecekan setiap <b>5 menit</b> otomatis.\n\n"
        "🔔 Notif untuk:\n"
        "• Delisting koin\n"
        "• Token migration / contract change\n"
        "• Ticker / symbol change\n"
        "• Network upgrade / mainnet upgrade\n"
        "• Hard fork\n"
        "• Notice of removal\n\n"
        "<i>Bot siap jalan 24 jam!</i> 🚀"
    )
    send_telegram(msg)

# ─── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    log.info("🚀 Bot dimulai!")
    send_test_message()
    check_all()
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(check_all, "interval", minutes=CHECK_EVERY)
    scheduler.start()
