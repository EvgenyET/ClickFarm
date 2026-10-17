from pathlib import Path
import os
BASE_DIR = Path(__file__).resolve().parent
import re
import time
import json
import sqlite3
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from telegraph import Telegraph
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

# === НАСТРОЙКИ ===
URLLY_API_TOKEN = os.environ.get("URLLY_API_TOKEN", "")
CHROMEDRIVER_PATH = str(BASE_DIR / "chromedriver" / "chromedriver.exe")
TELEGRAPH_AUTHOR = "Jonny Poster"
DB_NAME = str(BASE_DIR / 'articles.db')
ARTICLES_FILE = str(BASE_DIR / 'articles_paste.txt')
MAX_CONCURRENT_ARTICLES = 10  # Количество одновременно обрабатываемых статей (для публикации и ссылок)

# === Создание экземпляра Chrome-драйвера ===
def create_chrome_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--log-level=3")
    service = Service(executable_path=CHROMEDRIVER_PATH)
    return webdriver.Chrome(service=service, options=options)

# === Функция для сокращения ссылок через UrlLy и Selenium с передачей драйвера ===
def shorten_url_with_urlly(original_url, driver):
    full_url = f"http://urlly.ru/st?api={URLLY_API_TOKEN}&url={original_url}"
    print(f"\n🌍 [START] Сокращение ссылки:\n→ {original_url}")

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

# === Вспомогательная функция для обрезки текста без HTML-тегов (превью) ===
def html_to_text_preview(html):
    text = re.sub('<[^<]+?>', '', html)
    return text[:250] + "..." if len(text) > 250 else text

