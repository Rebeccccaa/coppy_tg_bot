# Инициализация Telegram-клиента через Telethon
# Используется пользовательская сессия, а не бот

from telethon import TelegramClient
from config import API_ID, API_HASH, SESSION_NAME

# Создаём клиент, который будет авторизован как пользователь
# Добавляем параметры устройства, чтобы сессия выглядела как реальное устройство
client = TelegramClient(
    SESSION_NAME,
    API_ID,
    API_HASH,
    device_model="Windows 10 PC",       # модель устройства
    system_version="10.0",              # версия ОС
    app_version="4.16.30 x64",          # версия Telegram (можно взять с десктопа/мобилки)
    lang_code="ru",                     # язык интерфейса
    system_lang_code="ru-RU"            # системный язык
)
