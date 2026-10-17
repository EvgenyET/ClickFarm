from pathlib import Path
import os
BASE_DIR = Path(__file__).resolve().parent
# -*- coding: utf-8 -*-

# Стандартные библиотеки
import sys              # Для системных функций (выход из приложения)
from contextlib import closing
import sqlite3          # Для работы с базой данных SQLite
import requests         # Для выполнения HTTP-запросов к API
import json             # Для работы с форматом JSON
import re               # Для работы с регулярными выражениями (поиск URL, парсинг ошибок)
from datetime import datetime, timedelta, date as standard_date # Работа с датой и временем
import time             # Для пауз (time.sleep)
import threading
import os               # Для работы с операционной системой (проверка существования файлов)
from concurrent.futures import ThreadPoolExecutor, as_completed # Для многопоточности (параллельные запросы)

# Сторонние библиотеки
import pandas as pd     # Для агрегации данных графика и работы с временными рядами
try:
    import numpy as np  # Для математических операций (нужно для pyqtgraph)
except ImportError:
    print("Ошибка: Необходима библиотека NumPy. Установите ее: pip install numpy")
    sys.exit(1)
# --- Добавляем pytz для часовых поясов ---
try:
    import pytz # Библиотека для работы с часовыми поясами
except ImportError:
    print("Ошибка: Необходима библиотека pytz. Установите ее: pip install pytz")
    sys.exit(1)
# ---------------------------------------

# Библиотеки GUI (PyQt5 и PyQtGraph)
from PyQt5.QtWidgets import ( QApplication, QMainWindow, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QHeaderView, QLabel, QStatusBar,
    QProgressBar, QAbstractItemView, QMessageBox,
    QDialog, QCalendarWidget, QDialogButtonBox) # Элементы интерфейса
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QDate # Основные классы Qt и сигналы/слоты
from PyQt5.QtGui import QColor, QFont # Для стилизации (цвет, шрифт)
import pyqtgraph as pg # Библиотека для графиков

# Локальный прокси принимает HTTP CONNECT, в том числе для HTTPS-сайтов.
from urllib.request import getproxies

API_PROXIES = getproxies()
_https_proxy = API_PROXIES.get('https', '')
if _https_proxy.startswith(('https://127.0.0.1:', 'https://localhost:')):
    API_PROXIES['https'] = 'http://' + _https_proxy[len('https://'):]

# === КОНФИГУРАЦИЯ ===
DB_NAME = str(BASE_DIR / 'articles.db')           # Имя единой базы данных для статей и статистики
START_DATE_STR = "2025-03-30"     # Дата НАЧАЛА для ГРАФИКА и ПОЛНОГО СКАНИРОВАНИЯ (ГГГГ-ММ-ДД)
MAX_API_WORKERS = 3              # Максимальное количество одновременных потоков для запросов к API
API_DELAY_SUCCESS = 0.2           # Задержка ПОСЛЕ успешного запроса API (в секундах) для снижения нагрузки
API_RETRIES = 4                     # Количество повторных попыток при ошибке API
API_RETRY_DELAY = 2                 # Начальная задержка перед повторной попыткой (в секундах), будет увеличиваться
BAR_COLOR = '#FFA500'             # Цвет заливки гистограммы (оранжевый)
TEXT_COLOR = 'k'                  # Цвет текста с количеством просмотров над столбцами (черный)
GRID_ALPHA = 0.1                  # Прозрачность линий сетки на графике (0.0 - невидима, 1.0 - непрозрачна)
TEXT_Z_VALUE = 20                 # Z-уровень для текста (чтобы был поверх баров)
BAR_Z_VALUE = 10                  # Z-уровень для гистограммы (чтобы была поверх сетки)
BAR_WIDTH_RATIO = 0.92            # Ширина бара гистограммы (0.92 = 92% от ширины дня) для создания небольших отступов
TEXT_SIZE_PT = 7                  # Размер шрифта текста над барами в пунктах
DISABLE_ANTIALIASING = True       # Отключить сглаживание для четкого перекрытия сетки гистограммой
TARGET_TIMEZONE = 'Europe/Moscow' # Часовой пояс для определения "сегодня" и максимальной даты в календаре

# === НАСТРОЙКИ PyQtGraph ===
pg.setConfigOption('background', 'w') # Установка белого фона для графиков
pg.setConfigOption('foreground', 'k') # Установка черного цвета для осей и текста графиков
if DISABLE_ANTIALIASING:
    pg.setConfigOptions(antialias=False) # Отключение сглаживания (может улучшить вид гистограммы)
    print("ℹ️ Сглаживание графика отключено.")

# Отдельная HTTP-сессия на поток повторно использует соединение с прокси.
_api_thread_state = threading.local()
_api_request_lock = threading.Lock()
_api_last_request = 0.0
_api_pause_until = 0.0
API_REQUEST_INTERVAL = 0.5


def get_api_session():
    if not hasattr(_api_thread_state, 'session'):
        _api_thread_state.session = requests.Session()
    return _api_thread_state.session


