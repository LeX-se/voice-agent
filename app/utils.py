"""Вспомогательные функции: логирование, очистка текста, разбиение на фрагменты."""

import logging
import logging.handlers
import re

from . import config


# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------
def setup_logging() -> logging.Logger:
    """Настраивает запись журнала в logs/app.log (rotating, 5 файлов по 2 МБ)."""
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("voice_agent")
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            str(config.APP_LOG_PATH),
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)

    return logger


log = setup_logging()


# ---------------------------------------------------------------------------
# Разбиение текста на фрагменты
# ---------------------------------------------------------------------------
def split_into_chunks(text: str,
                      chunk_size: int = config.CHUNK_SIZE,
                      overlap: int = config.CHUNK_OVERLAP) -> list[str]:
    """Режет текст на фрагменты ~chunk_size символов с перекрытием overlap.

    По возможности режет по границам абзацев (пустых строк).
    Пустые фрагменты не возвращает.
    """
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    chunks: list[str] = []
    current = ""

    def flush(value: str) -> None:
        value = value.strip()
        if value:
            chunks.append(value)

    for para in paragraphs:
        # Абзац сам длиннее целевого размера — режем его по предложениям/символам.
        while len(para) > chunk_size:
            cut = _find_break(para, chunk_size)
            piece, para = para[:cut].strip(), para[cut:].strip()
            # Кусок без продолжения не должен терять хвост предыдущего куска.
            tail_source = piece
            flush(piece)
            # Перекрытие: начинаем следующий кусок с хвоста предыдущего.
            if overlap > 0 and len(tail_source) > overlap:
                para = tail_source[-overlap:] + " " + para

        candidate = (current + "\n\n" + para) if current else para
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            flush(current)
            if overlap > 0 and current:
                current = current.strip()[-overlap:] + "\n\n" + para
            else:
                current = para

    flush(current)

    # Финальная подстраховка: ни один фрагмент не должен быть сильно больше лимита.
    result: list[str] = []
    for ch in chunks:
        while len(ch) > chunk_size + overlap:
            cut = _find_break(ch, chunk_size)
            result.append(ch[:cut].strip())
            ch = ch[cut - overlap if overlap < cut else cut:].strip()
        if ch.strip():
            result.append(ch.strip())

    return [c for c in result if c]


def _find_break(text: str, limit: int) -> int:
    """Ищет удобную точку разрыва не дальше limit символов."""
    segment = text[:limit]
    for pattern in ("\n", ". ", "! ", "? "):
        idx = segment.rfind(pattern)
        if idx > limit // 2:
            return idx + len(pattern)
    idx = segment.rfind(" ")
    if idx > limit // 2:
        return idx + 1
    return limit


# ---------------------------------------------------------------------------
# Очистка текста для озвучки
# ---------------------------------------------------------------------------
_CODE_BLOCK_RE = re.compile(r"```.*?(```|$)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_STRIP_CHARS_RE = re.compile(r"[*_#>|]")


def clean_for_speech(text: str, max_chars: int = config.MAX_SPEECH_CHARS) -> str:
    """Готовит текст к озвучиванию: убирает разметку, схлопывает пробелы,
    обрезает до max_chars по границе слова."""
    if not text:
        return ""
    cleaned = _CODE_BLOCK_RE.sub(" ", text)
    cleaned = _INLINE_CODE_RE.sub(r"\1", cleaned)
    cleaned = _STRIP_CHARS_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if len(cleaned) > max_chars:
        cut = cleaned[:max_chars]
        space_idx = cut.rfind(" ")
        if space_idx > max_chars // 2:
            cut = cut[:space_idx]
        cleaned = cut.rstrip()

    return cleaned


# ---------------------------------------------------------------------------
# Прочее
# ---------------------------------------------------------------------------
def truncate(text: str, max_chars: int) -> str:
    """Обрезает строку до max_chars символов, добавляя многоточие."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def parse_quoted_argument(argument: str) -> str:
    """Убирает обрамляющие кавычки из аргумента команды.

    Поддерживает: /index "C:\\мои заметки" и /index 'C:\\мои заметки'.
    """
    argument = argument.strip()
    if len(argument) >= 2 and argument[0] == argument[-1] and argument[0] in ('"', "'"):
        argument = argument[1:-1]
    return argument.strip()
