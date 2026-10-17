from pathlib import Path
from urllib.request import getproxies
from urllib.parse import urlsplit, urlunsplit
import os
BASE_DIR = Path(__file__).resolve().parent
# -*- coding: utf-8 -*-
import sqlite3
import google.generativeai as genai
import os
import sys
from datetime import datetime

# === НАСТРОЙКИ ===
# ВАЖНО: Хранение API ключа в коде небезопасно! Рассмотрите переменные окружения.
API_KEY = os.environ.get("GEMINI_API_KEY", 'YOUR_GEMINI_API_KEY')
NUM_ARTICLES_TO_GENERATE = 10  # Укажите желаемое количество статей
DB_NAME = str(BASE_DIR / 'articles.db')
OUTPUT_FILE = str(BASE_DIR / 'articles_paste.txt')
MODEL_NAME = "gemini-3.8-flash"

# --- Шаблон Промта ---
PROMPT_TEMPLATE = """
Ты — автор, который пишет кликабельные и SEO-дружественные статьи на русском языке для автоматической публикации в Telegraph через Python-скрипт. Скрипт сокращает ссылки с помощью сервиса UrlLy и сохраняет информацию в базу данных SQLite. Твоя задача — генерировать статьи по заданным требованиям и улучшенному стилю. Следуй этим инструкциям:

---

### ОСНОВНЫЕ ТРЕБОВАНИЯ К ФОРМАТУ

1) Каждая статья должна быть оформлена в формате Python-кода для VS Code:
Use code with caution.
Python
Статья <номер>
title = "Заголовок статьи"
content = \"\"\"
HTML содержимое
\"\"\"

2) Между статьями добавляй комментарии `# Статья 1`, `# Статья 2` и т.д. для разделения.

3) Сохраняй отступы и переносы строк в `content = \"\"\"...\"\"\"`, чтобы текст оставался читабельным для копирования в файл `articles_paste.txt`.

4) В каждой статье должно быть минимум 3 реальных, работающих ссылки, относящихся к теме. Формат ссылок строго:
Use code with caution.
<p>🔗 <a href="https://example.com">Описание ссылки</a></p>

- Ссылки должны начинаться с `https://`, без пробелов.
- Допустимые источники: официальные сайты (например, `https://protonvpn.com`), Telegram-боты/чаты (`https://t.me/...`), YouTube (`https://www.youtube.com/...`), образовательные платформы (`https://stepik.org/...`), GitHub (`https://github.com/...`), инструменты (`https://notion.so`), проекты/игры (`https://steamcommunity.com`).
- Ссылки не должны вести на страницы входа, соцсети без контекста, Google Поиск или битые страницы.

5) Списки и советы оформляй так (с пустой строкой перед <h4> и каждый пункт <p>• на новой строке):

<h4>Название раздела</h4>
<p>• Пункт 1</p>
<p>• Пункт 2</p>
<p>• Пункт 3</p>
Use code with caution.

6) Темы бери из ТОП-20 кликабельных тем Рунета 2025 года:
Telegram, Криптовалюты и аирдропы, Игры, Взлом и хаки, VPN, Нейросети, Заработок в интернете, Windows/Android-хаки, TikTok/YouTube, Расширения для браузеров, AI-инструменты, Анонимность и безопасность, Фишки для смартфонов, Секретные сайты и боты, Обучение Python и ИТ и другие подобные темы.

7) Структура статьи:
- Вступление (1 абзац, 3-5 предложений).
- 3 смысловых раздела с заголовками <h4> и минимум 1 ссылкой в каждом.
- 1 раздел с советами (3+ пункта в формате <p>•).
- Завершение (1-2 предложения с призывом к действию).

8) Избегай повторов тем из этого списка (они уже есть в базе): {existing_titles_str}

9) Используй смайлы для выделения подзаголовков и важных частей:
- Перед каждым <h4> ставь пустую строку и добавляй смайлы (например, 📡, ⚙️, 🚀 и другие подобные смайлы).
- В тексте применяй смайлы умеренно (✔️, 💡, ✨, 📌 и другие подобные смайлы) для разделения блоков и акцентов.
- В расслабленных темах (игры, TikTok) добавляй прикольные смайлы (🎮, 🤖, 🌟 и другие подобные смайлы).
- В серьёзных темах (VPN, безопасность) используй строгие смайлы (🔒, 🛡, 🔹 и другие подобные смайлы).
- Не ставь смайлы в заголовок title.

УЛУЧШЕНИЯ ДЛЯ СТАТЕЙ:

10) Добавляй визуальные разделители:
Используй <hr> между вступлением и первым разделом для визуального разрыва.

11) Делай вступление привлекательным:

Перед первым абзацем вставь короткий список (3 пункта) с эмодзи:

<p>Что ты узнаешь:</p>
<p>✔️ Пункт 1</p>
<p>✔️ Пункт 2</p>
<p>✔️ Пункт 3</p>

- В первом абзаце задавай вопрос читателю (например, «Хочешь больше просмотров?»).

12) Улучшай ссылки:
- Делай описания ссылок кликабельными с глаголами или выгодами (например, «Скачай ProtonVPN», «Узнай тренды TikTok» вместо «Сайт ProtonVPN»).
- Меняй эмодзи перед ссылками в зависимости от типа: 📥 (скачивание), 🎥 (видео), 🤖 (боты).

13) Завершение с призывом:
В конце добавляй мотивацию (например, «Попробуй прямо сейчас!», «Сними видео и стань звездой!»).

14) SEO-оптимизация:
Вставляй ключевые слова в <h4> и первый абзац (например, «VPN 2025», «TikTok 2025»).

15) Интерактивность:
Добавляй вопросы к читателю в тексте (например, «Круто, правда?»).

ЗАДАНИЕ:

Сгенерируй {num_articles} статей по этим правилам. Не пиши лишних комментариев вне шаблона — только код для копирования в articles_paste.txt. Убедись, что:
- Темы уникальны и не повторяют указанный список.
- Каждая статья содержит минимум 3 разные ссылки.
- Форматирование читабельное, с отступами и переносами.
- Смайлы соответствуют тематике и используются умеренно.

Пример структуры статьи:

# Статья 1
title = "Как ускорить Windows 11 без лишних программ в 2025 году"
content = \"\"\"
<p>Твой ПК тормозит? Windows 11 можно разогнать без подозрительных утилит!</p>
<p>Что ты узнаешь:</p>
<p>✔️ Как отключить лишнее</p>
<p>✔️ Как ускорить загрузку</p>
<p>✔️ Как сохранить нервы</p>
<hr>

<h4>🧹 Чистка автозагрузки</h4>
<p>Открой «Диспетчер задач» и выруби ненужное.</p>
<p>🔗 <a href="https://example.com/autostart-guide">Смотри гайд по автозагрузке</a></p>

<h4>⚡ Оптимизация эффектов</h4>
<p>Выключи анимации для скорости.</p>
<p>🔗 <a href="https://example.com/ms-performance-tips">Читай советы от Microsoft</a></p>

<h4>🔋 Экономия энергии</h4>
<p>Включи режим «Максимальная производительность».</p>
<p>🔗 <a href="https://example.com/power-plan-tutorial">Узнай, как это сделать</a></p>

<h4>📌 Твои шаги к скорости</h4>
<p>• Отключи 3 программы.</p>
<p>• Выключи эффекты.</p>
<p>• Перезагружай ПК раз в неделю.</p>

<p>🚀 Windows 11 летает! Попробуй прямо сейчас!</p>
\"\"\"

"""