def wait_for_api_slot():
    global _api_last_request
    with _api_request_lock:
        now = time.monotonic()
        delay = max(_api_last_request + API_REQUEST_INTERVAL, _api_pause_until) - now
        if delay > 0:
            time.sleep(delay)
        _api_last_request = time.monotonic()


def recover_api_connection(attempt):
    """Сбрасывает оборванное соединение и даёт прокси время восстановиться."""
    global _api_pause_until
    session = getattr(_api_thread_state, 'session', None)
    if session is not None:
        session.close()
        del _api_thread_state.session
    cooldown = min(30, 5 * (attempt + 1))
    with _api_request_lock:
        _api_pause_until = max(_api_pause_until, time.monotonic() + cooldown)


# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

# Извлекает 'path' из URL статьи Telegra.ph
def extract_path_from_url(url):
    """Извлекает 'path' (часть URL после telegra.ph/) из ссылки."""
    if not url: return None
    # Ищем совпадение с началом строки, чтобы не захватить лишнего
    match = re.search(r'^https?://telegra\.ph/([^#?]+)', url)
    # Возвращаем найденную группу (сам path) или None
    return match.group(1) if match else None

# --- БОЛЕЕ НАДЕЖНАЯ ФУНКЦИЯ API ЗАПРОСА ---
def get_views_api_robust(path, year, month, day, retries=API_RETRIES, initial_delay=API_RETRY_DELAY):
    """
    Выполняет запрос к API Telegra.ph с повторными попытками и обработкой ошибок.
    Возвращает: число (int) просмотров или None при неудаче после ретраев.
    """
    if not path: return None # Проверка наличия path
    api_url = "https://api.telegra.ph/getViews" # Базовый URL API
    params = {'path': path} # Параметры запроса
    # Добавляем параметры даты/времени только если они переданы (не None)
    if year is not None: params['year'] = year
    if month is not None: params['month'] = month
    if day is not None: params['day'] = day
    current_delay = initial_delay # Начальная задержка для ретраев
    # Формируем строку даты для логов (или 'Total' если даты нет)
    date_str = f"{year}-{month:02d}-{day:02d}" if year and month and day else "Total"

    # Цикл повторных попыток
    for attempt in range(retries):
        try:
            # Выполняем GET-запрос к API с таймаутом
            wait_for_api_slot()
            response = get_api_session().get(api_url, params=params, proxies=API_PROXIES, timeout=(10, 30))
            response.raise_for_status() # Проверка на ошибки HTTP (4xx, 5xx)
            data = response.json() # Парсим JSON ответ

            # Проверяем успешность ответа API
            if data.get("ok"):
                # Возвращаем количество просмотров (или 0, если ключ 'views' отсутствует)
                return data.get("result", {}).get("views", 0)
            else: # Обработка ошибок, возвращенных API Telegra.ph
                error_msg = data.get("error", "Unknown API error")
                if error_msg == "PAGE_NOT_FOUND":
                    return 0 # Считаем просмотры для несуществующей страницы равными 0
                elif "FLOOD_WAIT" in error_msg: # Обработка превышения лимитов запросов
                    try:
                        # Пытаемся извлечь время ожидания из сообщения об ошибке
                        wait_time = max(1, int(re.search(r'\d+', error_msg).group())) # Ждем минимум 1 секунду
                        print(f"    ⏳ Flood wait {wait_time}s (path: {path}, date: {date_str}, att: {attempt+1})...")
                        time.sleep(wait_time + 0.5) # Ждем указанное время + небольшой запас
                    except:
                        # Если не удалось извлечь время, ждем стандартную задержку
                        print(f"    ⏳ Flood wait (parse err), waiting {current_delay:.1f}s...")
                        time.sleep(current_delay)
                    # Для Flood Wait не увеличиваем задержку retry, а сразу повторяем попытку
                    if attempt < retries - 1:
                        continue # Переходим к следующей итерации цикла для ЭТОГО же запроса
                    else:
                        print(f"    ⛔️ Flood wait не прошел для {path} {date_str} после ожидания.")
                else:
                    # Другая ошибка API Telegra.ph
                    print(f"    ❌ API Error '{path}' {date_str}: {error_msg} (Att {attempt+1}/{retries})")
        # Обработка исключений библиотеки requests и json
        except requests.exceptions.Timeout:
            recover_api_connection(attempt)
            print(f"    ❌ Timeout '{path}' {date_str} (Att {attempt+1}/{retries})")
        except requests.exceptions.ConnectionError as e:
            recover_api_connection(attempt)
            print(f"    ❌ Обрыв соединения через сеть/прокси: {path} {date_str} (Att {attempt+1}/{retries}). Соединение будет создано заново.")
        except requests.exceptions.RequestException as e:
            print(f"    ❌ Network/HTTP Error '{path}' {date_str}: {e} (Att {attempt+1}/{retries})")
        except json.JSONDecodeError:
            print(f"    ❌ Invalid JSON '{path}' {date_str} (Att {attempt+1}/{retries})")
        # Обработка других непредвиденных ошибок
        except Exception as e:
            print(f"    ❌ Unexpected Error '{path}' {date_str}: {e} (Att {attempt+1}/{retries})")

        # Если произошла ошибка (кроме Flood Wait, где мы делаем continue),
        # ждем перед следующей попыткой
        if attempt < retries - 1:
            print(f"    ...retrying in {current_delay:.1f} seconds...")
            time.sleep(current_delay)
            current_delay *= 1.5 # Увеличиваем задержку для следующей попытки
        else:
            # Если это была последняя попытка
            print(f"    ⛔️ Не удалось получить данные для {path} {date_str} после {retries} попыток.")

    return None # Возвращаем None, если все попытки не удались

