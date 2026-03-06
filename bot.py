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

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": USER_AGENT}

HISTORY_FILE = Path(__file__).parent / "history.json"
SETTINGS_FILE = Path(__file__).parent / "settings.json"

FORMAT_SCHEDULE = {
    0: "news", 1: "stat", 2: "analysis",
    3: "news", 4: "stat", 5: "news", 6: "analysis",
}

# Паттерны для фильтрации плохих изображений
BAD_IMAGE_PATTERNS = [
    "logo", "icon", "avatar", "brand", "favicon", "badge", "banner-ad",
    "placeholder", "default", "pixel", "tracking", "1x1", "blank",
    "PinExt", "share", "social", "button", "sprite",
]

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


# === Settings & History ===

def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return {"post_on_weekends": True, "format_override": None, "paused": False}


def load_history() -> list[str]:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text()).get("published", [])
        except (json.JSONDecodeError, KeyError):
            pass
    return []


def save_history(published: list[str]):
    published = published[-90:]
    HISTORY_FILE.write_text(json.dumps({"published": published}, indent=2))


# === Утилиты ===

def clean_html(text: str) -> str:
    """Убрать HTML-теги из текста (для очистки RSS summary)."""
    return BeautifulSoup(text, "html.parser").get_text(separator=" ", strip=True)


def is_good_image(url: str) -> bool:
    """Проверить что URL — это реальное фото, а не логотип/иконка."""
    if not url:
        return False
    url_lower = url.lower()
    return not any(p in url_lower for p in BAD_IMAGE_PATTERNS)


# Ключевые слова для фильтрации релевантных новостей (логистика + укр. бизнес)
LOGISTICS_KEYWORDS = [
    # Логистика и перевозки
    "shipping", "freight", "cargo", "logistics", "container", "port", "vessel",
    "maritime", "ocean", "sea", "air cargo", "airfreight", "trucking", "rail",
    "supply chain", "warehouse", "distribution", "delivery", "transport",
    "forwarding", "carrier", "fleet", "bulk", "tanker",
    # Торговля и таможня
    "tariff", "customs", "trade", "import", "export", "duty", "sanction",
    "embargo", "quota", "regulation", "compliance", "border",
    # Маршруты и регионы
    "china", "asia", "europe", "ukraine", "turkey", "suez", "panama",
    "mediterranean", "black sea", "baltic", "red sea", "silk road",
    # Ставки и финансы
    "rate", "cost", "price", "fee", "surcharge", "index", "teu", "feu",
    "spot", "contract", "market",
    # Инфраструктура
    "terminal", "dock", "berth", "canal", "strait", "route", "corridor",
    "pipeline", "hub",
]


def is_relevant_news(news_item: dict) -> bool:
    """Проверить что новость связана с логистикой/торговлей."""
    text = (news_item.get("title", "") + " " + news_item.get("summary", "")).lower()
    return any(kw in text for kw in LOGISTICS_KEYWORDS)


# === Парсинг ===

def fetch_rss_news(source: dict, since_hours: int = 72) -> list[dict]:
    """Получить новости из RSS за последние N часов."""
    try:
        feed = feedparser.parse(source["url"], agent=USER_AGENT)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        news = []
        for entry in feed.entries[:25]:
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

            # Очищаем summary от HTML
            summary = clean_html(entry.get("summary", ""))[:500]

            news.append({
                "title": entry.get("title", "").strip(),
                "link": entry.get("link", ""),
                "summary": summary,
                "source": source["name"],
                "published": published.isoformat() if published else "",
                "image": image if is_good_image(image) else None,
            })
        logger.info(f"RSS {source['name']}: {len(news)}")
        return news
    except Exception as e:
        logger.warning(f"RSS {source['name']}: {e}")
        return []


def collect_all_news() -> list[dict]:
    """Собрать новости со всех RSS-источников."""
    all_news = []
    for source in SOURCES:
        if source["type"] == "rss":
            all_news.extend(fetch_rss_news(source))
    logger.info(f"Всего: {len(all_news)} новостей")
    return all_news


# === Работа со статьями ===

