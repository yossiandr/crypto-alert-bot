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
BOT_TOKEN   = os.environ.get("BOT_TOKEN")
CHANNEL_ID  = os.environ.get("CHANNEL_ID")
CHECK_EVERY = 2
DB_PATH     = "seen.db"

if not BOT_TOKEN or not CHANNEL_ID:
    raise ValueError("BOT_TOKEN dan CHANNEL_ID harus diisi di Railway Variables!")

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
        "catalog_id": 161,
        "url": "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20&catalogId=161",
        "logo": "🟡",
        "base_link": "https://www.binance.com/en/support/announcement/",
    },
    {
        "name": "Binance",
        "type": "binance_api",
        "catalog_id": 157,
        "url": "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20&catalogId=157",
        "logo": "🟡",
        "base_link": "https://www.binance.com/en/support/announcement/",
    },
    {
        "name": "Bybit",
        "type": "bybit_scrape",
        "url": "https://announcements.bybit.com/en/?category=&page=1",
        "logo": "🟠",
        "base_link": "https://announcements.bybit.com",
    },
    {
        "name": "OKX",
        "type": "scrape",
        "url": "https://www.okx.com/help/section/announcements-latest-announcements",
        "logo": "⚫",
    },
    {
        "name": "KuCoin",
        "type": "kucoin_api",
        "url": "https://api.kucoin.com/api/ua/v1/market/announcement?annType=latest-announcements&lang=en_US&page=1&pageSize=20",
        "logo": "🟢",
    },
    {
        "name": "Gate.io",
        "type": "gate_api",
        "url": "https://www.gate.com/announcements/latest",
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
        "url": "https://bingx.com/en/support/categories/360002065274",
        "logo": "🟣",
    },
    {
        "name": "Poloniex",
        "type": "scrape",
        "url": "https://support.poloniex.com/hc/en-us/sections/360006455114-Latest-Announcements",
        "logo": "🔴",
    },
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

def fetch_binance_scrape(source):
    """Scrape halaman list/157 dan list/161 Binance langsung."""
    log.info(f"🕷️  Scrape: Binance ({source['url'].split('/')[-1]})")
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            # Link artikel Binance: /en/support/announcement/detail/xxxx
            if "/support/announcement/detail/" not in href:
                continue
            title = a.get_text(strip=True)
            if len(title) < 10 or not is_relevant(title):
                continue
            if href.startswith("/"):
                href = source["base_link"] + href
            uid = f"binance_{href.split('/')[-1]}"
            if is_seen(uid):
                continue
            mark_seen(uid)
            send_telegram(format_message(source["logo"], source["name"], title, href))
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error scrape Binance: {e}")


def fetch_binance_api(source):
    cat = source.get("catalog_id", "?")
    log.info(f"🔌 Cek API: Binance (catalog {cat})")
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=15)
        data = r.json()
        # Binance kadang return di catalogs[0].articles, kadang langsung articles
        catalogs = data.get("data", {}).get("catalogs", [])
        articles = []
        if catalogs:
            # Cari catalog yang sesuai catalog_id
            for cat_data in catalogs:
                if str(cat_data.get("catalogId")) == str(source.get("catalog_id")):
                    articles = cat_data.get("articles", [])
                    break
            # Kalau tidak ketemu, ambil semua
            if not articles:
                for cat_data in catalogs:
                    articles.extend(cat_data.get("articles", []))
        else:
            articles = data.get("data", {}).get("articles", [])

        log.info(f"   → {len(articles)} artikel ditemukan")
        for article in articles:
            title = article.get("title", "")
            code  = article.get("code", "")
            if not code or not is_relevant(title):
                continue
            uid = f"binance_{code}"
            if is_seen(uid):
                continue
            mark_seen(uid)
            link = f"{source['base_link']}{code}"
            send_telegram(format_message(source["logo"], source["name"], title, link))
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error API Binance: {e}")


def fetch_gate_api(source):
    log.info("🔌 Cek API: Gate.io")
    try:
        r = requests.get(
            source["url"],
            headers=HEADERS,
            timeout=15
        )
        log.info(f"STATUS: {r.status_code}")
        log.info(f"CONTENT-TYPE: {r.headers.get('Content-Type')}")
        # tampilkan sebagian response
        log.info(f"RESPONSE: {r.text[:1000]}")
        data = r.json()
        items = (
            data.get("data", {}).get("list", [])
            or data.get("data", [])
            or data.get("list", [])
            or []
        )
        log.info(f"   → {len(items)} artikel ditemukan")
        for item in items:
            title = item.get("title", "") or item.get("name", "")
            uid = str(item.get("id", "") or item.get("article_id", ""))
            link = (
                item.get("url", "")
                or f"{source['base_link']}{uid}"
            )
            if not uid or not is_relevant(title):
                continue
            uid_key = f"gate_{uid}"
            if is_seen(uid_key):
                continue
            mark_seen(uid_key)
            send_telegram(
                format_message(
                    source["logo"],
                    source["name"],
                    title,
                    link
                )
            )
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error API Gate.io: {e}")


def fetch_bybit_scrape(source):
    log.info("🕷️  Scrape: Bybit")
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/en/detail/" not in href:
                continue
            # Ambil judul dari elemen terdalam yang punya teks
            title_tag = a.find(["h3", "h4", "p", "span", "div"])
            title = title_tag.get_text(strip=True) if title_tag else a.get_text(strip=True)
            # Potong jika ada teks metadata
            title = title.split("  ")[0].strip()
            if len(title) < 10 or not is_relevant(title):
                continue
            if href.startswith("/"):
                href = source["base_link"] + href
            if is_seen(href):
                continue
            mark_seen(href)
            send_telegram(format_message(source["logo"], source["name"], title, href))
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error scrape Bybit: {e}")


def fetch_kucoin_api(source):
    log.info("🔌 Cek API: KuCoin")
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
        log.error(f"❌ Error API KuCoin: {e}")


def fetch_scrape(source):
    log.info(f"🕷️  Scrape: {source['name']}")
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        seen_uids = set()
        for a in soup.find_all("a", href=True):
            href  = a["href"]
            title = a.get_text(separator=" ", strip=True)
            # Judul artikel wajar < 200 karakter
            if len(title) < 15 or len(title) > 200:
                continue
            if not is_relevant(title):
                continue
            if href.startswith("/"):
                base = "/".join(source["url"].split("/")[:3])
                href = base + href
            elif not href.startswith("http"):
                continue
            uid = href
            if uid in seen_uids or is_seen(uid):
                continue
            seen_uids.add(uid)
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
        if t == "binance_scrape":
            fetch_binance_scrape(source)
        elif t == "binance_api":
            fetch_binance_api(source)
        elif t == "gate_scrape":
            fetch_gate_api(source)
        elif t == "kucoin_api":
            fetch_kucoin_api(source)
        elif t == "bybit_scrape":
            fetch_bybit_scrape(source)
        elif t == "scrape":
            fetch_scrape(source)
    log.info("✅ Selesai pengecekan.")

# ─── TEST MESSAGE ──────────────────────────────────────────────────────────────
def send_test_message():
    msg = (
        "🤖 <b>Crypto CEX Alarm Bot Aktif!</b>\n\n"
        "✅ Bot berhasil terhubung ke channel ini.\n"
        "📡 Memantau: Binance, Bybit, OKX, KuCoin, Gate.io, MEXC, BingX, Poloniex, HTX\n"
        "⏱ Pengecekan setiap <b>2 menit</b> otomatis.\n\n"
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
    scheduler.add_job(check_all, "interval", minutes=CHECK_EVERY, max_instances=1, coalesce=True)
    scheduler.start()