# --- Функции для работы с БД articles.db ---

# Инициализирует базу данных: создает таблицы и столбцы, если их нет
def initialize_db():
    """Проверяет/создает таблицу daily_views и столбец published_at в articles."""
    try:
        conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
        # Проверка/добавление столбца published_at в таблицу articles
        try: cur.execute("SELECT published_at FROM articles LIMIT 1")
        except sqlite3.OperationalError as e:
             if "no such column" in str(e):
                try: cur.execute("ALTER TABLE articles ADD COLUMN published_at TEXT"); conn.commit(); print(f"📊 Добавлен 'published_at' в '{DB_NAME}'.")
                except sqlite3.OperationalError as alter_e:
                     if "duplicate column name" not in str(alter_e): print(f"❌ Ошибка добавления 'published_at': {alter_e}")
             else: print(f"❌ Ошибка проверки 'published_at': {e}")
        # Создание таблицы daily_views, если она не существует
        cur.execute("""CREATE TABLE IF NOT EXISTS daily_views (
                        article_id INTEGER NOT NULL, -- ID статьи из таблицы articles
                        view_date TEXT NOT NULL,     -- Дата в формате 'YYYY-MM-DD'
                        views INTEGER NOT NULL,      -- Количество просмотров за эту дату
                        FOREIGN KEY (article_id) REFERENCES articles (id) ON DELETE CASCADE, -- Связь с таблицей articles
                        UNIQUE (article_id, view_date) -- Уникальность пары (статья, дата)
                       )""")
        # Создание индексов для ускорения запросов к daily_views
        cur.execute("CREATE INDEX IF NOT EXISTS idx_daily_views_date ON daily_views (view_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_daily_views_article_date ON daily_views (article_id, view_date)")
        conn.commit(); conn.close() # Сохраняем изменения и закрываем соединение
        print(f"📊 БД '{DB_NAME}' инициализирована.")
    except sqlite3.Error as e: print(f"❌ Ошибка инициализации БД '{DB_NAME}': {e}")

# Загружает данные статей (id, title, url, published_at) из БД
def get_articles_from_db():
    """Получает список словарей со статьями из БД."""
    articles = []; url_column_name = None
    try:
        conn = sqlite3.connect(DB_NAME); conn.row_factory = sqlite3.Row; cur = conn.cursor()
        # Получаем информацию о столбцах таблицы articles
        cur.execute("PRAGMA table_info(articles)"); columns = [info['name'] for info in cur.fetchall()]
        has_published_at = 'published_at' in columns # Проверяем наличие published_at
        # Определяем имя столбца с URL (telegraph_url или url)
        if 'telegraph_url' in columns: url_column_name = 'telegraph_url'
        elif 'url' in columns: url_column_name = 'url'; print(f"⚠️ Используется столбец 'url'.")
        else: print(f"❌ Не найден столбец URL."); conn.close(); return []
        # Проверяем наличие столбца id
        if 'id' not in columns: print(f"❌ Не найден столбец 'id'."); conn.close(); return []
        id_column_name = 'id'
        # Формируем SQL-запрос
        select_columns = f"{id_column_name}, title, {url_column_name}"
        select_columns += ", published_at" if has_published_at else ", NULL as published_at"
        query = f"SELECT {select_columns} FROM articles WHERE {url_column_name} IS NOT NULL AND {url_column_name} != ''"
        cur.execute(query)
        # Создаем список словарей из результатов запроса
        for row in cur.fetchall(): articles.append({"id": row[id_column_name], "title": row["title"], "url": row[url_column_name], "published_at": row["published_at"]})
        conn.close()
        print(f"📊 Загружено {len(articles)} статей из БД '{DB_NAME}'.")
    except sqlite3.Error as e: print(f"❌ Ошибка чтения статей: {e}")
    return articles

def get_missing_view_dates(article_id, start_date, end_date):
    """Возвращает пробелы в истории; сегодняшний день обновляется повторно."""
    with closing(sqlite3.connect(DB_NAME)) as conn:
        saved = {row[0] for row in conn.execute(
            "SELECT view_date FROM daily_views WHERE article_id=? AND view_date BETWEEN ? AND ?",
            (article_id, start_date.isoformat(), end_date.isoformat()))}
    return [start_date + timedelta(days=i)
            for i in range(max(0, (end_date - start_date).days + 1))
            if (start_date + timedelta(days=i)).isoformat() not in saved
            or start_date + timedelta(days=i) == end_date]

