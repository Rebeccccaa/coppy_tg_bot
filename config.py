from dotenv import load_dotenv
import os

load_dotenv()  # Загружаем переменные из .env

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
# BOT_TOKEN = os.getenv('BOT_TOKEN')

SESSION_NAME = os.getenv('SESSION_NAME', 'copier_session') # Файл для сохранения сессии