def fetch_article_content(url: str) -> tuple[str, str | None]:
    """Скачать текст и изображение статьи."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Ищем изображение (приоритет: og:image → twitter:image → первое в article)
        image_url = None
        for meta_prop in ["og:image", "og:image:url"]:
            tag = soup.find("meta", property=meta_prop)
            if tag and tag.get("content") and is_good_image(tag["content"]):
                image_url = tag["content"]
                break

        if not image_url:
            tw = soup.find("meta", attrs={"name": "twitter:image"})
            if tw and tw.get("content") and is_good_image(tw["content"]):
                image_url = tw["content"]

        if not image_url:
            article_tag = soup.find("article") or soup.find("main")
            if article_tag:
                for img in article_tag.find_all("img", src=True):
                    src = img["src"]
                    if not src.startswith("http"):
                        src = requests.compat.urljoin(url, src)
                    width = img.get("width", "")
                    if width and width.isdigit() and int(width) < 200:
                        continue
                    if is_good_image(src):
                        image_url = src
                        break

        # Извлекаем текст
        for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()

        article = soup.find("article") or soup.find("main") or soup.find("body")
        text = article.get_text(separator="\n", strip=True) if article else ""

        return text[:5000], image_url
    except Exception as e:
        logger.warning(f"Загрузка {url}: {e}")
        return "", None


# === AI ===

def select_best_news(news: list[dict]) -> dict | None:
    """Выбрать лучшую новость через GPT-4o-mini (дёшево)."""
    if not news:
        return None

    # Передаём заголовок + описание для лучшего выбора
    news_list = "\n".join(
        f"{i+1}. [{item['source']}] {item['title']}"
        + (f" — {item['summary'][:200]}" if item.get("summary") else "")
        for i, item in enumerate(news)
    )

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=10,
        messages=[
            {"role": "system", "content": SELECTOR_PROMPT},
            {"role": "user", "content": f"Список новин:\n\n{news_list}"},
        ],
    )

    answer = response.choices[0].message.content.strip()
    match = re.search(r"\d+", answer)
    if match:
        idx = int(match.group()) - 1
        if 0 <= idx < len(news):
            logger.info(f"Выбрана: {news[idx]['title'][:60]}... ({news[idx]['source']})")
            return news[idx]

    return news[0]


def generate_article(news: dict, article_text: str, format_type: str) -> str:
    """Сгенерировать пост через GPT-4o."""
    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = get_article_prompt(format_type, news["link"])

    user_msg = f"""Новина:
Заголовок: {news['title']}
Джерело: {news['source']}
URL: {news['link']}