# Вставляет или обновляет (заменяет) записи в daily_views
def insert_or_replace_daily_views(views_data):
    """Массовая вставка/замена данных в daily_views."""
    if not views_data: return 0 # Ничего не делаем, если список пуст
    inserted_count = 0
    try:
        conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
        # Используем INSERT OR REPLACE для атомарной вставки/обновления
        cur.executemany("INSERT OR REPLACE INTO daily_views (article_id, view_date, views) VALUES (?, ?, ?)", views_data)
        conn.commit(); inserted_count = cur.rowcount; conn.close() # Получаем количество затронутых строк
    except sqlite3.Error as e: print(f"❌ Ошибка записи в daily_views: {e}")
    return inserted_count

# Агрегирует (суммирует) просмотры по дням из БД для графика
def get_aggregated_daily_views(start_date):
    """Получает суммарные просмотры по дням из БД и возвращает Pandas Series."""
    aggregated_data = {}; start_date_str = start_date.strftime('%Y-%m-%d')
    try:
        conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
        # SQL-запрос для суммирования просмотров по датам
        cur.execute("SELECT view_date, SUM(views) as total_views FROM daily_views WHERE view_date >= ? GROUP BY view_date ORDER BY view_date ASC", (start_date_str,))
        for row in cur.fetchall(): aggregated_data[row[0]] = row[1] # Заполняем словарь {дата: сумма_просмотров}
        conn.close()
        print(f"📈 Агрегировано {len(aggregated_data)} дней из БД для графика.")
    except sqlite3.Error as e: print(f"❌ Ошибка агрегации: {e}")
    # Преобразуем словарь в Pandas Series с индексом DateTime
    if not aggregated_data: return pd.Series(dtype=int) # Возвращаем пустую серию, если нет данных
    try:
        s = pd.Series(aggregated_data, dtype=int); s.index = pd.to_datetime(s.index)
        # Заполняем пропущенные дни нулями для непрерывности графика
        if not s.empty: s = s.reindex(pd.date_range(start=s.index.min(), end=s.index.max(), freq='D'), fill_value=0)
        s.sort_index(inplace=True); return s
    except Exception as e: print(f"❌ Ошибка конвертации в Series: {e}"); return pd.Series(dtype=int)

# --- НОВАЯ ФУНКЦИЯ для получения общих просмотров из БД ---
def get_total_views_for_article_from_db(article_id):
    """Получает сумму всех просмотров для одной статьи из daily_views."""
    total_views = 0
    try:
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        # Суммируем все просмотры для данного article_id
        cur.execute("SELECT SUM(views) FROM daily_views WHERE article_id = ?", (article_id,))
        result = cur.fetchone()
        if result and result[0] is not None: # Проверяем, что результат не None
            total_views = int(result[0])
        conn.close()
    except sqlite3.Error as e:
        print(f"❌ Ошибка SUM(views) для id={article_id}: {e}")
        total_views = 0 # Возвращаем 0 при ошибке
    except Exception as e:
        print(f"❌ Непредв. ошибка SUM(views) для id={article_id}: {e}")
        total_views = 0
    return total_views
# ---------------------------------------------------------


