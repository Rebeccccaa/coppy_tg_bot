import logging

# Основной лог
logging.basicConfig(
    filename='bot.log',
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    encoding='utf-8'
)

# Контент лог
content_logger = logging.getLogger('content_logger')
content_handler = logging.FileHandler('content.log', encoding='utf-8')
content_handler.setFormatter(logging.Formatter('[%(asctime)s] %(message)s'))
content_logger.addHandler(content_handler)
content_logger.setLevel(logging.INFO)

# Flood лог
flood_logger = logging.getLogger('flood_logger')
flood_handler = logging.FileHandler('flood.log', encoding='utf-8')
flood_handler.setFormatter(logging.Formatter('[%(asctime)s] %(message)s'))
flood_logger.addHandler(flood_handler)
flood_logger.setLevel(logging.INFO)

# Новый логгер для рекламы
ad_logger = logging.getLogger("ads")
ad_logger.setLevel(logging.INFO)
ad_handler = logging.FileHandler("ads.log", encoding="utf-8")
ad_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s"))
ad_logger.addHandler(ad_handler)