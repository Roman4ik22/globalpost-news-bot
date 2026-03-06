"""
Админ-бот GlobalPost с ручной модерацией постов.
Запуск: python admin_bot.py

Команды:
  /start      — показать все команды
  /post       — сгенерировать пост на модерацию
  /format     — изменить формат (news / stat / analysis)
  /status     — текущие настройки
  /history    — последние 5 публикаций
  /pause      — приостановить / возобновить авто-публикацию
  /weekend    — вкл/выкл публикации на выходных
  /moderation — вкл/выкл ручную модерацию

Модерация:
  После /post бот присылает превью поста.
  Кнопки: ✅ Опублікувати | 🔄 Інша новина | 🔁 Перегенерувати
  Или напишите текстом что исправить — бот перепишет пост.
"""

import os
import sys
import json
import time
import logging
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")
SETTINGS_FILE = Path(__file__).parent / "settings.json"
HISTORY_FILE = Path(__file__).parent / "history.json"

API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Хранилище ожидающих модерации постов {chat_id: {...}}
pending_posts = {}


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return {"post_on_weekends": True, "format_override": None, "paused": False,
            "admin_ids": [], "moderation": True}


def save_settings(settings: dict):
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False))


def load_history() -> list[str]:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text()).get("published", [])
        except (json.JSONDecodeError, KeyError):
            return []
    return []


def send_message(chat_id: int, text: str, reply_markup: dict | None = None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(f"{API_BASE}/sendMessage", json=payload, timeout=10)


def send_photo(chat_id: int, photo_url: str, caption: str, reply_markup: dict | None = None):
    payload = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    resp = requests.post(f"{API_BASE}/sendPhoto", json=payload, timeout=15)
    if resp.status_code != 200:
        send_message(chat_id, caption, reply_markup)


def answer_callback(callback_query_id: str, text: str = ""):
    requests.post(f"{API_BASE}/answerCallbackQuery", json={
        "callback_query_id": callback_query_id,
        "text": text,
    }, timeout=5)


def is_admin(user_id: int, settings: dict) -> bool:
    if not settings.get("admin_ids"):
        settings["admin_ids"] = [user_id]
        save_settings(settings)
        logger.info(f"Первый админ: {user_id}")
        return True
    return user_id in settings["admin_ids"]


def moderation_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Опублікувати", "callback_data": "approve"},
                {"text": "🔁 Перегенерувати", "callback_data": "regenerate"},
            ],
            [
                {"text": "🔄 Інша новина", "callback_data": "different"},
                {"text": "❌ Скасувати", "callback_data": "cancel"},
            ],
        ]
    }


def send_preview(chat_id: int, post: dict):
    """Отправить превью поста админу с кнопками модерации."""
    text = post["text"]
    image_url = post.get("image_url")
    news = post.get("news", {})

    header = (
        f"📝 <b>ПРЕВЬЮ ПОСТА</b>\n"
        f"Джерело: {news.get('source', '?')} | Формат: {post.get('format_type', '?')}\n"
        f"Символів: {len(text)}\n"
        f"{'—' * 30}\n\n"
    )

    preview_text = header + text
    hint = "\n\n—\n💡 <i>Напишіть текстом що змінити, або натисніть кнопку</i>"

    if len(preview_text + hint) <= 1024 and image_url:
        send_photo(chat_id, image_url, preview_text + hint, moderation_keyboard())
    else:
        if image_url:
            send_photo(chat_id, image_url, "🖼 Зображення для поста")
        send_message(chat_id, preview_text + hint, moderation_keyboard())


def handle_post_command(chat_id: int):
    """Сгенерировать пост и отправить на модерацию."""
    send_message(chat_id, "⏳ Збираю новини та генерую пост...")

    try:
        from bot import prepare_post
        excluded = [p["news"]["link"] for p in pending_posts.values()]
        result = prepare_post(exclude_links=excluded)

        if not result:
            send_message(chat_id, "❌ Не вдалося згенерувати пост. Спробуйте пізніше.")
            return

        pending_posts[chat_id] = result
        send_preview(chat_id, result)

    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        send_message(chat_id, f"❌ Помилка: {e}")