# === ПОТОК ДЛЯ ЗАГРУЗКИ ДАННЫХ ===
class DataLoaderThread(QThread):
    """Выполняет загрузку и обновление статистики в фоне."""
    table_data_ready = pyqtSignal(list)
    graph_data_ready = pyqtSignal(object)
    status_update = pyqtSignal(str)
    progress_update = pyqtSignal(int)
    error_report = pyqtSignal(list)

    def __init__(self, start_date, force_full_scan=False, single_date=None):
        super().__init__(); self.start_date = start_date; self.is_running = True
        self.force_full_scan = force_full_scan; self.single_date = single_date
        self.failed_tasks_info = []

    def run(self):
        """Основная логика работы потока."""
        if not self.is_running: return
        self.status_update.emit(f"Иниц. БД '{DB_NAME}'..."); initialize_db(); self.progress_update.emit(5)
        if not self.is_running: return
        self.status_update.emit(f"Загрузка статей..."); db_articles = get_articles_from_db()
        if not db_articles: self.status_update.emit(f"Нет статей."); self.progress_update.emit(100); self.graph_data_ready.emit(pd.Series(dtype=int)); self.table_data_ready.emit([]); return
        self.progress_update.emit(10)
        if not self.is_running: return

        # --- 2. Загрузка общих просмотров (для таблицы) ---
        # (Этот блок остается без изменений, он использует API)
        self.status_update.emit("Общие просмотры (API)..."); table_results_dict = {}; total_steps_part1 = len(db_articles); current_step_part1 = 0
        with ThreadPoolExecutor(max_workers=MAX_API_WORKERS) as executor:
            future_to_url = { executor.submit(get_views_api_robust, extract_path_from_url(a["url"]), None, None, None): a["url"] for a in db_articles }
            for future in as_completed(future_to_url):
                if not self.is_running: return
                url = future_to_url[future]; article_info = next((a for a in db_articles if a["url"] == url), None)
                if not article_info: continue
                try: total_views = future.result()
                except Exception as e: print(f"❌ Крит. ошибка future общ.пр. {url}: {e}"); total_views = None
                table_results_dict[url] = { "title": article_info["title"], "url": url, "published_at": article_info["published_at"], "total_views": total_views if total_views is not None else "Ошибка"}
                current_step_part1 += 1; self.progress_update.emit(10 + int(30 * current_step_part1 / total_steps_part1 if total_steps_part1 > 0 else 30))
        table_results = sorted(list(table_results_dict.values()), key=lambda x: x.get('title', '')); self.table_data_ready.emit(table_results)
        if not self.is_running: return

        # --- 3. Запрос дневной статистики (используя Московское время) ---
        try: moscow_tz = pytz.timezone(TARGET_TIMEZONE); today_moscow = datetime.now(moscow_tz).date()
        except pytz.UnknownTimeZoneError: print(f"⚠️ Неизвестный часовой пояс '{TARGET_TIMEZONE}'."); today_moscow = standard_date.today()
        all_api_tasks = []; self.failed_tasks_info = []
        scan_mode_message = ""; dates_to_scan_final = []
        # Определение диапазона дат
        if self.single_date:
             dates_to_scan_final = [self.single_date]; scan_mode_message = f"Сканирование даты: {self.single_date.strftime('%Y-%m-%d')}"; self.status_update.emit(f"Сканирование {self.single_date.strftime('%Y-%m-%d')}...")
        elif self.force_full_scan:
            scan_mode_message = f"ПОЛНОЕ сканирование с {START_DATE_STR}"; self.status_update.emit("ПОЛНОЕ сканирование...");
            try: scan_start_date = datetime.strptime(START_DATE_STR, "%Y-%m-%d").date()
            except ValueError: scan_start_date = self.start_date
            if scan_start_date > today_moscow: print(f"⚠️ Дата сканирования {START_DATE_STR} в будущем."); scan_date_range = []
            else: scan_date_range = [scan_start_date + timedelta(days=x) for x in range((today_moscow - scan_start_date).days + 1)]
            dates_to_scan_final = scan_date_range
        else: # Инкрементальное
            scan_mode_message = f"Инкремент. обновление (~{self.start_date.strftime('%Y-%m-%d')} по {today_moscow.strftime('%Y-%m-%d')})"; self.status_update.emit("Обновление стат...");
        print(f"🗓️ {scan_mode_message} для {len(db_articles)} статей...")
        # Формирование задач API
        for article in db_articles:
            if not self.is_running: return
            path = extract_path_from_url(article["url"]);
            if not path: continue
            if self.single_date:
                dates_to_fetch = dates_to_scan_final
            else:
                first_date = scan_start_date if self.force_full_scan else self.start_date
                dates_to_fetch = get_missing_view_dates(article["id"], first_date, today_moscow)
            for dt in dates_to_fetch: all_api_tasks.append({ "article_id": article["id"], "path": path, "date": dt, "year": dt.year, "month": dt.month, "day": dt.day })
        # Выполнение запросов
        total_tasks = len(all_api_tasks)
        if total_tasks == 0: self.status_update.emit("Нет API запросов для дневной стат."); self.progress_update.emit(90)
        else:
            print(f"⚙️ Всего API запросов для дневной стат.: {total_tasks}"); self.status_update.emit(f"Выполнение {total_tasks} API запросов..."); current_api_call = 0
            saved_count = 0
            # В очереди лишь небольшая группа: остановка не ждёт десятки тысяч задач.
            with ThreadPoolExecutor(max_workers=MAX_API_WORKERS) as executor:
                for offset in range(0, total_tasks, MAX_API_WORKERS):
                    if not self.is_running:
                        break
                    batch = all_api_tasks[offset:offset + MAX_API_WORKERS]
                    futures = {executor.submit(get_views_api_robust, task["path"],
                               task["year"], task["month"], task["day"]): task for task in batch}
                    for future in as_completed(futures):
                        task = futures[future]
                        try:
                            views = future.result()
                        except Exception as exc:
                            print(f"Ошибка запроса: {exc}")
                            views = None
                        if views is not None:
                            row = (task["article_id"], task["date"].isoformat(), views)
                            # Каждый успешный результат сразу фиксируется транзакцией.
                            if insert_or_replace_daily_views([row]) != 1:
                                self.status_update.emit("Ошибка сохранения. Сканирование остановлено.")
                                self.failed_tasks_info.append(f"Ошибка записи: {row[0]} {row[1]}")
                                self.is_running = False
                            else:
                                saved_count += 1
                        else:
                            self.failed_tasks_info.append(f"ID:{task['article_id']} Path:{task['path']} Date:{task['date']}")
                        current_api_call += 1
                        self.progress_update.emit(40 + int(50 * current_api_call / total_tasks))
                        self.status_update.emit(f"Обработано {current_api_call}/{total_tasks}; сохранено: {saved_count}")
            print(f"Сохранено результатов: {saved_count}; ошибок: {len(self.failed_tasks_info)}")
        self.progress_update.emit(95)
        if not self.is_running: return

        # --- 5. Агрегация для графика ---
        self.status_update.emit("Агрегация..."); final_graph_series = get_aggregated_daily_views(self.start_date)
        self.graph_data_ready.emit(final_graph_series)
        if self.failed_tasks_info: self.error_report.emit(self.failed_tasks_info); self.status_update.emit("Завершено с ошибками!")
        else: self.status_update.emit("Готово!")
        self.progress_update.emit(100)

    def stop(self): print("Остановка потока..."); self.is_running = False

