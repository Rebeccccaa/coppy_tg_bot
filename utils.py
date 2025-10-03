import re
import logging
from urllib.parse import urlparse
from telethon.tl.types import MessageEntityTextUrl, MessageMediaWebPage

# Регулярка для поиска "голых" ссылок
URL_RE = re.compile(r"https?://[^\s\]\)]+", flags=re.IGNORECASE)


def normalize_link(link: str) -> str:
    """Нормализует ссылку: убирает хвостовой /, query/fragment и приводит к нижнему регистру"""
    if not link:
        return ""
    p = urlparse(link)
    return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/").lower()


def replace_allowed_link_single(text: str, source_link: str, target_link: str) -> str:
    """Заменяет все вхождения source_link в тексте на target_link"""
    if not text:
        return text
    src_base = re.escape(source_link.rstrip("/"))
    tgt = target_link.rstrip("/")
    pattern = re.compile(rf"{src_base}/?(?:\?[^\s#]*)?", flags=re.IGNORECASE)
    return pattern.sub(tgt, text)


def update_entity_urls_single(entities: list, source_link: str, target_link: str) -> list:
    """Обновляет url внутри MessageEntityTextUrl"""
    if not entities:
        return entities
    src_norm = normalize_link(source_link)
    tgt = target_link.rstrip("/")
    updated = []
    for ent in entities:
        if isinstance(ent, MessageEntityTextUrl):
            ent_url = getattr(ent, "url", "") or ""
            if normalize_link(ent_url) == src_norm:
                new_ent = MessageEntityTextUrl(offset=ent.offset, length=ent.length, url=tgt)
                updated.append(new_ent)
            else:
                updated.append(ent)
        else:
            updated.append(ent)
    return updated


def replace_links_everywhere(text: str, entities: list, mappings: list) -> tuple[str, list]:
    """Применяет все маппинги ссылок к тексту и entities"""
    new_text = text or ""
    new_entities = list(entities or [])
    for m in mappings or []:
        src = m.get("src")
        tgt = m.get("tgt")
        if not src or not tgt:
            continue
        new_text = replace_allowed_link_single(new_text, src, tgt)
        new_entities = update_entity_urls_single(new_entities, src, tgt)
    return new_text, new_entities


def replace_name_outside_entities(message_text: str, entities: list, src_name: str, tgt_name: str) -> str:
    """Заменяет source_name → target_name в plain‑тексте, игнорируя гиперссылки"""
    if not message_text or not src_name or src_name == tgt_name:
        return message_text

    protected_ranges = []
    for ent in entities or []:
        if isinstance(ent, MessageEntityTextUrl):
            protected_ranges.append((ent.offset, ent.offset + ent.length))

    def overlaps_any(start: int, end: int) -> bool:
        for a, b in protected_ranges:
            if not (end <= a or start >= b):
                return True
        return False

    result = []
    i = 0
    L = len(message_text)
    s_len = len(src_name)
    while i < L:
        if message_text.startswith(src_name, i):
            start = i
            end = i + s_len
            if overlaps_any(start, end):
                result.append(message_text[i:end])
            else:
                result.append(tgt_name)
            i = end
        else:
            result.append(message_text[i])
            i += 1
    return "".join(result)


def update_entities_with_name_and_url(message_text: str, entities: list, mappings: list,
                                      src_name: str = None, tgt_name: str = None) -> tuple[str, list]:
    """Обновляет гиперссылки: меняет url по mappings и видимый текст по src_name/tgt_name"""
    if not entities:
        return message_text, entities

    map_by_norm = {}
    for m in mappings or []:
        s = m.get("src")
        t = m.get("tgt")
        if s and t:
            map_by_norm[normalize_link(s)] = t.rstrip("/")

    sorted_entities = sorted(entities, key=lambda e: getattr(e, "offset", 0))
    updated_entities = []
    shift = 0

    for ent in sorted_entities:
        if isinstance(ent, MessageEntityTextUrl):
            start = ent.offset + shift
            end = start + ent.length
            link_text = message_text[start:end] if 0 <= start <= len(message_text) else ""

            ent_url = getattr(ent, "url", "") or ""
            ent_url_norm = normalize_link(ent_url)

            new_url = map_by_norm.get(ent_url_norm, ent_url)

            new_text = link_text
            if src_name and tgt_name and link_text == src_name:
                new_text = tgt_name

            if new_text != link_text or new_url != ent_url:
                logging.info(f"Гиперссылка заменена: [{link_text}]({ent_url}) → [{new_text}]({new_url})")

            if new_text != link_text:
                before = message_text[:start]
                after = message_text[end:]
                message_text = before + new_text + after
                delta = len(new_text) - len(link_text)
                new_ent = MessageEntityTextUrl(offset=ent.offset + shift, length=len(new_text), url=new_url)
                updated_entities.append(new_ent)
                shift += delta
            else:
                new_ent = MessageEntityTextUrl(offset=ent.offset + shift, length=ent.length, url=new_url)
                updated_entities.append(new_ent)
        else:
            try:
                if hasattr(ent, "offset"):
                    ent.offset = ent.offset + shift
                updated_entities.append(ent)
            except Exception:
                updated_entities.append(ent)

    return message_text, updated_entities


def extract_links_from_text_and_entities(text: str, entities: list, media=None, reply_markup=None) -> set:
    """Возвращает множество нормализованных ссылок из текста, entities, web‑preview и кнопок"""
    found = set()

    # plain text
    if text:
        for m in URL_RE.finditer(text):
            url = m.group(0).rstrip(".,;:!?")
            norm = normalize_link(url)
            if norm:
                found.add(norm)

    # entities
    for ent in entities or []:
        if isinstance(ent, MessageEntityTextUrl):
            url = getattr(ent, "url", "") or ""
            if url:
                norm = normalize_link(url)
                if norm:
                    found.add(norm)

    # web preview
    if isinstance(media, MessageMediaWebPage):
        webpage = getattr(media, "webpage", None)
        wp_url = getattr(webpage, "url", None) if webpage else None
        if wp_url:
            norm = normalize_link(wp_url)
            if norm:
                found.add(norm)

    # кнопки
    try:
        if reply_markup and hasattr(reply_markup, "rows"):
            for row in reply_markup.rows:
                for btn in getattr(row, "buttons", []):
                    btn_url = getattr(btn, "url", None)
                    if btn_url:
                        norm = normalize_link(btn_url)
                        if norm:
                            found.add(norm)
    except Exception:
        pass

    return found


def links_allowed_by_whitelist(links: set, whitelist: list) -> tuple[bool, set]:
    """Проверяет ссылки против whitelist"""
    if not links:
        return True, set()

    wl_norm = {normalize_link(u) for u in (whitelist or []) if u}
    disallowed = {l for l in links if l not in wl_norm}
    return (len(disallowed) == 0), disallowed
