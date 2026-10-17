from pathlib import Path
import os
BASE_DIR = Path(__file__).resolve().parent
from telegraph import Telegraph

# 1. Создаём аккаунт
telegraph = Telegraph()
account = telegraph.create_account(short_name='Jonny Poster')

# 2. Сохраняем токен в файл
with open(BASE_DIR / "telegraph_token.txt", "w") as f:
    f.write(account["access_token"])

# 3. Выводим данные
print("✅ Токен успешно создан и сохранён в telegraph_token.txt")
print("🔑 access_token:", account["access_token"])
print("🔗 auth_url (ссылка для редактирования/удаления статей):")
print(account["auth_url"])