Текст статті:
{article_text}"""

    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=1500,
        temperature=0.7,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_msg},
        ],
    )

    article = response.choices[0].message.content.strip()
    logger.info(f"Сгенерировано ({format_type}): {len(article)} символов")
    return article


def validate_post(text: str) -> bool:
    """Проверить качество поста перед публикацией."""
    if len(text) < 100:
        logger.warning(f"Пост слишком короткий: {len(text)} символов")
        return False

    # Проверяем что GPT не отказался
    refusals = ["на жаль", "не можу", "не вдалося", "не маю змоги", "надайте", "якщо ви наведете", "sorry", "i can't"]
    if any(r in text.lower() for r in refusals):
        logger.warning("GPT отказался генерировать")
        return False

    # Проверяем наличие обязательных элементов
    if "globalpost.ua" not in text:
        logger.warning("Нет ссылки на GlobalPost")

    if "#" not in text:
        logger.warning("Нет хештегов")

    return True


def check_image_url(url: str) -> bool:
    """Проверить что изображение доступно (HEAD запрос)."""
    try:
        resp = requests.head(url, headers=HEADERS, timeout=5, allow_redirects=True)
        content_type = resp.headers.get("content-type", "")
        return resp.status_code == 200 and "image" in content_type
    except Exception:
        return False


def trim_post_to_limit(text: str, limit: int = 1024) -> str:
    """Обрезать пост до лимита, не ломая HTML-теги и хештеги."""
    if len(text) <= limit:
        return text

    # Находим хештеги в конце
    lines = text.rstrip().split("\n")
    hashtag_line = ""
    if lines and lines[-1].strip().startswith("#"):
        hashtag_line = "\n" + lines[-1].strip()
        text_body = "\n".join(lines[:-1]).rstrip()
    else:
        text_body = text

    available = limit - len(hashtag_line) - 4  # "...\n"
    if available < 100:
        return text[:limit]

    trimmed = text_body[:available]

    # Не обрезаем посреди HTML-тега
    open_tag = trimmed.rfind("<")
    close_tag = trimmed.rfind(">")
    if open_tag > close_tag:
        trimmed = trimmed[:open_tag]

    # Закрываем незакрытые теги
    open_tags = re.findall(r"<(b|i|a)[^>]*>", trimmed)
    close_tags = re.findall(r"</(b|i|a)>", trimmed)
    for tag in reversed(open_tags[len(close_tags):]):
        tag_name = tag.split()[0] if " " in tag else tag
        trimmed += f"</{tag_name}>"

    return trimmed.rstrip() + "..." + hashtag_line


# === Telegram ===

def send_to_telegram(text: str, image_url: str | None = None) -> bool:
    """Фото + текст = один пост."""
    text = trim_post_to_limit(text, 1024)

    if image_url:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
            json={
                "chat_id": TELEGRAM_CHANNEL_ID,
                "photo": image_url,
                "caption": text,
                "parse_mode": "HTML",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            logger.info("Пост с фото отправлен")
            return True
        logger.warning(f"Фото не отправлено ({resp.status_code}), пробуем без")

    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHANNEL_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=15,
    )
    if resp.status_code == 200:
        logger.info("Пост отправлен (без фото)")
        return True
    logger.error(f"Telegram: {resp.status_code} — {resp.text}")
    return False


# === Подготовка поста (без публикации) ===

def prepare_post(exclude_links: list[str] | None = None) -> dict | None:
    """Подготовить пост: парсинг → выбор → генерация. Возвращает dict с результатом."""
    settings = load_settings()
    today = datetime.now(timezone.utc)
    weekday = today.weekday()
    format_type = settings.get("format_override") or FORMAT_SCHEDULE.get(weekday, "news")

    history = load_history()
    all_news = collect_all_news()
    if not all_news:
        return None

    already_used = set(history + (exclude_links or []))
    fresh = [n for n in all_news if n["link"] not in already_used]
    if not fresh:
        fresh = all_news

    # Фильтруем только релевантные новости (логистика, торговля, укр. бизнес)
    relevant = [n for n in fresh if is_relevant_news(n)]
    logger.info(f"Релевантных: {len(relevant)} из {len(fresh)}")
    if not relevant:
        relevant = fresh  # fallback если фильтр слишком строгий

    with_numbers = [n for n in relevant if n.get("summary") and re.search(r'\d', n["title"])]
    with_summary = [n for n in relevant if n.get("summary")]
    pool = with_numbers or with_summary or relevant

    for attempt in range(3):
        selected = select_best_news(pool)
        if not selected:
            return None
        pool = [n for n in pool if n["link"] != selected["link"]]

        article_text, image_url = fetch_article_content(selected["link"])
        if not article_text:
            article_text = selected.get("summary") or selected["title"]
        if len(article_text) < 30:
            continue

        if not image_url and selected.get("image"):
            image_url = selected["image"]
        if image_url and not check_image_url(image_url):
            image_url = None
        if not image_url:
            image_url = random.choice(FALLBACK_IMAGES)

        article = generate_article(selected, article_text, format_type)
        if not validate_post(article):
            continue

        return {
            "text": article,
            "image_url": image_url,
            "news": selected,
            "article_text": article_text,
            "format_type": format_type,
        }

    return None


def edit_post(current_text: str, instruction: str, news: dict, article_text: str) -> str:
    """Отредактировать пост по инструкции пользователя через GPT."""
    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=1500,
        temperature=0.7,
        messages=[
            {"role": "system", "content": (
                "Ти — копірайтер Telegram-каналу GlobalPost.ua. "
                "Тобі надано поточний пост і інструкцію від редактора. "
                "Виправ пост згідно з інструкцією. "
                "Зберігай формат, HTML-теги (<b>, <a href>, <i>), хештеги та посилання. "
                "МАКСИМУМ 900 символів. МОВА: українська. "
                "Виведи ТІЛЬКИ виправлений текст посту."
            )},
            {"role": "user", "content": (
                f"Поточний пост:\n\n{current_text}\n\n"
                f"Оригінальна стаття:\n{article_text[:2000]}\n\n"
                f"Інструкція редактора: {instruction}"
            )},
        ],
    )
    return response.choices[0].message.content.strip()


def publish_post(text: str, image_url: str | None, news_link: str) -> bool:
    """Опубликовать пост и сохранить в историю."""
    if send_to_telegram(text, image_url):
        history = load_history()
        history.append(news_link)
        save_history(history)
        return True
    return False


# === Main (автопубликация для GitHub Actions) ===

def main():
    logger.info("=== GlobalPost News Bot ===")

    if not OPENAI_API_KEY or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.error("Missing env vars: OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID")
        return

    settings = load_settings()
    force = os.environ.get("FORCE_POST") == "1"

    if settings.get("paused") and not force:
        logger.info("Бот на паузе")
        return

    today = datetime.now(timezone.utc)
    weekday = today.weekday()
    if weekday >= 5 and not settings.get("post_on_weekends", True) and not force:
        logger.info("Выходной, пропускаем")
        return

    # Проверяем режим модерации
    if settings.get("moderation") and not force:
        logger.info("Режим модерации — пост ожидает ручного утверждения через админ-бот")
        return

    result = prepare_post()
    if not result:
        logger.error("Не удалось подготовить пост")
        return

    if publish_post(result["text"], result["image_url"], result["news"]["link"]):
        logger.info("=== Готово ===")
    else:
        logger.error("Не удалось опубликовать")


if __name__ == "__main__":
    main()
