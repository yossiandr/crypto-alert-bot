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
CHANNEL_ID  = os.environ.get("CHANNEL_ID", "@nama_channel_kamu")
CHECK_EVERY = 5   # menit
DB_PATH     = "seen.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ─── KEYWORDS ──────────────────────────────────────────────────────────────────
KEYWORDS = [
    # Delisting
    "delist", "delisting", "will delist", "to delist",
    "removal", "remove trading pair", "trading pair removal",
    "cease trading", "suspend trading", "discontinue",
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
    {
        "name": "Binance",
        "type": "binance_api",
        "url": "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20&catalogId=48",
        "logo": "🟡",
        "base_link": "https://www.binance.com/en/support/announcement/",
    },
    {
        "name": "Bybit",
        "type": "rss",
        "url": "https://announcements.bybit.com/en-US/rss/?category=delistings",
        "logo": "🟠",
    },
    {
        "name": "OKX",
        "type": "okx_api",
        "url": "https://www.okx.com/v2/support/home/web?language=en_US&pageSize=20&page=1",
        "logo": "⚫",
        "base_link": "https://www.okx.com/help-center/",
    },
    {
        "name": "KuCoin",
        "type": "kucoin_api",
        "url": "https://www.kucoin.com/_api/cms/articles?page=1&pageSize=20&category=delisting&lang=en_US",
        "logo": "🟢",
        "base_link": "https://www.kucoin.com/announcement/",
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
        "url": "https://www.mexc.com/support/sections/360000030572",
        "logo": "🔷",
    },
    {
        "name": "BingX",
        "type": "scrape",
        "url": "https://bingx.com/en-us/support/categories/360001496233/",
        "logo": "🟣",
    },
    {
        "name": "Poloniex",
        "type": "scrape",
        "url": "https://support.poloniex.com/hc/en-us/sections/360008730014-Delistings",
        "logo": "🔴",
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

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
            send_telegram(format_message(source["logo"], source["name"], title, link))
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error RSS {source['name']}: {e}")


def fetch_binance_api(source: dict):
    log.info(f"🔌 Cek API: {source['name']}")
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=15)
        data = r.json()
        articles = data.get("data", {}).get("articles", [])
        for article in articles:
            title = article.get("title", "")
            code  = article.get("code", "")
            uid   = code
            link  = f"{source['base_link']}{code}"
            if not uid or not is_relevant(title):
                continue
            if is_seen(uid):
                continue
            mark_seen(uid)
            send_telegram(format_message(source["logo"], source["name"], title, link))
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error API {source['name']}: {e}")


def fetch_kucoin_api(source: dict):
    log.info(f"🔌 Cek API: {source['name']}")
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=15)
        data = r.json()
        items = data.get("items", []) or data.get("data", {}).get("items", [])
        for item in items:
            title = item.get("title", "")
            uid   = str(item.get("id", ""))
            slug  = item.get("path", uid)
            link  = f"{source['base_link']}{slug}"
            if not uid or not is_relevant(title):
                continue
            if is_seen(uid):
                continue
            mark_seen(uid)
            send_telegram(format_message(source["logo"], source["name"], title, link))
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error API {source['name']}: {e}")


def fetch_okx_api(source: dict):
    log.info(f"🔌 Cek API: {source['name']}")
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=15)
        data = r.json()
        articles = []
        if isinstance(data.get("data"), list):
            articles = data["data"]
        elif isinstance(data.get("data"), dict):
            articles = data["data"].get("list", [])
        for article in articles:
            title = article.get("title", "") or article.get("name", "")
            uid   = str(article.get("id", ""))
            link  = article.get("url", "") or f"{source['base_link']}{uid}"
            if not uid or not is_relevant(title):
                continue
            if is_seen(uid):
                continue
            mark_seen(uid)
            send_telegram(format_message(source["logo"], source["name"], title, link))
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error API {source['name']}: {e}")


def fetch_scrape(source: dict):
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
            uid = href
            if is_seen(uid):
                continue
            mark_seen(uid)
            send_telegram(format_message(source["logo"], source["name"], title, href))
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error scrape {source['name']}: {e}")

# ─── MAIN JOB ──────────────────────────────────────────────────────────────────
def check_all():
    log.info("🔄 Mulai pengecekan semua CEX...")
    for source in SOURCES:
        t = source["type"]
        if t == "rss":
            fetch_rss(source)
        elif t == "binance_api":
            fetch_binance_api(source)
        elif t == "kucoin_api":
            fetch_kucoin_api(source)
        elif t == "okx_api":
            fetch_okx_api(source)
        elif t == "scrape":
            fetch_scrape(source)
    log.info("✅ Selesai pengecekan.")

# ─── TEST MESSAGE ──────────────────────────────────────────────────────────────
def send_test_message():
    msg = (
        "🤖 <b>Crypto CEX Alarm Bot Aktif!</b>\n\n"
        "✅ Bot berhasil terhubung ke channel ini.\n"
        "📡 Memantau: Binance, Bybit, OKX, KuCoin, Gate.io, MEXC, BingX, Poloniex\n"
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
