from pathlib import Path
import os
BASE_DIR = Path(__file__).resolve().parent
import re
import time
import json
import sqlite3
import ast
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from telegraph import Telegraph

# === НАСТРОЙКИ ===
URLLY_API_TOKEN = os.environ.get("URLLY_API_TOKEN", "")
CHROMEDRIVER_PATH = str(BASE_DIR / "chromedriver" / "chromedriver.exe")
TELEGRAPH_AUTHOR = "Jonny Poster"
DB_NAME = str(BASE_DIR / 'articles.db')
ARTICLES_FILE = str(BASE_DIR / 'articles_paste.txt')

# === Инициализация Telegraph ===
with open(BASE_DIR / "telegraph_token.txt", "r") as f:
    access_token = f.read().strip()
telegraph = Telegraph(access_token=access_token)

# === Сокращение URL через UrlLy + Selenium ===
def shorten_url_with_urlly(original_url):
    full_url = f"http://urlly.ru/st?api={URLLY_API_TOKEN}&url={original_url}"

    print(f"\n🌍 [START] Сокращение ссылки:\n→ {original_url}")

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--log-level=3")

    service = Service(executable_path=CHROMEDRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=options)

    try:
        print("🔄 Открываю UrlLy в фоновом режиме...")
        driver.get(full_url)
        time.sleep(4)
        final_url = driver.current_url

        if final_url != original_url:
            print(f"✅ Ссылка сокращена:\n→ {final_url}")
        else:
            print("⚠️ Редирект не сработал, оставляем оригинальную ссылку.")

        return final_url
    except Exception as e:
        print(f"❌ Ошибка при обработке: {e}")
        return original_url
    finally:
        driver.quit()
        print("🧹 Браузер закрыт.")

# === Очистка текста от HTML для превью ===
def html_to_text_preview(html):
    text = re.sub('<[^<]+?>', '', html)
    return text[:250] + "..." if len(text) > 250 else text

# === Обновление статьи в БД и Telegraph ===
def update_article(title, content_html):
    try:
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()

        # Получаем ссылку на существующую статью
        cur.execute("SELECT telegraph_url FROM articles WHERE title = ? ORDER BY id DESC LIMIT 1", (title,))
        result = cur.fetchone()

        if not result:
            print(f"❌ Статья '{title}' не найдена в базе. Пропускаем.")
            return

        telegraph_url = result[0]
        page_path = telegraph_url.split("/")[-1]

        # Распознаём экранированный HTML
        content_html = ast.literal_eval(f'"""{content_html}"""')

        # Сокращаем ссылки
        urls = re.findall(r'href=["\'](https?://[^"\']+)["\']', content_html)
        short_links = []

        print(f"🔍 Найдено ссылок для сокращения: {len(urls)}")
        for url in urls:
            short = shorten_url_with_urlly(url)
            short_links.append(short)
            content_html = content_html.replace(url, short)
            print(f"🔗 Заменено: {url} → {short}")

        # Обновление статьи
        response = telegraph.edit_page(
            path=page_path,
            title=title,
            html_content=content_html.strip(),
            author_name=TELEGRAPH_AUTHOR
        )

        # Обновление в БД
        clean_html = content_html.strip()
        clean_preview = html_to_text_preview(clean_html)

        cur.execute("""
            UPDATE articles
            SET content_html = ?, text_preview = ?, raw_links = ?, short_links = ?, published_at = ?
            WHERE title = ?
        """, (
            clean_html,
            clean_preview,
            json.dumps(urls, ensure_ascii=False),
            json.dumps(short_links, ensure_ascii=False),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            title
        ))

        conn.commit()
        conn.close()

        print("✅ Статья обновлена!")
        print("🔗 Ссылка:", response["url"])
        print("💾 Обновление БД завершено.")

    except Exception as e:
        print(f"❌ Ошибка при обновлении: {e}")

# === Загрузка и обработка статей ===
with open(ARTICLES_FILE, "r", encoding="utf-8") as file:
    content_raw = file.read()

article_blocks = re.findall(r'title\s*=\s*"(.*?)"\s*content\s*=\s*"""(.*?)"""', content_raw, re.DOTALL)
print(f"\n📄 Найдено статей для обновления: {len(article_blocks)}")

for i, (title, html_content) in enumerate(article_blocks, start=1):
    print(f"\n🔁 [{i}] Обновление статьи: {title}")
    update_article(title, html_content)