def handle_approve(chat_id: int):
    """Опубликовать утверждённый пост."""
    post = pending_posts.get(chat_id)
    if not post:
        send_message(chat_id, "Немає поста для публікації. Натисніть /post")
        return

    try:
        from bot import publish_post
        if publish_post(post["text"], post.get("image_url"), post["news"]["link"]):
            send_message(chat_id, "✅ Пост опубліковано в канал!")
        else:
            send_message(chat_id, "❌ Не вдалося опублікувати. Перевірте налаштування каналу.")
    except Exception as e:
        send_message(chat_id, f"❌ Помилка: {e}")
    finally:
        pending_posts.pop(chat_id, None)


def handle_regenerate(chat_id: int):
    """Перегенерировать пост из того же источника."""
    post = pending_posts.get(chat_id)
    if not post:
        send_message(chat_id, "Немає поста. Натисніть /post")
        return

    send_message(chat_id, "🔁 Перегенерую пост...")

    try:
        from bot import generate_article, validate_post
        new_text = generate_article(post["news"], post["article_text"], post["format_type"])
        if validate_post(new_text):
            post["text"] = new_text
            send_preview(chat_id, post)
        else:
            send_message(chat_id, "❌ Згенерований пост не пройшов валідацію. Спробуйте ще.")
    except Exception as e:
        send_message(chat_id, f"❌ Помилка: {e}")


def handle_different(chat_id: int):
    """Выбрать другую новость."""
    post = pending_posts.get(chat_id)
    excluded = [post["news"]["link"]] if post else []
    pending_posts.pop(chat_id, None)

    send_message(chat_id, "🔄 Шукаю іншу новину...")

    try:
        from bot import prepare_post
        all_excluded = excluded + [p["news"]["link"] for p in pending_posts.values()]
        result = prepare_post(exclude_links=all_excluded)

        if not result:
            send_message(chat_id, "❌ Не знайшов інших підходящих новин.")
            return

        pending_posts[chat_id] = result
        send_preview(chat_id, result)

    except Exception as e:
        send_message(chat_id, f"❌ Помилка: {e}")


def handle_edit_instruction(chat_id: int, instruction: str):
    """Отредактировать пост по текстовой инструкции."""
    post = pending_posts.get(chat_id)
    if not post:
        send_message(chat_id, "Немає поста для редагування. Натисніть /post")
        return

    send_message(chat_id, "✏️ Вношу зміни...")

    try:
        from bot import edit_post, validate_post
        new_text = edit_post(post["text"], instruction, post["news"], post["article_text"])
        if validate_post(new_text):
            post["text"] = new_text
            send_preview(chat_id, post)
        else:
            send_message(chat_id, "❌ Результат не пройшов валідацію. Спробуйте іншу інструкцію.")
    except Exception as e:
        send_message(chat_id, f"❌ Помилка: {e}")


