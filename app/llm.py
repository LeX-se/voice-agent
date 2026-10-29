"""Работа с локальной моделью через Ollama API (только localhost)."""

import socket

import requests

from . import config
from .search import SearchHit
from .utils import log


def _no_proxy_session() -> requests.Session:
    """Сессия, игнорирующая системные прокси.

    На Windows переменные HTTP(S)_PROXY или реестровые настройки прокси
    иногда направляют запросы к localhost через прокси-сервер, который
    их «вешает» на таймауты операционной системы (минуты вместо секунд).
    trust_env=False запрещает requests читать переменные окружения прокси.
    """
    session = requests.Session()
    session.trust_env = False
    return session


session = _no_proxy_session()


SYSTEM_PROMPT = (
    "Ты локальный голосовой ассистент на Windows.\n"
    "Отвечай по-русски, кратко и ясно, чтобы ответ можно было озвучить.\n"
    "Если дан контекст из локальной базы знаний, опирайся на него и упоминай источники.\n"
    "Если информации нет, честно скажи, что не нашел точных данных.\n"
    "Не выдумывай факты. Не используй markdown-таблицы и блоки кода."
)


class LlmError(Exception):
    """Понятная пользователю ошибка работы с LLM (текст уже на русском)."""


# ---------------------------------------------------------------------------
# Проверки доступности
# ---------------------------------------------------------------------------
def _tcp_ping(host: str, port: int, timeout: float) -> bool:
    """Быстрая проверка: принимает ли host:port TCP-соединение.

    Не поднимает прокси и не ждёт HTTP-ответа — самый надёжный способ
    убедиться, что Ollama вообще слушает порт.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_ollama_available() -> bool:
    """Проверяет, отвечает ли Ollama (TCP-пинг порта + GET /api/tags)."""
    # «localhost» может резолвиться в ::1, а Ollama слушать только 127.0.0.1
    # (или наоборот) — поэтому пингуем адрес напрямую с коротким таймаутом.
    host = "127.0.0.1"
    port = 11434
    try:
        from urllib.parse import urlparse
        parsed = urlparse(config.OLLAMA_BASE_URL)
        if parsed.hostname in ("localhost", "127.0.0.1", "::1"):
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 11434
    except ValueError:
        pass
    reachable = _tcp_ping("127.0.0.1", port, config.OLLAMA_PING_TIMEOUT_SECONDS) or (
        host != "127.0.0.1" and _tcp_ping(host, port,
                                          config.OLLAMA_PING_TIMEOUT_SECONDS))
    if not reachable:
        log.warning("Порт Ollama (%s:%s) не отвечает за %s с",
                    host, port, config.OLLAMA_PING_TIMEOUT_SECONDS)
        return False
    # Быстрый HTTP-запрос без системных прокси (session.trust_env=False).
    try:
        response = session.get(f"{config.OLLAMA_BASE_URL}/api/tags",
                               timeout=config.OLLAMA_PING_TIMEOUT_SECONDS)
        return response.status_code == 200
    except requests.RequestException as exc:
        log.warning("Ollama недоступна: %s", exc)
        return False


def check_model_available() -> bool:
    """Проверяет наличие нужной модели в списке моделей Ollama."""
    try:
        response = session.get(f"{config.OLLAMA_BASE_URL}/api/tags",
                               timeout=config.OLLAMA_PING_TIMEOUT_SECONDS * 2)
        response.raise_for_status()
        models = response.json().get("models", [])
        names = [m.get("name", "") for m in models]
        # Совпадение точное или по префиксу без тега (qwen2.5:3b / qwen2.5:3b-instruct...)
        for name in names:
            if name == config.OLLAMA_MODEL or name.startswith(config.OLLAMA_MODEL):
                return True
        return False
    except (requests.RequestException, ValueError) as exc:
        log.warning("Не удалось получить список моделей: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Прогрев модели
# ---------------------------------------------------------------------------
def warmup_model() -> bool:
    """Загружает модель в память Ollama коротким запросом.

    Первая загрузка gguf-модели с диска занимает десятки секунд; если не
    делать прогрев, эту паузу пользователь видит при первом же вопросе как
    «зависание». keepalive в payload удерживает модель загруженной между
    запросами, поэтому последующие ответы приходят быстро.
    Возвращает True, если прогрев прошёл успешно (ошибки не фатальны).
    """
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": [{"role": "user", "content": "ок"}],
        "stream": False,
        "keepalive": config.LLM_KEEPALIVE,
        "options": {"temperature": 0.0, "num_predict": 1},
    }
    try:
        response = session.post(f"{config.OLLAMA_BASE_URL}/api/chat",
                                json=payload, timeout=config.LLM_TIMEOUT_SECONDS)
        return response.status_code == 200
    except requests.RequestException as exc:
        log.warning("Прогрев модели не выполнен: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Формирование сообщений
# ---------------------------------------------------------------------------
def build_context_message(hits: list[SearchHit]) -> str:
    """Собирает system-сообщение с найденными фрагментами и путями источников."""
    parts = [
        "Контекст из локальной базы знаний. Опирайся на эти фрагменты "
        "и указывай источник в ответе."
    ]
    for i, hit in enumerate(hits, start=1):
        parts.append(f"Фрагмент {i}. Источник: {hit.source}\n{hit.chunk}")
    return "\n\n".join(parts)


def build_messages(question: str, hits: list[SearchHit],
                   history: list[dict]) -> list[dict]:
    """Собирает список сообщений для /api/chat.

    Порядок: system-инструкция, (system с контекстом), история сессии, текущий вопрос.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if hits:
        messages.append({"role": "system", "content": build_context_message(hits)})
    messages.extend(history)
    messages.append({"role": "user", "content": question})
    return messages