# --- Функции ---
def get_existing_titles(db_path):
    """Загружает существующие заголовки из базы данных SQLite."""
    titles = []
    if not os.path.exists(db_path):
        print(f"⚠️ Файл базы данных '{db_path}' не найден.")
        return titles

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='articles';")
        if cur.fetchone() is None:
            print(f"⚠️ Таблица 'articles' не найдена в базе данных '{db_path}'.")
            conn.close()
            return titles

        cur.execute("PRAGMA table_info(articles)")
        columns = [info[1] for info in cur.fetchall()]
        if 'title' not in columns:
            print(f"⚠️ Столбец 'title' не найден в таблице 'articles' базы данных '{db_path}'.")
            conn.close()
            return titles

        cur.execute("SELECT title FROM articles WHERE title IS NOT NULL")
        rows = cur.fetchall()
        titles = [row[0] for row in rows]
        conn.close()
        print(f"✅ Найдено {len(titles)} существующих заголовков в '{db_path}'.")
    except sqlite3.Error as e:
        print(f"❌ Ошибка при чтении заголовков из БД '{db_path}': {e}")
    return titles

def generate_articles(api_key, model_name, prompt):
    """Отправляет промт в Gemini API и возвращает сгенерированный текст."""
    print(f"⚙️ Конфигурация Gemini API (Модель: {model_name})...")
    try:
        # REST совместим с локальным HTTP CONNECT-прокси.
        for protocol, proxy_url in getproxies().items():
            if protocol not in ("http", "https"):
                continue
            parsed = urlsplit(proxy_url)
            if parsed.scheme == "https" and parsed.hostname in ("localhost", "127.0.0.1", "::1"):
                proxy_url = urlunsplit(parsed._replace(scheme="http"))
            os.environ[protocol.upper() + "_PROXY"] = proxy_url
            os.environ[protocol + "_proxy"] = proxy_url
        genai.configure(api_key=api_key, transport="rest")
        model = genai.GenerativeModel(model_name)
        print("⏳ Генерация статей... Это может занять некоторое время.")
        response = model.generate_content(prompt)
        if response and hasattr(response, 'text'):
            print("✅ Статьи успешно сгенерированы.")
            return response.text
        else:
            print("❌ API не вернуло текст. Ответ:")
            print(response)
            return None
    except Exception as e:
        print(f"❌ Ошибка при взаимодействии с Gemini API: {e}")
        return None

def save_to_file(filename, content):
    """Сохраняет сгенерированный контент в файл."""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"💾 Сгенерированные статьи сохранены в файл: {filename}")
    except IOError as e:
        print(f"❌ Ошибка при сохранении в файл '{filename}': {e}")

# --- Основной блок выполнения ---
if __name__ == "__main__":
    print("-" * 30)
    print("🚀 Запуск скрипта генерации статей Gemini")
    print("-" * 30)

    existing_titles = get_existing_titles(DB_NAME)
    if existing_titles:
        titles_string = ", ".join(f'"{title}"' for title in existing_titles)
    else:
        titles_string = "Нет существующих тем для исключения."

    final_prompt = PROMPT_TEMPLATE.format(
        num_articles=NUM_ARTICLES_TO_GENERATE,
        existing_titles_str=titles_string
    )

    generated_content = generate_articles(API_KEY, MODEL_NAME, final_prompt)
    if generated_content:
        save_to_file(OUTPUT_FILE, generated_content)
    else:
        print("❗️ Генерация не удалась или API вернуло пустой ответ. Файл не был создан/перезаписан.")

    print("-" * 30)
    print("🏁 Скрипт завершил работу.")
    print("-" * 30)
