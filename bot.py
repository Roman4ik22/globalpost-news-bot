"""
Бот для автоматической генерации и публикации новостей GlobalPost в Telegram.
Запуск: python bot.py
"""

import os
import re
import logging
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

from sources import SOURCES
from prompts import SELECTOR_PROMPT, ARTICLE_PROMPT

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]

USER_AGENT = "GlobalPostBot/1.0"
HEADERS = {"User-Agent": USER_AGENT}


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

            news.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "summary": entry.get("summary", "")[:500],
                "source": source["name"],
                "published": published.isoformat() if published else "",
            })
        logger.info(f"RSS {source['name']}: {len(news)} новостей")
        return news
    except Exception as e:
        logger.warning(f"Ошибка парсинга RSS {source['name']}: {e}")
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

            # Пропускаем навигационные и служебные ссылки
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

        # Убираем дубликаты по заголовку
        seen = set()
        unique = []
        for item in news[:15]:
            if item["title"] not in seen:
                seen.add(item["title"])
                unique.append(item)

        logger.info(f"WEB {source['name']}: {len(unique)} новостей")
        return unique
    except Exception as e:
        logger.warning(f"Ошибка парсинга WEB {source['name']}: {e}")
        return []


def collect_all_news() -> list[dict]:
    """Собрать новости со всех источников."""
    all_news = []
    for source in SOURCES:
        if source["type"] == "rss":
            all_news.extend(fetch_rss_news(source))
        elif source["type"] == "web":
            all_news.extend(fetch_web_news(source))
    logger.info(f"Всего собрано новостей: {len(all_news)}")
    return all_news


def fetch_article_text(url: str) -> str:
    """Скачать и извлечь текст статьи по URL."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Удаляем ненужные элементы
        for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        # Пытаемся найти основной контент
        article = soup.find("article") or soup.find("main") or soup.find("body")
        if article:
            text = article.get_text(separator="\n", strip=True)
        else:
            text = soup.get_text(separator="\n", strip=True)

        # Ограничиваем размер текста
        return text[:5000]
    except Exception as e:
        logger.warning(f"Ошибка загрузки статьи {url}: {e}")
        return ""


def select_best_news(news: list[dict]) -> dict | None:
    """Выбрать лучшую новость через Claude API."""
    if not news:
        logger.error("Нет новостей для выбора")
        return None

    # Формируем пронумерованный список заголовков
    news_list = "\n".join(
        f"{i+1}. [{item['source']}] {item['title']}"
        for i, item in enumerate(news)
    )

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=10,
        messages=[
            {"role": "system", "content": SELECTOR_PROMPT},
            {"role": "user", "content": f"Ось список новин:\n\n{news_list}"},
        ],
    )

    answer = response.choices[0].message.content.strip()
    # Извлекаем число из ответа
    match = re.search(r"\d+", answer)
    if match:
        idx = int(match.group()) - 1
        if 0 <= idx < len(news):
            selected = news[idx]
            logger.info(f"Выбрана новость: {selected['title']} ({selected['source']})")
            return selected

    logger.warning(f"Не удалось распарсить выбор Claude: '{answer}', берём первую новость")
    return news[0]


def generate_article(news: dict, article_text: str) -> str:
    """Сгенерировать статью через Claude API."""
    client = OpenAI(api_key=OPENAI_API_KEY)

    user_message = f"""Новина для адаптації:

Заголовок: {news['title']}
Джерело: {news['source']}
Посилання: {news['link']}

Повний текст статті:
{article_text}"""

    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=2000,
        messages=[
            {"role": "system", "content": ARTICLE_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )

    article = response.choices[0].message.content.strip()
    logger.info(f"Статья сгенерирована, длина: {len(article)} символов")
    return article


def send_to_telegram(text: str) -> bool:
    """Отправить сообщение в Telegram-канал."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # Telegram ограничивает сообщения 4096 символами
    if len(text) > 4096:
        text = text[:4090] + "..."

    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    resp = requests.post(url, json=payload, timeout=15)

    if resp.status_code == 200:
        logger.info("Пост успешно отправлен в Telegram")
        return True
    else:
        logger.error(f"Ошибка отправки в Telegram: {resp.status_code} — {resp.text}")
        return False


def main():
    logger.info("=== Запуск бота GlobalPost News ===")

    # 1. Собираем новости
    all_news = collect_all_news()
    if not all_news:
        logger.error("Не удалось собрать новости ни с одного источника")
        return

    # 2. Выбираем лучшую новость
    selected = select_best_news(all_news)
    if not selected:
        return

    # 3. Скачиваем полный текст
    article_text = fetch_article_text(selected["link"])
    if not article_text:
        # Используем summary если не удалось скачать
        article_text = selected.get("summary", selected["title"])

    # 4. Генерируем статью
    article = generate_article(selected, article_text)

    # 5. Публикуем в Telegram
    send_to_telegram(article)

    logger.info("=== Бот завершил работу ===")


if __name__ == "__main__":
    main()
