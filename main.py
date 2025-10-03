from client import client
from handlers import register_handlers
import logger

def main():
    register_handlers(client)  # Регистрация обработчиков
    client.start()
    print("Bot is running...")
    client.run_until_disconnected()

if __name__ == '__main__':
    main()
