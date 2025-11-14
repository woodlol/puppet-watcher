import os
import re
import json
import subprocess
from urllib.parse import urljoin
from datetime import datetime
from typing import List, Dict, Set, Optional, Tuple

import requests
from bs4 import BeautifulSoup, Tag

# новый дефолт — под текущую афишу
AFISHA_URL = os.environ.get("AFISHA_URL", "https://puppet-minsk.by/afisha")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
SEEN_FILE = os.environ.get("SEEN_FILE", "data/seen.json")

def log(msg: str):
    now = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    print(f"{now} {msg}", flush=True)

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

def fetch_afisha_html() -> Optional[str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:129.0) Gecko/20100101 Firefox/129.0",
        "Accept-Language": "ru,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    try:
        resp = requests.get(AFISHA_URL, headers=headers, timeout=25)
        resp.encoding = resp.apparent_encoding or resp.encoding or "utf-8"
        return resp.text
    except Exception as e:
        log(f"❗ Не удалось скачать афишу: {e}")
        return None

MONTHS_RU = {
    # поддержим и падежи: Ноябрь/Ноября, Декабрь/Декабря и т. п.
    "января": "01", "январь": "01",
    "февраля": "02", "февраль": "02",
    "марта": "03", "март": "03",
    "апреля": "04", "апрель": "04",
    "мая": "05", "май": "05",
    "июня": "06", "июнь": "06",
    "июля": "07", "июль": "07",
    "августа": "08", "август": "08",
    "сентября": "09", "сентябрь": "09",
    "октября": "10", "октябрь": "10",
    "ноября": "11", "ноябрь": "11",
    "декабря": "12", "декабрь": "12",
}

def _norm_space(s: str) -> str:
    if not s:
        return s
    s = s.replace("\xa0", " ").replace("\u2002", " ").replace("\u2003", " ").replace("\u2009", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _extract_year_month_from_container(node: Tag) -> Optional[Tuple[str, str]]:
    """
    Ищем ближайшего родителя с классом вида item_mounth-YYYY-MM.
    """
    cur: Optional[Tag] = node
    while isinstance(cur, Tag):
        classes = " ".join(cur.get("class", []))
        m = re.search(r"item_mounth-(\d{4})-(\d{2})", classes)
        if m:
            return m.group(1), m.group(2)
        cur = cur.parent if isinstance(cur.parent, Tag) else None
    return None

def _extract_day_and_month_from_text(text: str) -> Optional[Tuple[str, Optional[str]]]:
    """
    Из строки вида '15 Ноября, Сб' берём день '15' и, если есть, слово месяца 'Ноября'.
    """
    t = _norm_space(text).lower()
    # день: первое число
    m_day = re.search(r"\b(\d{1,2})\b", t)
    day = m_day.group(1) if m_day else None
    # месяц: слово из словаря (в падеже тоже ок)
    month_word = None
    for w in MONTHS_RU.keys():
        if w in t:
            month_word = w
            break
    return (day, month_word) if day else None

def parse_afisha(html: str) -> List[Dict]:
    """
    Новая разметка (карточки):
    .afisha_listcontainer.item_mounth-YYYY-MM > .afisha_item
      └─ .afisha-info
          ├─ p.afisha-day     → '15 Ноября, Сб'
          ├─ p.afisha-time    → '11:00'
          └─ p.afisha-title   → 'Название'
      └─ a.afisha_item-hover → относительная ссылка (…#tickets)
    """
    soup = BeautifulSoup(html, "html.parser")

    results: List[Dict] = []
    for item in soup.select(".afisha_listcontainer .afisha_item"):
        info = item.select_one(".afisha-info")
        if not info:
            continue

        p_day = info.select_one(".afisha-day")
        p_time = info.select_one(".afisha-time")
        p_title = info.select_one(".afisha-title")
        a_link = item.select_one("a.afisha_item-hover[href]")

        if not (p_day and p_time and p_title and a_link):
            continue

        day_text = p_day.get_text(" ", strip=True)
        time_text = _norm_space(p_time.get_text(" ", strip=True))
        title = _norm_space(p_title.get_text(" ", strip=True))
        href = a_link.get("href", "").strip()

        # год-месяц пытаемся взять из контейнера item_mounth-YYYY-MM
        ym = _extract_year_month_from_container(item)
        year, month_num = (None, None)
        if ym:
            year, month_num = ym

        # день и (возможно) слово месяца из текста "15 Ноября, Сб"
        day_month = _extract_day_and_month_from_text(day_text)
        if not day_month:
            continue
        day_num, month_word = day_month

        # если month_num не нашли в контейнере — пытаемся из названия месяца
        if not month_num and month_word:
            month_num = MONTHS_RU.get(month_word.lower())

        # если год не нашли — берём текущий (на всякий)
        if not year:
            year = str(datetime.now().year)

        # нормализуем день до 2 цифр
        if day_num and len(day_num) == 1:
            day_num = "0" + day_num

        # финальная дата
        if not (year and month_num and day_num and time_text):
            continue
        date_str = f"{day_num}.{month_num}.{year}"

        # абсолютная ссылка
        url_abs = urljoin(AFISHA_URL if AFISHA_URL.endswith("/") else AFISHA_URL + "/", href)

        item_id = f"{date_str} {time_text} | {title} | {url_abs}"
        results.append(
            {
                "id": item_id,
                "date": date_str,
                "time": time_text,
                "title": title,
                "url": url_abs,
            }
        )

    return results