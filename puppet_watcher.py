import os
import re
import json
import time
from datetime import datetime
from typing import List, Dict, Set, Optional

import requests
from bs4 import BeautifulSoup

# ================================
# НАСТРОЙКИ
# ================================
AFISHA_URL = "https://puppet-minsk.com/bilety/afisha"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])

CHECK_EVERY_SECONDS = int(os.environ.get("CHECK_EVERY_SECONDS", "300"))
SEEN_FILE = os.environ.get("SEEN_FILE", "/data/seen.json")


# ================================
# ЛОГ
# ================================
def log(msg: str):
    now = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    print(f"{now} {msg}", flush=True)


# ================================
# SEEN
# ================================
def load_seen() -> Set[str]:
    if not os.path.exists(SEEN_FILE):
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(seen: Set[str]):
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False, indent=2)


# ================================
# TELEGRAM
# ================================
def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if not r.ok:
            log(f"❗ Ошибка отправки в Telegram: {r.text}")
    except Exception as e:
        log(f"❗ Telegram error: {e}")


# ================================
# FETCH
# ================================
def fetch_afisha_html() -> Optional[str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:129.0) Gecko/20100101 Firefox/129.0",
        "Accept-Language": "ru,en;q=0.9",
    }
    try:
        resp = requests.get(AFISHA_URL, headers=headers, timeout=20)
        resp.encoding = resp.apparent_encoding or resp.encoding
        return resp.text
    except Exception as e:
        log(f"❗ Не удалось скачать афишу: {e}")
        return None


# ================================
# PARSE
# ================================
def parse_afisha(html: str) -> List[Dict]:
    """
    Возвращает список спектаклей:
    {
      "id": "...",
      "date": "02.11.2025",
      "time": "19:00",
      "title": "Записки юного врача",
      "url": "https://tce.by/..."
    }
    """
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict] = []

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue

        a = tr.find("a", href=lambda h: h and "tce.by" in h)
        if not a:
            continue

        title = a.get_text(strip=True)
        url = a["href"]

        # первая ячейка: "11.11.2025 19:00"
        first_text = tds[0].get_text(" ", strip=True).replace("\xa0", " ").replace("\u2003", " ")
        m = re.match(r"(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})", first_text)
        if not m:
            continue

        date_str, time_str = m.group(1), m.group(2)
        item_id = f"{date_str} {time_str} | {title} | {url}"

        results.append(
            {
                "id": item_id,
                "date": date_str,
                "time": time_str,
                "title": title,
                "url": url,
            }
        )

    return results


# ================================
# ПРОВЕРКА НОВЫХ
# ================================
def check_once():
    log("🔎 Проверяю афишу…")
    html = fetch_afisha_html()
    if html is None:
        return

    items = parse_afisha(html)
    if not items:
        log("😴 В афише ничего не найдено (или сайт поменял разметку).")
        return

    log(f"🎭 Найдено {len(items)} записей в афише.")

    seen = load_seen()
    new_items = [x for x in items if x["id"] not in seen]

    if not new_items:
        log("😌 Новых записей нет.")
        return

    for x in new_items:
        msg = (
            f"<b>Новая запись в афише!</b>\n"
            f"{x['date']} {x['time']}\n"
            f"{x['title']}\n"
            f"{x['url']}"
        )
        send_telegram(msg)
        seen.add(x["id"])
        log(f"✨ Отправлено в Telegram: {x['title']} ({x['date']} {x['time']})")

    save_seen(seen)


# ================================
# ЦИКЛ
# ================================
def main_loop():
    while True:
        check_once()
        time.sleep(CHECK_EVERY_SECONDS)


if __name__ == "__main__":
    main_loop()
