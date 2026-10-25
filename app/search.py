"""База знаний: индексация текстовых файлов и полнотекстовый поиск."""

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import config, storage
from .utils import log, split_into_chunks, truncate


SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown"}

# Стоп-слова русского (и базовые английские) языка для поиска.
STOP_WORDS = {
    "и", "в", "во", "на", "не", "что", "как", "про", "это", "а", "я", "ты", "он",
    "она", "оно", "мы", "вы", "они", "мне", "меня", "тебе", "тебя", "ему", "ей",
    "нас", "вас", "них", "ней", "нем", "под", "над", "для", "от", "до", "по",
    "из", "за", "об", "о", "при", "без", "через", "через", "или", "но", "да",
    "если", "то", "тоже", "также", "будто", "который", "которая", "которые",
    "какой", "какая", "какие", "где", "когда", "почему", "зачем", "кто", "чем",
    "тем", "всем", "всё", "все", "много", "мало", "очень", "уже", "ещё", "еще",
    "бы", "был", "была", "были", "есть", "будет", "буду", "сам", "сама", "сами",
    "мой", "моя", "моё", "мое", "твой", "твоя", "наш", "ваш", "этот", "эта",
    "эти", "тот", "та", "те", "такой", "таки", "just", "the", "a", "an", "and",
    "or", "is", "are", "was", "were", "to", "of", "in", "on", "at", "by",
}

_WORD_RE = re.compile(r"[\w]+", re.UNICODE)


@dataclass
class SearchHit:
    """Найденный фрагмент текста с указанием источника."""
    source: str
    chunk: str
    score: float = 0.0


# ---------------------------------------------------------------------------
# Индексация
# ---------------------------------------------------------------------------
def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_text(path: Path) -> str | None:
    """Читает файл в UTF-8 (с допуском ошибок). Нечитаемый файл — None."""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        log.warning("Не удалось прочитать файл %s: %s", path, exc)
        return None


def index_file(conn: sqlite3.Connection, path: Path, use_fts: bool) -> str:
    """Индексирует один файл.

    Возвращает статус: "indexed", "skipped" (не изменился), "too_large",
    "unsupported", "unreadable".
    """
    path = path.resolve()
    source = str(path)

    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return "unsupported"

    try:
        stat = path.stat()
    except OSError as exc:
        log.warning("Файл недоступен: %s (%s)", path, exc)
        return "unreadable"

    max_bytes = config.MAX_INDEX_FILE_MB * 1024 * 1024
    if stat.st_size > max_bytes:
        print(f"  Пропущен слишком большой файл: {path.name} "
              f"(>{config.MAX_INDEX_FILE_MB} МБ)")
        return "too_large"

    hash_value = _file_hash(path)
    meta = storage.get_doc_meta(conn, source)
    if meta and meta["mtime"] == int(stat.st_mtime) and meta["size"] == stat.st_size \
            and meta["hash"] == hash_value:
        return "skipped"

    text = _read_text(path)
    if text is None:
        return "unreadable"

    chunks = split_into_chunks(text)
    if not chunks:
        return "skipped"

    # Повторная индексация: старые фрагменты удаляются внутри upsert_doc_meta.
    doc_id = storage.upsert_doc_meta(
        conn, source=source, mtime=int(stat.st_mtime),
        size=stat.st_size, hash_value=hash_value,
    )
    storage.add_chunks(conn, doc_id=doc_id, source=source,
                       chunks=chunks, use_fts=use_fts)
    log.info("Проиндексирован файл %s: %d фрагментов", source, len(chunks))
    return "indexed"


