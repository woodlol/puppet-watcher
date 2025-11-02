import os
import re
import json
import subprocess
from datetime import datetime
from typing import List, Dict, Set, Optional

import requests
from bs4 import BeautifulSoup

AFISHA_URL = "https://puppet-minsk.com/bilety/afisha"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])

# для GitHub Actions кладём сюда
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
    }
    try:
        resp = requests.get(AFISHA_URL, headers=headers, timeout=20)
        resp.encoding = resp.apparent_encoding or resp.encoding
        return resp.text
    except Exception as e:
        log(f"❗ Не удалось скачать афишу: {e}")
        return None


def parse_afisha(html: str) -> List[Dict]:
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


def git_has_changes() -> bool:
    """
    Проверяем, есть ли изменения в рабочей директории
    """
    try:
        out = subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
        return bool(out)
    except Exception as e:
        log(f"⚠️ Не удалось проверить git status: {e}")
        return False


def git_commit_and_push(commit_msg: str):
    """
    Коммитим и пушим изменения (в первую очередь data/seen.json)
    """
    try:
        # настроим имя/почту для actions
        subprocess.run(["git", "config", "user.name", "github-actions"], check=True)
        subprocess.run(["git", "config", "user.email", "actions@github.com"], check=True)

        subprocess.run(["git", "add", SEEN_FILE], check=True)
        # Commit может быть пустым, поэтому allow-empty
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)
        subprocess.run(["git", "push"], check=True)
        log("✅ Изменения закоммичены и отправлены в репозиторий")
    except subprocess.CalledProcessError as e:
        log(f"❗ Не удалось закоммитить/запушить: {e}")
    except Exception as e:
        log(f"❗ git push error: {e}")


def main():
    log("🔎 Проверяю афишу…")
    html = fetch_afisha_html()
    if html is None:
        return

    items = parse_afisha(html)
    if not items:
        log("😴 В афише ничего не найдено (или разметка поменялась).")
        return

    log(f"🎭 Найдено записей: {len(items)}")

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

    # сохраняем
    save_seen(seen)

    # если есть изменения — коммитим
    if git_has_changes():
        git_commit_and_push("update seen.json from GitHub Actions")
    else:
        log("ℹ️ Изменений для коммита нет.")


if __name__ == "__main__":
    main()