# === Сохранение статьи в БД с логированием и повторными попытками ===
def save_article(title, telegraph_url, content_html, raw_links, short_links, index):
    while True:  # Цикл для повторных попыток
        try:
            clean_html = content_html.strip()
            clean_preview = html_to_text_preview(clean_html)
            published_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # Время записи в БД

            conn = sqlite3.connect(DB_NAME)
            cur = conn.cursor()

            cur.execute("""
                INSERT INTO articles (
                    title, content_html, text_preview, telegraph_url,
                    published_at, raw_links, short_links
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                title,
                clean_html,
                clean_preview,
                telegraph_url,
                published_at,
                json.dumps(raw_links, ensure_ascii=False),
                json.dumps(short_links, ensure_ascii=False)
            ))

            conn.commit()
            conn.close()

            print(f"\n💾 ДАННЫЕ СОХРАНЕНЫ В БД: {DB_NAME}")
            print("📝 Заголовок:", title)
            print(f"🔗 Ссылка на статью {index}:", telegraph_url)
            print("📅 Дата записи в БД (published_at):", published_at)
            print(f"🔍 Ссылок найдено: {len(raw_links)} (сокращено: {len(short_links)})")
            for i, (r, s) in enumerate(zip(raw_links, short_links), start=1):
                print(f"   {i}. {r} → {s}")
            print("✅ Запись успешно добавлена в таблицу articles.\n")
            return True  # Успешная запись
        except Exception as e:
            print(f"❌ Ошибка при сохранении в БД: {e}")
            retry = input("🔄 Попробовать сохранить ещё раз? Нажми Enter для повтора или любую другую клавишу для пропуска: ")
            if retry != "":
                print(f"⏩ Пропускаем сохранение статьи '{title}'.")
                return False  # Пропуск записи
            else:
                print("🔄 Повторяю попытку сохранения...")

# === Проверка токена Telegraph перед запуском ===
def check_telegraph_token(token):
    print("Проверка валидности токена.")
    try:
        response = requests.get(f"https://api.telegra.ph/getAccountInfo?access_token={token}")
        data = response.json()
        if data.get("ok"):
            print(f"🔑 Токен Telegraph валиден: {data['result']['short_name']}")
            return True
        else:
            print(f"❌ Ошибка токена: {data.get('error', 'Неизвестная ошибка')}")
            return False
    except Exception as e:
        print(f"❌ Ошибка проверки токена: {e}")
        return False

# === Обработка одной статьи с параллельным сокращением ссылок ===
def process_article(article_data, index):
    title, content = article_data
    print(f"\n📝 Публикация статьи {index}: {title}")

    # Ищем все ссылки
    urls = re.findall(r'href=["\'](https?://[^"\']+)["\']', content)
    print(f"🔍 Найдено ссылок для сокращения: {len(urls)}")

    # Создаем пул драйверов (до MAX_CONCURRENT_ARTICLES)
    drivers = [create_chrome_driver() for _ in range(min(MAX_CONCURRENT_ARTICLES, len(urls)))]
    shortened_urls = [None] * len(urls)  # Предварительно создаем список для сохранения порядка

    # Параллельное сокращение ссылок с сохранением порядка
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_ARTICLES) as executor:
        futures = {executor.submit(shorten_url_with_urlly, url, drivers[i % len(drivers)]): i for i, url in enumerate(urls)}
        for future in as_completed(futures):
            idx = futures[future]  # Получаем индекс оригинальной ссылки
            short_url = future.result()
            shortened_urls[idx] = short_url  # Записываем результат в правильную позицию

    # Закрываем все драйверы после обработки
    for driver in drivers:
        driver.quit()
    print(f"🧹 Все браузеры закрыты для статьи '{title}'.")

    # Заменяем ссылки в контенте
    for orig, short in zip(urls, shortened_urls):
        content = content.replace(orig, short)
        print(f"🔗 Заменено: {orig} → {short}")

    # Публикация на Telegraph с обработкой ошибок
    while True:
        try:
            response = telegraph.create_page(
                title=title,
                html_content=content,
                author_name=TELEGRAPH_AUTHOR
            )
            print(f"✅ Статья {index} опубликована!")
            print("🔗 Ссылка:", response["url"])
            break
        except Exception as e:
            print(f"❌ Ошибка при публикации на Telegraph: {e}")
            try:
                last_response = requests.post(
                    "https://api.telegra.ph/createPage",
                    data={
                        "access_token": access_token,
                        "title": title,
                        "content": json.dumps([{"tag": "p", "children": [content]}]),
                        "author_name": TELEGRAPH_AUTHOR
                    }
                ).text
                print(f"📋 PSYОтвет от Telegraph: {last_response}")
            except Exception as log_e:
                print(f"❌ Не удалось получить ответ: {log_e}")
            retry = input("🔄 Попробовать опубликовать ещё раз? Нажми Enter для повтора или любую другую клавишу для пропуска: ")
            if retry != "":
                print(f"⏩ Пропускаем публикацию статьи '{title}'.")
                return None
            else:
                print("🔄 Повторяю попытку публикации...")
                time.sleep(2)

    # Возвращаем данные для записи в БД
    if 'response' in locals():
        return {
            'index': index,
            'title': title,
            'telegraph_url': response["url"],
            'content_html': content,
            'raw_links': urls,
            'short_links': shortened_urls
        }
    return None

# === Основной блок выполнения ===
if __name__ == "__main__":
    # Загрузка токена Telegraph
    with open(BASE_DIR / "telegraph_token.txt", "r") as f:
        access_token = f.read().strip()

    # Проверяем токен перед инициализацией (закомментировано по просьбе пользователя)
    # if not check_telegraph_token(access_token):
    #     print("❌ Публикация остановлена из-за неверного токена!")
    #     exit(1)

    telegraph = Telegraph(access_token=access_token)

    # Загрузка всех статей из файла
    with open(ARTICLES_FILE, "r", encoding="utf-8") as f:
        raw_data = f.read()

    # Разделяем статьи по шаблону: title = "..." + content = """..."""
    articles = re.findall(r'title\s*=\s*"(.*?)"\s*content\s*=\s*"""(.*?)"""', raw_data, re.DOTALL)
    print(f"\n📄 Найдено статей для публикации: {len(articles)}")

    # Валидация ссылок до запуска Selenium
    for idx, (title, content) in enumerate(articles, start=1):
        urls = re.findall(r'href=["\'](https?://[^"\']+)["\']', content)
        if len(urls) < 3:
            print(f"❌ ОШИБКА: В статье [{idx}] '{title}' недостаточно ссылок для сокращения ({len(urls)} найдено). Публикация остановлена!")
            exit(1)

    # Многопоточная обработка статей
    print("\n🚀 Начинаю многопоточную публикацию статей...")
    results = [None] * len(articles)  # Список для хранения результатов в порядке индексов
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_ARTICLES) as executor:
        futures = {executor.submit(process_article, article, i): i for i, article in enumerate(articles, start=1)}
        for future in as_completed(futures):
            idx = futures[future] - 1  # Индекс в списке (начинается с 0)
            result = future.result()
            results[idx] = result  # Сохраняем результат в правильной позиции

    # Последовательная запись в БД по порядку из файла
    print("\n Mutopia в БД в порядке из файла...")
    for i, result in enumerate(results, start=1):
        if result:
            success = save_article(
                title=result['title'],
                telegraph_url=result['telegraph_url'],
                content_html=result['content_html'],
                raw_links=result['raw_links'],
                short_links=result['short_links'],
                index=i
            )
            if success:
                print(f"✅ Статья {i} успешно записана в БД.")
            else:
                print(f"⚠️ Статья {i} не была записана в БД из-за пропуска пользователем.")
        else:
            print(f"⚠️ Статья {i} не была опубликована, пропускаем запись в БД.")

    # Сообщение о завершении всех публикаций
    print("\n🏁 Все статьи обработаны и записаны в базу данных в порядке из файла.")