def handle_command(message: dict):
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "").strip()
    settings = load_settings()

    if not is_admin(user_id, settings):
        send_message(chat_id, "⛔ У вас немає доступу до цього бота.")
        return

    if text == "/start" or text == "/help":
        mod_status = "✅ увімк." if settings.get("moderation", True) else "❌ вимк."
        send_message(chat_id, (
            "🤖 <b>GlobalPost News Bot — Адмін-панель</b>\n\n"
            "/post — згенерувати пост (на модерацію)\n"
            "/format — змінити формат (news / stat / analysis)\n"
            "/status — поточні налаштування\n"
            "/history — останні 5 публікацій\n"
            "/pause — призупинити / відновити авто-публікацію\n"
            "/weekend — вкл/викл публікації на вихідних\n"
            f"/moderation — ручна модерація ({mod_status})\n\n"
            "💡 <i>Після /post бот покаже превью.\n"
            "Натисніть кнопку або напишіть що змінити.</i>"
        ))

    elif text == "/status":
        fmt = settings.get("format_override") or "авто (за розкладом)"
        paused = "⏸ Призупинено" if settings.get("paused") else "▶️ Активний"
        weekend = "✅ Так" if settings.get("post_on_weekends") else "❌ Ні"
        moderation = "✅ Увімк." if settings.get("moderation", True) else "❌ Вимк."
        history = load_history()
        last = history[-1] if history else "—"
        pending = "✅ Є" if chat_id in pending_posts else "—"
        send_message(chat_id, (
            f"📊 <b>Статус бота</b>\n\n"
            f"Стан: {paused}\n"
            f"Формат: {fmt}\n"
            f"Вихідні: {weekend}\n"
            f"Модерація: {moderation}\n"
            f"Пост на модерації: {pending}\n"
            f"Всього публікацій: {len(history)}\n"
            f"Остання: {last}"
        ))

    elif text == "/pause":
        settings["paused"] = not settings.get("paused", False)
        save_settings(settings)
        state = "⏸ Призупинено" if settings["paused"] else "▶️ Відновлено"
        send_message(chat_id, state)

    elif text == "/weekend":
        settings["post_on_weekends"] = not settings.get("post_on_weekends", True)
        save_settings(settings)
        state = "✅ Публікації на вихідних увімкнено" if settings["post_on_weekends"] else "❌ Публікації на вихідних вимкнено"
        send_message(chat_id, state)

    elif text == "/moderation":
        settings["moderation"] = not settings.get("moderation", True)
        save_settings(settings)
        if settings["moderation"]:
            send_message(chat_id, "✅ Ручна модерація увімкнена.\nПости будуть надсилатися вам на перевірку перед публікацією.")
        else:
            send_message(chat_id, "❌ Ручна модерація вимкнена.\nПости публікуватимуться автоматично за розкладом.")

    elif text.startswith("/format"):
        parts = text.split()
        if len(parts) == 2 and parts[1] in ("news", "stat", "analysis", "auto"):
            if parts[1] == "auto":
                settings["format_override"] = None
            else:
                settings["format_override"] = parts[1]
            save_settings(settings)
            send_message(chat_id, f"✅ Формат змінено: <b>{parts[1]}</b>")
        else:
            send_message(chat_id, (
                "Використання: /format [тип]\n\n"
                "Типи:\n"
                "• <b>news</b> — новина\n"
                "• <b>stat</b> — цифра дня\n"
                "• <b>analysis</b> — аналітика\n"
                "• <b>auto</b> — за розкладом"
            ))

    elif text == "/history":
        history = load_history()
        if not history:
            send_message(chat_id, "Історія порожня.")
            return
        last5 = history[-5:]
        lines = [f"{i+1}. {url}" for i, url in enumerate(reversed(last5))]
        send_message(chat_id, "📋 <b>Останні публікації:</b>\n\n" + "\n".join(lines))

    elif text == "/post":
        handle_post_command(chat_id)

    else:
        send_message(chat_id, "Невідома команда. Напишіть /start для списку команд.")


def handle_callback(callback_query: dict):
    """Обработка нажатий на inline-кнопки."""
    chat_id = callback_query["message"]["chat"]["id"]
    user_id = callback_query["from"]["id"]
    data = callback_query.get("data", "")
    callback_id = callback_query["id"]

    settings = load_settings()
    if not is_admin(user_id, settings):
        answer_callback(callback_id, "⛔ Немає доступу")
        return

    answer_callback(callback_id)

    if data == "approve":
        handle_approve(chat_id)
    elif data == "regenerate":
        handle_regenerate(chat_id)
    elif data == "different":
        handle_different(chat_id)
    elif data == "cancel":
        pending_posts.pop(chat_id, None)
        send_message(chat_id, "❌ Публікацію скасовано.")


def handle_text_message(message: dict):
    """Обработка текстовых сообщений (инструкции по правке)."""
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "").strip()

    settings = load_settings()
    if not is_admin(user_id, settings):
        return

    if chat_id in pending_posts:
        handle_edit_instruction(chat_id, text)
    else:
        send_message(chat_id, "Напишіть /post щоб згенерувати новий пост, або /start для списку команд.")


def poll():
    """Поллинг Telegram для получения команд."""
    logger.info("Админ-бот запущен (с модерацией). Ожидаю команды...")
    offset = 0

    while True:
        try:
            resp = requests.get(f"{API_BASE}/getUpdates", params={
                "offset": offset,
                "timeout": 30,
            }, timeout=35)

            if resp.status_code != 200:
                logger.warning(f"getUpdates error: {resp.status_code}")
                time.sleep(5)
                continue

            data = resp.json()
            if not data.get("ok"):
                time.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                # Inline-кнопки
                if "callback_query" in update:
                    handle_callback(update["callback_query"])
                    continue

                message = update.get("message")
                if not message:
                    continue

                text = message.get("text", "")
                if text.startswith("/"):
                    handle_command(message)
                elif text.strip():
                    handle_text_message(message)

        except requests.exceptions.Timeout:
            continue
        except KeyboardInterrupt:
            logger.info("Админ-бот остановлен")
            break
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            time.sleep(5)


if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        print("Установите TELEGRAM_BOT_TOKEN:")
        print("  export TELEGRAM_BOT_TOKEN='ваш_токен'")
        sys.exit(1)
    poll()