# ---------------------------------------------------------------------------
# Запрос к модели
# ---------------------------------------------------------------------------
def ask(messages: list[dict]) -> str:
    """Отправляет сообщения в Ollama и возвращает текст ответа.

    Бросает LlmError с понятным русским сообщением при проблемах.
    """
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        # keepalive: Ollama держит модель в оперативной памяти между
        # запросами. Без него после простоя каждый следующий вопрос
        # начинается с повторной загрузки модели с диска — это и даёт
        # задержку до 60-120 секунд, похожую на зависание интерфейса.
        "keepalive": config.LLM_KEEPALIVE,
        "options": {"temperature": config.LLM_TEMPERATURE},
    }

    try:
        response = session.post(
            f"{config.OLLAMA_BASE_URL}/api/chat",
            json=payload,
            timeout=config.LLM_TIMEOUT_SECONDS,
        )
    except requests.ConnectionError as exc:
        log.warning("Соединение с Ollama не установлено: %s", exc)
        raise LlmError(
            "Ollama не отвечает. Проверьте, что Ollama запущена; "
            "при необходимости выполните в отдельном окне команду: ollama serve"
        ) from exc
    except requests.Timeout as exc:
        log.warning("Превышен таймаут ожидания ответа Ollama")
        raise LlmError(
            "Модель не успела ответить за отведённое время. "
            "Попробуйте ещё раз или задайте более короткий вопрос."
        ) from exc
    except requests.RequestException as exc:
        log.exception("Ошибка запроса к Ollama: %s", exc)
        raise LlmError(
            "Не удалось обратиться к Ollama. Подробности в журнале logs/app.log."
        ) from exc

    if response.status_code == 404:
        raise LlmError(
            f"Модель {config.OLLAMA_MODEL} не найдена в Ollama. "
            f"Выполните в отдельном окне команду: ollama pull {config.OLLAMA_MODEL}"
        )
    if response.status_code != 200:
        log.error("Ollama вернула HTTP %s: %s", response.status_code, response.text[:500])
        raise LlmError(
            f"Ollama вернула ошибку HTTP {response.status_code}. "
            "Подробности в журнале logs/app.log."
        )

    try:
        data = response.json()
        answer = (data.get("message") or {}).get("content", "").strip()
    except ValueError as exc:
        log.exception("Некорректный JSON от Ollama: %s", exc)
        raise LlmError(
            "Ollama вернула некорректный ответ. Подробности в журнале logs/app.log."
        ) from exc

    if not answer:
        raise LlmError("Модель вернула пустой ответ. Попробуйте переформулировать вопрос.")

    return answer
