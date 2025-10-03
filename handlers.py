import json
import logging
import asyncio
import tempfile
import os
from telethon import events
from telethon.errors.rpcerrorlist import MediaEmptyError, FileReferenceExpiredError
from telethon.tl.types import MessageMediaWebPage, MessageMediaGame
from utils import (
    replace_links_everywhere,
    replace_name_outside_entities,
    update_entities_with_name_and_url,
    extract_links_from_text_and_entities,
    links_allowed_by_whitelist,
)
from logger import content_logger, flood_logger, ad_logger

# Загружаем пары каналов из JSON
with open("channels.json", "r", encoding="utf-8") as f:
    CHANNEL_PAIRS = json.load(f)

PAIR_BY_SOURCE = {pair["source_id"]: pair for pair in CHANNEL_PAIRS}
task_queue = asyncio.Queue()

# Защита от повторной обработки
processed_ids = set()
processed_groups = set()  # grouped_id альбомов


async def try_send_media_with_fallback(client, target_id, media, caption, entities):
    """Отправка одного медиа с fallback через временный файл"""
    try:
        # Проверка валидности media
        if not hasattr(media, "document") and not hasattr(media, "photo"):
            logging.warning(f"Media object is invalid: {repr(media)}")
            return False

        await client.send_file(
            target_id,
            media,
            caption=caption if caption else None,
            formatting_entities=entities if entities else None,
        )
        return True
    except (MediaEmptyError, FileReferenceExpiredError) as e:
        logging.warning(f"send_file initial failed ({type(e).__name__}): {e}; trying download+send fallback")
    except Exception as e:
        logging.warning(f"send_file initial exception: {repr(e)}; trying download+send fallback")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tmp_path = tf.name
        await client.download_media(media, file=tmp_path)
        await client.send_file(
            target_id,
            tmp_path,
            caption=caption if caption else None,
            formatting_entities=entities if entities else None,
        )
        return True
    except Exception as e:
        logging.error(f"Fallback download+send failed: {repr(e)}")
        return False
    finally:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


async def try_send_album_with_fallback(client, target_id, medias, caption, entities):
    """Отправка альбома списком medias; при ошибке — скачиваем каждый и шлём локальными файлами"""
    try:
        await client.send_file(
            target_id,
            medias,
            caption=caption if caption else None,
            formatting_entities=entities if entities else None,
        )
        return True
    except Exception as e:
        logging.warning(f"Album send_file failed: {repr(e)}; trying file-path fallback")

    tmp_paths = []
    try:
        for m in medias:
            if not hasattr(m, "document") and not hasattr(m, "photo"):
                logging.warning(f"Invalid album media item: {repr(m)}")
                continue
            tf = tempfile.NamedTemporaryFile(delete=False)
            tf.close()
            await client.download_media(m, file=tf.name)
            tmp_paths.append(tf.name)

        if not tmp_paths:
            logging.error("Album fallback: no valid media downloaded")
            return False

        await client.send_file(
            target_id,
            tmp_paths,
            caption=caption if caption else None,
            formatting_entities=entities if entities else None,
        )
        return True
    except Exception as e:
        logging.error(f"Album fallback failed: {repr(e)}")
        return False
    finally:
        for p in tmp_paths:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


async def process_message(client, msg, pair):
    """Обработка одиночного сообщения"""
    # Игнорируем сообщения, являющиеся частью альбома — их обработает Album()
    if getattr(msg, "grouped_id", None):
        logging.info(f"Skip single item of album id={msg.grouped_id} msg_id={msg.id}")
        return

    if msg.id in processed_ids:
        logging.info(f"Skipped duplicate msg {msg.id}")
        return
    processed_ids.add(msg.id)

    original_text = msg.message or ""
    entities = msg.entities or []
    target_id = pair["target_id"]

    logging.info(f"Incoming msg chat={msg.chat_id} id={msg.id} from source={pair['source_id']}")
    flood_logger.info(f"Обработка: chat={msg.chat_id} id={msg.id} text={original_text}")

    # --- Проверка white_list ---
    whitelist = pair.get("white_list") or []
    found_links = extract_links_from_text_and_entities(
        original_text,
        entities,
        media=msg.media,
        reply_markup=getattr(msg, "reply_markup", None),
    )
    if found_links:
        allowed_all, disallowed = links_allowed_by_whitelist(found_links, whitelist)
        if not allowed_all:
            for bad in sorted(disallowed):
                ad_logger.info(f"{bad} | source={pair.get('source_id')} | msg={msg.id}")
            return

    mappings = pair.get("link_mappings") or []
    src_name = pair.get("source_name")
    tgt_name = pair.get("target_name")

    text = original_text
    ents = entities

    if mappings:
        text, ents = update_entities_with_name_and_url(text, ents, mappings, src_name, tgt_name)
        text, ents = replace_links_everywhere(text, ents, mappings)

    text = replace_name_outside_entities(text, ents, src_name, tgt_name)

    unsupported_media = (MessageMediaWebPage, MessageMediaGame)
    media = msg.media
    has_supported_media = media is not None and not isinstance(media, unsupported_media)

    if has_supported_media:
        caption = text if text and text.strip() else None
        sent_ok = await try_send_media_with_fallback(client, target_id, media, caption, ents)
        if sent_ok:
            logging.info(f"Forwarded with media {pair['source_id']} -> {pair['target_id']} (msg {msg.id})")
        else:
            safe_text = text if text is not None else ""
            await client.send_message(target_id, safe_text, formatting_entities=ents if ents else None)
            logging.info(f"Fallback: text instead of media {pair['source_id']} -> {pair['target_id']} (msg {msg.id})")
    else:
        safe_text = text if text is not None else ""
        await client.send_message(target_id, safe_text, formatting_entities=ents if ents else None)
        logging.info(f"Forwarded text {pair['source_id']} -> {pair['target_id']} (msg {msg.id})")

    if text:
        content_logger.info(f"Содержимое: {text}")