# === Диалог выбора даты ===
class DateSelectionDialog(QDialog):
    """Диалоговое окно для выбора одной даты."""
    # (Код этого класса остается БЕЗ ИЗМЕНЕНИЙ)
    def __init__(self, parent=None, initial_date=None):
        super().__init__(parent); self.setWindowTitle("Выбор даты для обновления"); self.layout = QVBoxLayout(self)
        self.calendar = QCalendarWidget(self); self.calendar.setGridVisible(True)
        if initial_date and isinstance(initial_date, QDate): self.calendar.setSelectedDate(initial_date)
        else: self.calendar.setSelectedDate(QDate.currentDate())
        try: moscow_tz = pytz.timezone(TARGET_TIMEZONE); today_moscow_dt = datetime.now(moscow_tz); self.calendar.setMaximumDate(QDate(today_moscow_dt.year, today_moscow_dt.month, today_moscow_dt.day))
        except Exception as e: print(f"Не удалось установить макс. дату: {e}"); self.calendar.setMaximumDate(QDate.currentDate())
        self.layout.addWidget(self.calendar)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self)
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject); self.layout.addWidget(self.buttons)
    def selected_date(self): qdate = self.calendar.selectedDate(); return standard_date(qdate.year(), qdate.month(), qdate.day())

