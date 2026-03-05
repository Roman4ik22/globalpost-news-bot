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

# Используем реалистичный User-Agent чтобы избежать блокировок 403
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
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

            # Пытаемся достать картинку из RSS
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


def fetch_article_text(url: str) -> tuple[str, str | None]:
    """Скачать и извлечь текст статьи и главное изображение по URL.
    Возвращает (текст, url_изображения или None)."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Ищем главное изображение
        image_url = None
        # 1. Open Graph meta tag (самый надёжный способ)
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            image_url = og_image["content"]
        else:
            # 2. Twitter card image
            tw_image = soup.find("meta", attrs={"name": "twitter:image"})
            if tw_image and tw_image.get("content"):
                image_url = tw_image["content"]
            else:
                # 3. Первое большое изображение в article
                article_tag = soup.find("article") or soup.find("main")
                if article_tag:
                    for img in article_tag.find_all("img", src=True):
                        src = img["src"]
                        # Пропускаем иконки и мелкие изображения
                        width = img.get("width", "")
                        if width and width.isdigit() and int(width) < 200:
                            continue
                        if not src.startswith("http"):
                            src = requests.compat.urljoin(url, src)
                        image_url = src
                        break

        if image_url:
            logger.info(f"Найдено изображение: {image_url[:100]}")

        # Удаляем ненужные элементы
        for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        # Пытаемся найти основной контент
        article = soup.find("article") or soup.find("main") or soup.find("body")
        if article:
            text = article.get_text(separator="\n", strip=True)
        else:
            text = soup.get_text(separator="\n", strip=True)

        return text[:5000], image_url
    except Exception as e:
        logger.warning(f"Ошибка загрузки статьи {url}: {e}")
        return "", None


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


def find_fallback_image(query: str) -> str | None:
    """Найти картинку через Unsplash (бесплатный API без ключа)."""
    try:
        # Unsplash Source — бесплатный редирект на фото по запросу
        keywords = "logistics shipping container cargo port"
        url = f"https://source.unsplash.com/1200x630/?{keywords}"
        resp = requests.head(url, headers=HEADERS, timeout=10, allow_redirects=True)
        if resp.status_code == 200:
            image_url = resp.url
            logger.info(f"Fallback изображение от Unsplash: {image_url[:100]}")
            return image_url
    except Exception as e:
        logger.warning(f"Не удалось получить fallback изображение: {e}")
    return None


def send_to_telegram(text: str, image_url: str | None = None) -> bool:
    """Отправить сообщение в Telegram-канал. Если есть фото — отправляет как фото с подписью."""

    if image_url:
        # Отправляем фото с подписью (caption ограничен 1024 символами)
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        if len(text) > 1024:
            # Если текст длиннее 1024 — отправляем фото + отдельное сообщение
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "photo": image_url,
            }
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                logger.info("Фото отправлено в Telegram")
            else:
                logger.warning(f"Не удалось отправить фото: {resp.status_code}")

            # Отправляем текст отдельным сообщением
            return _send_text(text)
        else:
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "photo": image_url,
                "caption": text,
                "parse_mode": "HTML",
            }
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                logger.info("Пост с фото отправлен в Telegram")
                return True
            else:
                logger.warning(f"Не удалось отправить фото: {resp.status_code}, отправляю без фото")
                return _send_text(text)
    else:
        return _send_text(text)


def _send_text(text: str) -> bool:
    """Отправить текстовое сообщение в Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

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
        logger.info("Пост отправлен в Telegram")
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

    # 3. Скачиваем полный текст и изображение
    article_text, image_url = fetch_article_text(selected["link"])
    if not article_text:
        article_text = selected.get("summary", selected["title"])

    # 4. Если нет картинки из статьи — берём из RSS
    if not image_url and selected.get("image"):
        image_url = selected["image"]
        logger.info(f"Используем картинку из RSS: {image_url[:100]}")

    # 5. Если всё ещё нет — ищем stock-фото
    if not image_url:
        image_url = find_fallback_image(selected["title"])

    # 6. Генерируем статью
    article = generate_article(selected, article_text)

    # 7. Публикуем в Telegram (с фото)
    send_to_telegram(article, image_url)

    logger.info("=== Бот завершил работу ===")


if __name__ == "__main__":
    main()