def index_directory(conn: sqlite3.Connection, directory: Path,
                    use_fts: bool) -> dict:
    """Рекурсивно индексирует поддерживаемые файлы папки.

    Возвращает сводку {"indexed": n, "skipped": n, "failed": n}.
    """
    summary = {"indexed": 0, "skipped": 0, "failed": 0}
    directory = directory.resolve()

    if not directory.is_dir():
        print(f"Папка не найдена: {directory}")
        return summary

    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        try:
            status = index_file(conn, path, use_fts)
        except sqlite3.Error as exc:
            log.exception("Ошибка индексации %s: %s", path, exc)
            print(f"  Ошибка при индексации {path.name}: см. журнал logs/app.log")
            status = "failed"
        if status == "indexed":
            summary["indexed"] += 1
            print(f"  Проиндексирован: {path.name}")
        elif status == "skipped":
            summary["skipped"] += 1
        elif status in ("too_large", "unsupported"):
            summary["skipped"] += 1
        else:
            summary["failed"] += 1
            print(f"  Файл пропущен (нечитаемый): {path.name}")

    return summary


def add_manual_note(conn: sqlite3.Connection, text: str, use_fts: bool) -> str:
    """Сохраняет ручную заметку как источник manual:<timestamp>.

    Возвращает имя источника.
    """
    source = storage.manual_note_source()
    chunks = split_into_chunks(text)
    if not chunks:
        return ""
    doc_id = storage.upsert_doc_meta(
        conn, source=source, mtime=0, size=len(text), hash_value="",
    )
    storage.add_chunks(conn, doc_id=doc_id, source=source,
                       chunks=chunks, use_fts=use_fts)
    log.info("Добавлена ручная заметка %s", source)
    return source


# ---------------------------------------------------------------------------
# Поиск
# ---------------------------------------------------------------------------
def tokenize(query: str) -> list[str]:
    """Токенизирует запрос: буквы/цифры, нижний регистр, без стоп-слов."""
    tokens = [t.lower() for t in _WORD_RE.findall(query)]
    return [t for t in tokens if t not in STOP_WORDS and len(t) > 1]


def _fts_match_query(tokens: list[str]) -> str:
    """Собирает FTS5-запрос вида "токен1" OR "токен2" с экранированием кавычек."""
    parts = []
    for token in tokens:
        safe = token.replace('"', '""')
        # Превфиксный поиск уместен для коротких токенов.
        parts.append(f'"{safe}"')
    return " OR ".join(parts)


def _like_escape(value: str) -> str:
    """Экранирует спецсимволы LIKE: %, _, [."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_docs(conn: sqlite3.Connection, query: str, limit: int = config.SEARCH_TOP_K,
                use_fts: bool = True) -> list[SearchHit]:
    """Ищет релевантные фрагменты по запросу.

    Пустой список — не ошибка (возможно, база знаний пуста).
    """
    tokens = tokenize(query)
    if not tokens:
        return []

    hits: list[SearchHit] = []
    try:
        if use_fts:
            match = _fts_match_query(tokens)
            rows = conn.execute(
                "SELECT source, chunk, bm25(docs) AS rank"
                " FROM docs WHERE docs MATCH ? ORDER BY rank LIMIT ?",
                (match, limit),
            ).fetchall()
            for row in rows:
                hits.append(SearchHit(source=row["source"], chunk=row["chunk"],
                                      score=-row["rank"]))
        else:
            # Запасной вариант: поиск LIKE с экранированием спецсимволов.
            scored: list[SearchHit] = []
            rows = conn.execute("SELECT source, chunk FROM docs").fetchall()
            for row in rows:
                chunk_lower = row["chunk"].lower()
                score = 0
                for token in tokens:
                    pattern = _like_escape(token)
                    score += chunk_lower.count(pattern)
                if score > 0:
                    scored.append(SearchHit(source=row["source"],
                                            chunk=row["chunk"], score=float(score)))
            scored.sort(key=lambda h: h.score, reverse=True)
            hits = scored[:limit]
    except sqlite3.Error as exc:
        log.exception("Ошибка поиска: %s", exc)
        print("Ошибка поиска по базе знаний: подробности в журнале logs/app.log.")
        return []

    # Обрезка фрагментов до MAX_SNIPPET_CHARS.
    for hit in hits:
        hit.chunk = truncate(hit.chunk, config.MAX_SNIPPET_CHARS)

    return hits
