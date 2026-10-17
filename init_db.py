from pathlib import Path
import os
BASE_DIR = Path(__file__).resolve().parent
import sqlite3

DB_NAME = str(BASE_DIR / 'articles.db')

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content_html TEXT,
            text_preview TEXT,
            telegraph_url TEXT NOT NULL,
            published_at TEXT NOT NULL,
            raw_links TEXT,
            short_links TEXT
        )
    """)

    conn.commit()
    conn.close()
    print(f"✅ База данных «{DB_NAME}» и таблица «articles» успешно созданы.")

if __name__ == "__main__":
    init_db()