# === ГЛАВНОЕ ОКНО ПРИЛОЖЕНИЯ ===
class StatsViewer(QMainWindow):
    """Основной класс GUI приложения."""
    def __init__(self):
        """Инициализация окна, виджетов."""
        super().__init__()
        self.setWindowTitle("Статистика просмотров статей Telegra.ph"); self.setGeometry(100, 100, 1100, 800)
        self.central_widget = QWidget(); self.setCentralWidget(self.central_widget); self.layout = QVBoxLayout(self.central_widget)
        # --- Таблица ---
        self.table = QTableWidget(); self.table.setColumnCount(4); self.table.setHorizontalHeaderLabels(["Заголовок", "URL Статьи", "Дата добавления", "Всего просмотров"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch); self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents); self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents); self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers); self.table.setSelectionBehavior(QAbstractItemView.SelectItems); self.table.setSelectionMode(QAbstractItemView.ExtendedSelection); self.layout.addWidget(self.table, 3)
        # --- График ---
        self.plot_widget = pg.PlotWidget(axisItems={'bottom': pg.DateAxisItem()})
        self.plot_widget.setLabel('left', 'Сумма просмотров за день'); self.plot_widget.setLabel('bottom', 'Дата'); self.plot_widget.showGrid(x=True, y=True, alpha=GRID_ALPHA)
        self.plot_item = self.plot_widget.getPlotItem()
        self.bar_graph = pg.BarGraphItem(x=[], height=[], width=86400 * BAR_WIDTH_RATIO, brush=pg.mkBrush(color=BAR_COLOR), pen=None)
        self.bar_graph.setZValue(BAR_Z_VALUE); self.plot_item.addItem(self.bar_graph)
        self.text_items = []
        self.layout.addWidget(self.plot_widget, 2)
        # --- Кнопки ---
        button_layout1 = QHBoxLayout()
        self.refresh_button = QPushButton("Обновить статистику (за последние дни)"); self.refresh_button.clicked.connect(self.start_data_loading_incremental)
        self.full_scan_button = QPushButton("ПОЛНОЕ СКАНИРОВАНИЕ статистики"); self.full_scan_button.setStyleSheet("background-color: #FFDAB9;"); self.full_scan_button.clicked.connect(self.start_full_scan)
        button_layout1.addWidget(self.refresh_button); button_layout1.addWidget(self.full_scan_button)
        button_layout2 = QHBoxLayout()
        self.single_date_button = QPushButton("Обновить статистику за ОДНУ ДАТУ"); self.single_date_button.clicked.connect(self.start_single_date_scan)
        button_layout2.addWidget(self.single_date_button); button_layout2.addStretch()
        self.layout.addLayout(button_layout1); self.layout.addLayout(button_layout2)
        # --- Статус бар ---
        self.status_bar = QStatusBar(); self.setStatusBar(self.status_bar)
        self.progress_bar = QProgressBar(self.status_bar); self.progress_bar.setRange(0, 100); self.progress_bar.setValue(0); self.progress_bar.setTextVisible(False); self.progress_bar.hide(); self.status_bar.addPermanentWidget(self.progress_bar)
        # --- Инициализация ---
        self.data_loader_thread = None
        try: self.start_date = datetime.strptime(START_DATE_STR, "%Y-%m-%d").date()
        except ValueError: print(f"❌ Неверный START_DATE_STR '{START_DATE_STR}'."); self.start_date = standard_date.today()
        self.load_initial_display() # Загрузка данных из БД при старте

    def load_initial_display(self):
        """Загружает и отображает данные только из БД при старте, включая общие просмотры."""
        print("\n--- Начальная загрузка из БД ---"); self.status_bar.showMessage("Загрузка из БД...")
        initialize_db(); db_articles = get_articles_from_db()
        initial_table_data = []
        print(f"⏳ Получение общих просмотров из БД для {len(db_articles)} статей...")
        start_time = time.time()
        # --- ИЗМЕНЕНИЕ ЗДЕСЬ: Получаем общие просмотры из БД ---
        total_views_map = {} # Словарь {article_id: total_views}
        try:
             conn = sqlite3.connect(DB_NAME)
             cur = conn.cursor()
             # Одним запросом получаем сумму просмотров для ВСЕХ статей
             cur.execute("SELECT article_id, SUM(views) FROM daily_views GROUP BY article_id")
             for article_id, total_views in cur.fetchall():
                 total_views_map[article_id] = total_views if total_views is not None else 0
             conn.close()
        except sqlite3.Error as e:
             print(f"❌ Ошибка получения сумм просмотров из БД: {e}")
        # ------------------------------------------------------

        # Формируем данные для таблицы
        for article in db_articles:
             article_id = article.get("id")
             # Получаем сумму просмотров из созданного словаря
             total_v = total_views_map.get(article_id, 0) # Если для статьи нет записей, будет 0
             initial_table_data.append({
                 "title": article["title"],
                 "url": article["url"],
                 "published_at": article["published_at"],
                 "total_views": total_v # Отображаем сумму из БД
             })
        end_time = time.time()
        print(f"✓ Общие просмотры из БД получены за {end_time - start_time:.2f} сек.")

        self.update_table(initial_table_data) # Обновляем таблицу
        aggregated_views = get_aggregated_daily_views(self.start_date) # Данные для графика
        self.update_graph(aggregated_views) # Обновляем график
        self.status_bar.showMessage("Данные из БД загружены.", 5000); print("--- Начальная загрузка завершена ---")

    def start_data_loading(self, force_full_scan=False, single_date=None):
        """Слот для запуска фоновой загрузки/обновления данных."""
        if self.data_loader_thread and self.data_loader_thread.isRunning(): QMessageBox.warning(self, "Загрузка", "Процесс уже запущен."); return
        status_msg = "Обновление данных...";
        if force_full_scan: status_msg = "ПОЛНОЕ СКАНИРОВАНИЕ..."
        elif single_date: status_msg = f"Сканирование даты {single_date.strftime('%Y-%m-%d')}..."
        self.refresh_button.setEnabled(False); self.full_scan_button.setEnabled(False); self.single_date_button.setEnabled(False) # Блокируем все кнопки
        self.status_bar.showMessage(status_msg); self.progress_bar.setValue(0); self.progress_bar.show();
        for item in self.text_items: self.plot_item.removeItem(item)
        self.text_items.clear(); self.bar_graph.setOpts(x=[], height=[])
        # Запускаем поток
        self.data_loader_thread = DataLoaderThread(self.start_date, force_full_scan, single_date)
        self.data_loader_thread.table_data_ready.connect(self.update_table)
        self.data_loader_thread.graph_data_ready.connect(self.update_graph)
        self.data_loader_thread.status_update.connect(self.status_bar.showMessage)
        self.data_loader_thread.progress_update.connect(self.update_progress)
        self.data_loader_thread.error_report.connect(self.show_error_report)
        self.data_loader_thread.finished.connect(self.on_loading_finished)
        self.data_loader_thread.start()

    def start_data_loading_incremental(self): self.start_data_loading(force_full_scan=False, single_date=None)
    def start_full_scan(self):
        reply = QMessageBox.question(self, 'Полное сканирование', f"Запустить ПОЛНОЕ сканирование с {START_DATE_STR}?\nЭто займет МНОГО времени.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes: self.start_data_loading(force_full_scan=True, single_date=None)
        else: self.status_bar.showMessage("Полное сканирование отменено.", 3000)
    def start_single_date_scan(self):
        """Открывает диалог выбора даты и запускает сканирование для нее."""
        try: moscow_tz = pytz.timezone(TARGET_TIMEZONE); today_moscow_dt = datetime.now(moscow_tz); initial_qdate = QDate(today_moscow_dt.year, today_moscow_dt.month, today_moscow_dt.day)
        except Exception: initial_qdate = QDate.currentDate()
        dialog = DateSelectionDialog(self, initial_date=initial_qdate)
        if dialog.exec_() == QDialog.Accepted:
            selected_date = dialog.selected_date()
            print(f"Выбрана дата для обновления: {selected_date.strftime('%Y-%m-%d')}")
            self.start_data_loading(force_full_scan=False, single_date=selected_date) # Передаем выбранную дату
        else: self.status_bar.showMessage("Обновление одной даты отменено.", 3000)

    def update_table(self, data):
        """Обновляет таблицу данными, центрируя столбцы."""
        # (Код этой функции БЕЗ ИЗМЕНЕНИЙ)
        self.table.setRowCount(len(data))
        for row, item_data in enumerate(data):
            title = str(item_data.get("title", "")); url = str(item_data.get("url", "")); published_at_raw = item_data.get("published_at"); total_views = item_data.get("total_views")
            published_at_str = ""
            if published_at_raw:
                 try: dt_obj = datetime.fromisoformat(published_at_raw.split('.')[0]); published_at_str = dt_obj.strftime('%Y-%m-%d')
                 except (ValueError, TypeError): published_at_str = str(published_at_raw)
            date_item = QTableWidgetItem(published_at_str); date_item.setTextAlignment(Qt.AlignCenter)
            views_item = QTableWidgetItem()
            if isinstance(total_views, int): views_item.setData(Qt.DisplayRole, total_views)
            else: views_item.setText(str(total_views)) # Отображаем как есть ("-" или "Ошибка")
            views_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 0, QTableWidgetItem(title)); self.table.setItem(row, 1, QTableWidgetItem(url)); self.table.setItem(row, 2, date_item); self.table.setItem(row, 3, views_item)
        self.table.resizeColumnsToContents(); self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch); self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents); self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents); self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents); self.table.setSortingEnabled(True)
        if self.table.rowCount() > 0: self.table.sortByColumn(3, Qt.DescendingOrder)

    def update_graph(self, daily_data):
        """Обновляет гистограмму с отступами и центрированным текстом."""
        # (Код этой функции БЕЗ ИЗМЕНЕНИЙ)
        for item in self.text_items: self.plot_item.removeItem(item)
        self.text_items.clear()
        if daily_data is not None and not daily_data.empty:
            timestamps = daily_data.index.astype('int64') // 10**9
            views = daily_data.values
            bar_width_seconds = 86400 * BAR_WIDTH_RATIO
            self.bar_graph.setOpts(x=timestamps, height=views, width=bar_width_seconds)
            max_view = views.max() if len(views) > 0 and views.max() > 0 else 1
            text_offset = max_view * 0.03;
            if text_offset < 1 : text_offset = 1
            axis = self.plot_item.getAxis('bottom'); ticks = []
            for ts, v in zip(timestamps, views):
                tick_date = datetime.fromtimestamp(ts); tick_label = tick_date.strftime('%d.%m'); ticks.append((ts, tick_label))
                if v > 0:
                    text_str = f"{v:,}".replace(',', ' '); text = pg.TextItem(text=text_str, color=TEXT_COLOR, anchor=(0.5, 1.0))
                    text.setZValue(TEXT_Z_VALUE); font = QFont(); font.setPointSize(TEXT_SIZE_PT); text.setFont(font)
                    text.setPos(ts, v + text_offset); self.plot_item.addItem(text); self.text_items.append(text)
            axis.setTicks([ticks])
            min_ts = timestamps.min() - 86400; max_ts = timestamps.max() + 86400
            self.plot_widget.setXRange(min_ts, max_ts, padding=0); self.plot_widget.enableAutoRange(axis='y', enable=True); y_min, y_max = self.plot_item.vb.viewRange()[1]; self.plot_item.vb.setYRange(0, y_max * 1.15)
        else:
             self.bar_graph.setOpts(x=[], height=[]); axis = self.plot_item.getAxis('bottom'); axis.setTicks(None); self.plot_widget.autoRange()

    def update_progress(self, value): self.progress_bar.setValue(int(value)); self.progress_bar.show() if value < 100 else None
    def on_loading_finished(self):
        """Вызывается при завершении потока."""
        # (Разблокировка кнопок и сброс ссылки на поток без изменений)
        self.refresh_button.setEnabled(True); self.full_scan_button.setEnabled(True); self.single_date_button.setEnabled(True)
        self.progress_bar.hide()
        if self.data_loader_thread:
            if self.data_loader_thread.is_running and not self.data_loader_thread.failed_tasks_info:
                 current_message = self.status_bar.currentMessage()
                 if "ошибка" not in current_message.lower() and "error" not in current_message.lower(): self.status_bar.showMessage("Готово!", 5000)
        self.data_loader_thread = None

    def show_error_report(self, failed_tasks_info):
        """Показывает диалоговое окно с информацией об ошибках."""
        # (Код этой функции БЕЗ ИЗМЕНЕНИЙ)
        if not failed_tasks_info: return
        error_count = len(failed_tasks_info); details = "\n".join(failed_tasks_info[:20])
        if error_count > 20: details += f"\n...и еще {error_count - 20}."
        QMessageBox.warning(self, "Ошибки сканирования", f"Не удалось получить {error_count} зап. ({API_RETRIES} попыток).\n\nПодробности:\n" + details)

    def closeEvent(self, event):
        """Обработчик закрытия окна."""
        # (Код этой функции БЕЗ ИЗМЕНЕНИЙ)
        if self.data_loader_thread and self.data_loader_thread.isRunning():
            reply = QMessageBox.question(self, 'Завершение', 'Идет загрузка. Прервать?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.data_loader_thread.stop()
                if self.data_loader_thread.wait(3000):
                    event.accept()
                else:
                    self.status_bar.showMessage("Завершаются текущие запросы. Результаты сохраняются; закройте окно после завершения.")
                    event.ignore()
            else: event.ignore()
        else: event.accept()

# === ЗАПУСК ПРИЛОЖЕНИЯ ===
if __name__ == "__main__":
    app = QApplication(sys.argv); app.setStyle("Fusion")
    main_window = StatsViewer(); main_window.show(); sys.exit(app.exec_())