async def process_album(client, event, pair):
    """Обработка альбома (несколько медиа в одном посте)"""
    group_id = None
    try:
        # Берём grouped_id первого сообщения, если есть
        first = event.messages[0] if event.messages else None
        group_id = getattr(first, "grouped_id", None)
    except Exception:
        group_id = None

    if group_id and group_id in processed_groups:
        logging.info(f"Skipped duplicate album grouped_id={group_id}")
        return
    if group_id:
        processed_groups.add(group_id)

    album_ids = [msg.id for msg in event.messages]
    if any(mid in processed_ids for mid in album_ids):
        logging.info(f"Skipped duplicate album items {album_ids}")
        return
    processed_ids.update(album_ids)

    # Собираем медиа и единую подпись
    medias = []
    caption = None
    entities = None

    for msg in event.messages:
        if msg.media:
            medias.append(msg.media)
        if not caption and (msg.message or "").strip():
            caption = msg.message
            entities = msg.entities

    caption = caption or ""
    ents = entities or []

    flood_logger.info(f"Альбом: chat={event.chat_id} group={group_id} ids={album_ids} caption={caption}")

    # --- Проверка white_list на уровне альбома (агрегация ссылок со всех элементов) ---
    whitelist = pair.get("white_list") or []
    aggregated_links = set()
    for msg in event.messages:
        aggregated_links |= extract_links_from_text_and_entities(
            msg.message or "",
            msg.entities or [],
            media=msg.media,
            reply_markup=getattr(msg, "reply_markup", None),
        )
    if aggregated_links:
        allowed_all, disallowed = links_allowed_by_whitelist(aggregated_links, whitelist)
        if not allowed_all:
            for bad in sorted(disallowed):
                ad_logger.info(f"{bad} | source={pair.get('source_id')} | album_ids={album_ids}")
            return

    # Замены по маппингам
    mappings = pair.get("link_mappings") or []
    src_name = pair.get("source_name")
    tgt_name = pair.get("target_name")

    text = caption
    if mappings:
        text, ents = update_entities_with_name_and_url(text, ents, mappings, src_name, tgt_name)
        text, ents = replace_links_everywhere(text, ents, mappings)

    text = replace_name_outside_entities(text, ents, src_name, tgt_name)

    # Отправка альбома одним постом (с fallback)
    sent_ok = await try_send_album_with_fallback(client, pair["target_id"], medias, text if text.strip() else None, ents if ents else None)
    if sent_ok:
        logging.info(f"Forwarded album {pair['source_id']} -> {pair['target_id']} (count={len(medias)})")
    else:
        logging.exception(f"Error forwarding album {pair['source_id']} -> {pair['target_id']} (group={group_id})")


async def worker(client):
    while True:
        pair, event = await task_queue.get()

        # Защита от переполнения памяти в processed-структурах
        if len(processed_ids) > 100_000:
            processed_ids.clear()
        if len(processed_groups) > 50_000:
            processed_groups.clear()

        try:
            if hasattr(event, "messages"):  # Album
                await process_album(client, event, pair)
            else:
                await process_message(client, event.message, pair)
        except Exception as e:
            logging.exception(f"Error forwarding from {pair.get('source_id')} to {pair.get('target_id')}: {e}")
        finally:
            task_queue.task_done()


def register_handlers(client, worker_count: int = 3):
    @client.on(events.NewMessage())
    async def on_new_message(event):
        pair = PAIR_BY_SOURCE.get(event.chat_id)
        if not pair:
            logging.debug(f"Skipped NewMessage from chat={event.chat_id} id={event.message.id}")
            return

        # Если это элемент альбома — ждём Album() и не обрабатываем как одиночное
        if getattr(event.message, "grouped_id", None):
            logging.info(f"Detected album item grouped_id={event.message.grouped_id} msg_id={event.message.id} — waiting for Album event")
            return

        await task_queue.put((pair, event))
        logging.info(f"Enqueued message from {pair['source_id']} id={event.message.id}")

    @client.on(events.MessageEdited())
    async def on_message_edited(event):
        pair = PAIR_BY_SOURCE.get(event.chat_id)
        if not pair:
            logging.debug(f"Skipped MessageEdited from chat={event.chat_id} id={event.message.id}")
            return

        # Если правка касается элемента альбома — пропускаем (альбом уже отправлен)
        if getattr(event.message, "grouped_id", None):
            logging.info(f"Skip edit of album item grouped_id={event.message.grouped_id} msg_id={event.message.id}")
            return

        await task_queue.put((pair, event))
        logging.info(f"Enqueued edited message from {pair['source_id']} id={event.message.id}")

    @client.on(events.Album())
    async def on_album(event):
        pair = PAIR_BY_SOURCE.get(event.chat_id)
        if not pair:
            logging.debug(f"Skipped Album from chat={event.chat_id}")
            return
        await task_queue.put((pair, event))
        logging.info(f"Enqueued album from {pair['source_id']} with {len(event.messages)} items")

    for _ in range(worker_count):
        client.loop.create_task(worker(client))
