"""
Бот для автоматической генерации и публикации новостей GlobalPost в Telegram.
Запуск: python bot.py
"""

import os
import re
import json
import random
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

from sources import SOURCES
from prompts import SELECTOR_PROMPT, get_article_prompt

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": USER_AGENT}

HISTORY_FILE = Path(__file__).parent / "history.json"

# Ротация форматов: пн-новость, вт-цифра, ср-аналитика, чт-новость, пт-цифра
FORMAT_SCHEDULE = {
    0: "news",      # Понедельник
    1: "stat",      # Вторник
    2: "analysis",  # Среда
    3: "news",      # Четверг
    4: "stat",      # Пятница
}


# === История публикаций (защита от дубликатов) ===

def load_history() -> list[str]:
    """Загрузить список ранее опубликованных URL."""
    if HISTORY_FILE.exists():
        try:
            data = json.loads(HISTORY_FILE.read_text())
            return data.get("published", [])
        except (json.JSONDecodeError, KeyError):
            return []
    return []


def save_history(published: list[str]):
    """Сохранить историю. Храним последние 90 записей."""
    published = published[-90:]
    HISTORY_FILE.write_text(json.dumps({"published": published}, indent=2))


# === Парсинг новостей ===

def fetch_rss_news(source: dict, since_hours: int = 48) -> list[dict]:
    """Получить новости из RSS-фида за последние N часов."""
    try:
        feed = feedparser.parse(source["url"], agent=USER_AGENT)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        news = []
        for entry in feed.entries[:20]:
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)

            if published and published < cutoff:
                continue

            # Картинка из RSS
            image = None
            if hasattr(entry, "media_content") and entry.media_content:
                for media in entry.media_content:
                    if media.get("medium") == "image" or media.get("type", "").startswith("image"):
                        image = media.get("url")
                        break
            if not image and hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
                image = entry.media_thumbnail[0].get("url")
            if not image and hasattr(entry, "enclosures") and entry.enclosures:
                for enc in entry.enclosures:
                    if enc.get("type", "").startswith("image"):
                        image = enc.get("href") or enc.get("url")
                        break

            news.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "summary": entry.get("summary", "")[:500],
                "source": source["name"],
                "published": published.isoformat() if published else "",
                "image": image,
            })
        logger.info(f"RSS {source['name']}: {len(news)} новостей")
        return news
    except Exception as e:
        logger.warning(f"Ошибка RSS {source['name']}: {e}")
        return []


def fetch_web_news(source: dict) -> list[dict]:
    """Получить заголовки новостей с веб-страницы."""
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        news = []
        for link in soup.find_all("a", href=True):
            text = link.get_text(strip=True)
            if len(text) < 20 or len(text) > 300:
                continue
            href = link["href"]
            if not href.startswith("http"):
                href = requests.compat.urljoin(source["url"], href)

            skip_patterns = ["login", "signup", "contact", "about", "privacy", "cookie", "javascript:"]
            if any(p in href.lower() for p in skip_patterns):
                continue

            news.append({
                "title": text,
                "link": href,
                "summary": "",
                "source": source["name"],
                "published": "",
            })

        seen = set()
        unique = []
        for item in news[:15]:
            if item["title"] not in seen:
                seen.add(item["title"])
                unique.append(item)

        logger.info(f"WEB {source['name']}: {len(unique)} новостей")
        return unique
    except Exception as e:
        logger.warning(f"Ошибка WEB {source['name']}: {e}")
        return []


def collect_all_news() -> list[dict]:
    """Собрать новости со всех источников."""
    all_news = []
    for source in SOURCES:
        if source["type"] == "rss":
            all_news.extend(fetch_rss_news(source))
        elif source["type"] == "web":
            all_news.extend(fetch_web_news(source))
    logger.info(f"Всего собрано: {len(all_news)} новостей")
    return all_news


# === Работа со статьями ===

def fetch_article_text(url: str) -> tuple[str, str | None]:
    """Скачать текст статьи и главное изображение."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Ищем изображение
        image_url = None
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            image_url = og_image["content"]
        else:
            tw_image = soup.find("meta", attrs={"name": "twitter:image"})
            if tw_image and tw_image.get("content"):
                image_url = tw_image["content"]
            else:
                article_tag = soup.find("article") or soup.find("main")
                if article_tag:
                    for img in article_tag.find_all("img", src=True):
                        src = img["src"]
                        width = img.get("width", "")
                        if width and width.isdigit() and int(width) < 200:
                            continue
                        if not src.startswith("http"):
                            src = requests.compat.urljoin(url, src)
                        image_url = src
                        break

        if image_url:
            logger.info(f"Изображение: {image_url[:80]}")

        for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        article = soup.find("article") or soup.find("main") or soup.find("body")
        text = article.get_text(separator="\n", strip=True) if article else soup.get_text(separator="\n", strip=True)

        return text[:5000], image_url
    except Exception as e:
        logger.warning(f"Ошибка загрузки {url}: {e}")
        return "", None


# === AI ===

def select_best_news(news: list[dict]) -> dict | None:
    """Выбрать лучшую новость через GPT-4o-mini (дёшево)."""
    if not news:
        return None

    news_list = "\n".join(
        f"{i+1}. [{item['source']}] {item['title']}"
        for i, item in enumerate(news)
    )

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=10,
        messages=[
            {"role": "system", "content": SELECTOR_PROMPT},
            {"role": "user", "content": f"Ось список новин:\n\n{news_list}"},
        ],
    )

    answer = response.choices[0].message.content.strip()
    match = re.search(r"\d+", answer)
    if match:
        idx = int(match.group()) - 1
        if 0 <= idx < len(news):
            logger.info(f"Выбрана: {news[idx]['title']} ({news[idx]['source']})")
            return news[idx]

    logger.warning(f"Не удалось распарсить выбор: '{answer}', берём первую")
    return news[0]


def generate_article(news: dict, article_text: str, format_type: str) -> str:
    """Сгенерировать пост через GPT-4o."""
    client = OpenAI(api_key=OPENAI_API_KEY)

    system_prompt = get_article_prompt(format_type, news["link"])

    user_message = f"""Новина для адаптації:

Заголовок: {news['title']}
Джерело: {news['source']}
Посилання: {news['link']}

Текст:
{article_text}"""

    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=2000,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )

    article = response.choices[0].message.content.strip()
    logger.info(f"Сгенерировано ({format_type}): {len(article)} символов")
    return article


# === Telegram ===

FALLBACK_IMAGES = [
    "https://images.pexels.com/photos/1427107/pexels-photo-1427107.jpeg?auto=compress&cs=tinysrgb&w=1260",
    "https://images.pexels.com/photos/2226458/pexels-photo-2226458.jpeg?auto=compress&cs=tinysrgb&w=1260",
    "https://images.pexels.com/photos/1117210/pexels-photo-1117210.jpeg?auto=compress&cs=tinysrgb&w=1260",
    "https://images.pexels.com/photos/3846128/pexels-photo-3846128.jpeg?auto=compress&cs=tinysrgb&w=1260",
    "https://images.pexels.com/photos/906494/pexels-photo-906494.jpeg?auto=compress&cs=tinysrgb&w=1260",
    "https://images.pexels.com/photos/1427541/pexels-photo-1427541.jpeg?auto=compress&cs=tinysrgb&w=1260",
    "https://images.pexels.com/photos/2547565/pexels-photo-2547565.jpeg?auto=compress&cs=tinysrgb&w=1260",
    "https://images.pexels.com/photos/1267338/pexels-photo-1267338.jpeg?auto=compress&cs=tinysrgb&w=1260",
]


def send_to_telegram(text: str, image_url: str | None = None) -> bool:
    """Отправить пост в Telegram. Фото + текст = один пост."""
    if len(text) > 1024:
        text = text[:1020] + "..."
        logger.warning("Текст обрезан до 1024 символов")

    if image_url:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        payload = {
            "chat_id": TELEGRAM_CHANNEL_ID,
            "photo": image_url,
            "caption": text,
            "parse_mode": "HTML",
        }
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            logger.info("Пост с фото отправлен")
            return True
        else:
            logger.warning(f"Фото не отправлено ({resp.status_code}), пробуем без фото")

    # Fallback: без фото
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    resp = requests.post(url, json=payload, timeout=15)
    if resp.status_code == 200:
        logger.info("Пост отправлен (без фото)")
        return True
    logger.error(f"Ошибка Telegram: {resp.status_code} — {resp.text}")
    return False


# === Main ===

def main():
    logger.info("=== Запуск бота GlobalPost News ===")

    # Пропускаем выходные (сб=5, вс=6)
    today = datetime.now(timezone.utc)
    weekday = today.weekday()
    if weekday >= 5:
        logger.info(f"Выходной (день {weekday}), пропускаем")
        return

    # Определяем формат поста на сегодня
    format_type = FORMAT_SCHEDULE.get(weekday, "news")
    logger.info(f"Формат на сегодня: {format_type}")

    # Загружаем историю
    history = load_history()

    # 1. Собираем новости
    all_news = collect_all_news()
    if not all_news:
        logger.error("Нет новостей")
        return

    # Фильтруем уже опубликованные
    fresh_news = [n for n in all_news if n["link"] not in history]
    logger.info(f"После фильтра дубликатов: {len(fresh_news)} из {len(all_news)}")

    if not fresh_news:
        logger.warning("Все новости уже были опубликованы")
        fresh_news = all_news  # fallback

    # Приоритизируем RSS
    rss_news = [n for n in fresh_news if n.get("summary")]
    news_pool = rss_news if rss_news else fresh_news

    # Пробуем до 3 раз
    tried_links = set()
    for attempt in range(3):
        available = [n for n in news_pool if n["link"] not in tried_links]
        if not available:
            available = [n for n in fresh_news if n["link"] not in tried_links]
        if not available:
            logger.error("Закончились новости")
            return

        selected = select_best_news(available)
        if not selected:
            return

        tried_links.add(selected["link"])

        # Скачиваем текст и картинку
        article_text, image_url = fetch_article_text(selected["link"])
        if not article_text:
            article_text = selected.get("summary", selected["title"])

        if len(article_text) < 50:
            logger.warning(f"Мало контента ({len(article_text)}), пробуем другую")
            continue

        # Картинка: статья → RSS → fallback
        if not image_url and selected.get("image"):
            image_url = selected["image"]
        if not image_url:
            image_url = random.choice(FALLBACK_IMAGES)

        # Генерируем
        article = generate_article(selected, article_text, format_type)

        # Проверяем отказ GPT
        refusal_markers = ["на жаль", "не можу", "не вдалося", "не маю змоги", "надайте", "якщо ви наведете"]
        if any(m in article.lower() for m in refusal_markers):
            logger.warning(f"GPT отказался, попытка {attempt + 1}/3")
            continue

        # Публикуем
        if send_to_telegram(article, image_url):
            # Сохраняем в историю
            history.append(selected["link"])
            save_history(history)
            logger.info(f"Сохранено в историю: {selected['link']}")

        logger.info("=== Бот завершил работу ===")
        return

    logger.error("Не удалось сгенерировать за 3 попытки")


if __name__ == "__main__":
    main()
