"""Хранилище: база SQLite (диалоги, метаданные документов, полнотекстовый индекс)."""

import json
import sqlite3
from datetime import datetime, timezone

from . import config
from .utils import log


# ---------------------------------------------------------------------------
# Соединение
# ---------------------------------------------------------------------------
def get_connection() -> sqlite3.Connection:
    """Открывает соединение с базой данных, создавая при необходимости папку."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> bool:
    """Создаёт таблицы. Возвращает True, если доступен FTS5-индекс."""
    use_fts = False
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dialogues (
                id INTEGER PRIMARY KEY,
                ts TEXT DEFAULT CURRENT_TIMESTAMP,
                user_text TEXT,
                agent_text TEXT,
                sources TEXT,
                model TEXT,
                duration_ms INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS docs_meta (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                mtime INTEGER,
                size INTEGER,
                hash TEXT,
                indexed_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        use_fts = _fts5_supported(conn)
        if use_fts:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(
                    source UNINDEXED,
                    chunk,
                    doc_id UNINDEXED
                )
                """
            )
        else:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS docs (
                    id INTEGER PRIMARY KEY,
                    source TEXT,
                    chunk TEXT,
                    doc_id INTEGER
                )
                """
            )
        conn.commit()
    except sqlite3.Error as exc:
        log.exception("Ошибка инициализации базы данных: %s", exc)
        raise

    return use_fts


def _fts5_supported(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE TABLE _fts5_probe (c TEXT)")
        conn.execute("DROP TABLE _fts5_probe")
        conn.execute(
            "CREATE VIRTUAL TABLE _fts5_probe2 USING fts5(c)"
        )
        conn.execute("DROP TABLE _fts5_probe2")
        return True
    except sqlite3.OperationalError as exc:
        log.warning("FTS5 недоступен (%s), будет использован поиск LIKE", exc)
        return False


# ---------------------------------------------------------------------------
# Диалоги
# ---------------------------------------------------------------------------
def save_dialogue(conn: sqlite3.Connection, *, user_text: str, agent_text: str,
                  sources: list[str], model: str, duration_ms: int) -> None:
    """Сохраняет один успешный обмен репликами."""
    try:
        conn.execute(
            "INSERT INTO dialogues (user_text, agent_text, sources, model, duration_ms)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                user_text,
                agent_text,
                json.dumps(sources, ensure_ascii=False),
                model,
                duration_ms,
            ),
        )
        conn.commit()
    except sqlite3.Error as exc:
        log.exception("Не удалось сохранить диалог: %s", exc)


def recent_dialogues(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Возвращает последние N записей диалога (новые сверху)."""
    try:
        rows = conn.execute(
            "SELECT id, ts, user_text, agent_text, sources, model, duration_ms"
            " FROM dialogues ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["sources"] = json.loads(item.get("sources") or "[]")
            except (json.JSONDecodeError, TypeError):
                item["sources"] = []
            result.append(item)
        return result
    except sqlite3.Error as exc:
        log.exception("Не удалось прочитать историю: %s", exc)
        return []


def dialogue_count(conn: sqlite3.Connection) -> int:
    try:
        return conn.execute("SELECT COUNT(*) FROM dialogues").fetchone()[0]
    except sqlite3.Error as exc:
        log.exception("Не удалось посчитать диалоги: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Метаданные документов
# ---------------------------------------------------------------------------
def get_doc_meta(conn: sqlite3.Connection, source: str) -> dict | None:
    """Возвращает метаданные проиндексированного файла по пути-источнику."""
    try:
        row = conn.execute(
            "SELECT id, mtime, size, hash FROM docs_meta WHERE source = ?",
            (source,),
        ).fetchone()
        return dict(row) if row else None
    except sqlite3.Error as exc:
        log.exception("Ошибка чтения метаданных документа: %s", exc)
        return None


def upsert_doc_meta(conn: sqlite3.Connection, *, source: str, mtime: int,
                    size: int, hash_value: str) -> int:
    """Создаёт или обновляет запись о файле, возвращает id записи."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        existing = conn.execute(
            "SELECT id FROM docs_meta WHERE source = ?", (source,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE docs_meta SET mtime=?, size=?, hash=?, indexed_at=? WHERE id=?",
                (mtime, size, hash_value, now, existing["id"]),
            )
            conn.execute("DELETE FROM docs WHERE doc_id = ?", (existing["id"],))
            doc_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO docs_meta (source, mtime, size, hash, indexed_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (source, mtime, size, hash_value, now),
            )
            doc_id = cur.lastrowid
        conn.commit()
        return doc_id
    except sqlite3.Error as exc:
        log.exception("Ошибка сохранения метаданных документа: %s", exc)
        raise


def add_chunks(conn: sqlite3.Connection, *, doc_id: int, source: str,
               chunks: list[str], use_fts: bool) -> None:
    """Добавляет фрагменты текста в индекс."""
    try:
        if use_fts:
            conn.executemany(
                "INSERT INTO docs (source, chunk, doc_id) VALUES (?, ?, ?)",
                [(source, ch, doc_id) for ch in chunks],
            )
        else:
            conn.executemany(
                "INSERT INTO docs (source, chunk, doc_id) VALUES (?, ?, ?)",
                [(source, ch, doc_id) for ch in chunks],
            )
        conn.commit()
    except sqlite3.Error as exc:
        log.exception("Ошибка добавления фрагментов: %s", exc)
        raise


def clear_knowledge(conn: sqlite3.Connection) -> None:
    """Полностью очищает базу знаний (для /reindex)."""
    try:
        conn.execute("DELETE FROM docs")
        conn.execute("DELETE FROM docs_meta")
        conn.commit()
    except sqlite3.Error as exc:
        log.exception("Ошибка очистки базы знаний: %s", exc)
        raise


def count_documents(conn: sqlite3.Connection) -> int:
    """Число проиндексированных файлов."""
    try:
        return conn.execute("SELECT COUNT(*) FROM docs_meta").fetchone()[0]
    except sqlite3.Error as exc:
        log.exception("Ошибка подсчёта документов: %s", exc)
        return 0


def count_chunks(conn: sqlite3.Connection) -> int:
    try:
        return conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    except sqlite3.Error as exc:
        log.exception("Ошибка подсчёта фрагментов: %s", exc)
        return 0


def manual_note_source() -> str:
    """Имя источника для ручной заметки: manual:<timestamp>."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"manual:{stamp}"
