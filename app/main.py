"""Главный модуль голосового агента: статус, команды, главный цикл."""

import os
import sqlite3
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    # Прямой запуск файла ("python app\\main.py" или "python C:\\...\\app\\main.py")
    # ломает относительные импорты. Добавляем корень проекта в sys.path и
    # импортируем пакет целиком — так main.py работает при любом способе запуска.
    _ROOT = str(Path(__file__).resolve().parent.parent)
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from app import config, llm, search, storage  # type: ignore[no-redef]
    from app.stt import SpeechToText  # type: ignore[no-redef]
    from app.tts import TextToSpeech  # type: ignore[no-redef]
    from app.utils import log, parse_quoted_argument  # type: ignore[no-redef]
else:
    from . import config, llm, search, storage
    from .stt import SpeechToText
    from .tts import TextToSpeech
    from .utils import log, parse_quoted_argument


HELP_TEXT = """
Команды:
  /help                 — этот список команд
  /status               — состояние компонентов (Ollama, модель, Vosk, микрофон, TTS)
  /voices               — список доступных голосов Windows
  /say <текст>          — произнести текст выбранным голосом
  /voice                — начать запись с микрофона
  /index <папка>        — рекурсивно проиндексировать файлы из папки
                          (пример: /index "C:\\мои заметки")
  /file <файл>          — добавить в базу знаний один файл
  /reindex              — очистить базу знаний и заново проиндексировать knowledge
  /add <текст>          — сохранить ручную заметку
  /history [N]          — показать последние N записей диалога (по умолчанию 10)
  /exit, /quit, выход   — завершить работу

Как пользоваться:
  • Наберите вопрос и нажмите Enter — ответ придёт текстом и голосом.
  • Просто нажмите Enter (пустая строка) или /voice — можно говорить.
  • Все данные остаются на этом компьютере.
"""


class VoiceAgentApp:
    """Состояние и логика приложения."""

    def __init__(self) -> None:
        self.conn: sqlite3.Connection | None = None
        self.use_fts = True
        self.ollama_available = False
        self.model_available = False
        self.stt = SpeechToText()
        self.tts: TextToSpeech | None = None
        self.history: list[dict] = []  # история текущей сессии для LLM

    # ------------------------------------------------------------------
    # Инициализация (раздел 7 ТЗ)
    # ------------------------------------------------------------------
    def startup(self) -> None:
        print("=" * 60)
        print("Локальный голосовой агент «Voice Agent»")
        print("=" * 60)

        # 1. Создать отсутствующие папки.
        for directory in (config.DATA_DIR, config.LOGS_DIR, config.KNOWLEDGE_DIR):
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                print(f"Не удалось создать папку {directory}: {exc}")

        # 2. Логирование уже настроено при импорте utils.
        log.info("Запуск приложения, корень проекта: %s", config.PROJECT_ROOT)

        # 3. SQLite.
        try:
            self.conn = storage.get_connection()
            self.use_fts = storage.init_db(self.conn)
        except sqlite3.Error:
            print("Ошибка базы данных: подробности в журнале logs/app.log. "
                  "Приложение продолжит работу в ограниченном режиме.")
            log.exception("Инициализация SQLite не удалась")
            self.conn = None

        # 4–5. Ollama.
        print("Проверка Ollama...")
        self.ollama_available = llm.check_ollama_available()
        if self.ollama_available:
            self.model_available = llm.check_model_available()
        if not self.ollama_available:
            print("Ollama не отвечает. Проверьте, что она запущена "
                  "(при необходимости выполните: ollama serve).")
        elif not self.model_available:
            print(f"Модель {config.OLLAMA_MODEL} не найдена. "
                  f"Выполните: ollama pull {config.OLLAMA_MODEL}")

        # 6. Vosk.
        if self.stt.vosk_available and self.stt.model_exists_on_disk():
            self.stt.load_model()
        else:
            self.stt.print_missing_model_instructions()

        # 7. TTS.
        print("Инициализация синтеза речи...")
        self.tts = TextToSpeech()
        if self.tts.available:
            print(f"Голос озвучки: {self.tts.voice_name or '(по умолчанию)'}")

        # 8. Автоиндексация knowledge, если база пуста.
        if self.conn is not None:
            try:
                docs_count = storage.count_documents(self.conn)
                has_files = any(
                    p.is_file() and p.suffix.lower() in search.SUPPORTED_EXTENSIONS
                    for p in config.KNOWLEDGE_DIR.rglob("*")
                )
                if has_files and docs_count == 0:
                    print("Индексация папки knowledge...")
                    summary = search.index_directory(
                        self.conn, config.KNOWLEDGE_DIR, self.use_fts)
                    print(f"  Проиндексировано файлов: {summary['indexed']}, "
                          f"пропущено без изменений: {summary['skipped']}.")
            except sqlite3.Error:
                log.exception("Автоиндексация не удалась")

        # 9. Блок статуса и список команд.
        self.show_status(brief=False)
        print(HELP_TEXT)

        # 10. Приветствие голосом.
        if self.tts and self.tts.available:
            self.tts.say("Голосовой агент готов к работе.")

    # ------------------------------------------------------------------
    # Статус
    # ------------------------------------------------------------------
    def show_status(self, brief: bool = True) -> None:
        header = "Состояние компонентов:" if not brief else "Статус:"
        print(header)

        mic_ok = "-"
        if self.stt.model_loaded:
            try:
                import sounddevice as sd
                devices = sd.query_devices()
                inputs = [d for d in devices if d.get("max_input_channels", 0) > 0]
                mic_ok = f"да ({len(inputs)} устр.)" if inputs else "нет"
            except Exception:  # noqa: BLE001
                mic_ok = "проверка невозможна"
        else:
            mic_ok = "голосовой ввод отключён (нет модели Vosk)"

        docs = storage.count_documents(self.conn) if self.conn is not None else 0
        chunks = storage.count_chunks(self.conn) if self.conn is not None else 0

        print(f"  Ollama:        {'доступна' if self.ollama_available else 'НЕ ОТВЕЧАЕТ'}")
        print(f"  Модель {config.OLLAMA_MODEL}: "
              f"{'найдена' if self.model_available else 'НЕ НАЙДЕНА'}")
        print(f"  Vosk:          {'загружен' if self.stt.model_loaded else 'не загружен'}")
        print(f"  Микрофон:      {mic_ok}")
        tts_state = "нет"
        if self.tts and self.tts.available:
            tts_state = self.tts.voice_name or "доступен"
            if self.tts.used_default_voice:
                tts_state += " (голос по умолчанию)"
        print(f"  Голос TTS:     {tts_state}")
        print(f"  База знаний:   документов {docs}, фрагментов {chunks}"
              + ("" if self.use_fts else " (поиск LIKE, FTS5 недоступен)"))
        print(f"  Файл БД:       {config.DB_PATH}")

    # ------------------------------------------------------------------
    # Обработка вопроса (поиск → LLM → печать → сохранение → озвучка)
    # ------------------------------------------------------------------
    def process_question(self, question: str) -> None:
        question = question.strip()
        if not question:
            return

        hits: list[search.SearchHit] = []
        if self.conn is not None:
            try:
                hits = search.search_docs(self.conn, question,
                                          limit=config.SEARCH_TOP_K,
                                          use_fts=self.use_fts)
            except sqlite3.Error:
                log.exception("Ошибка поиска при обработке вопроса")

        if hits:
            print(f"Найдено фрагментов в базе знаний: {len(hits)}")

        messages = llm.build_messages(question, hits, self.history)

        print("Ожидание ответа модели...")
        started = time.monotonic()
        try:
            answer = llm.ask(messages)
        except llm.LlmError as exc:
            print(f"\n{exc}")
            return
        duration_ms = int((time.monotonic() - started) * 1000)

        sources = [hit.source for hit in hits]

        print("\nОтвет:")
        print(answer)
        if sources:
            print("\nИсточники:")
            for src in sources:
                print(f"  • {src}")

        # Сохранение диалога и обновление истории сессии.
        if self.conn is not None:
            storage.save_dialogue(
                self.conn,
                user_text=question,
                agent_text=answer,
                sources=sources,
                model=config.OLLAMA_MODEL,
                duration_ms=duration_ms,
            )
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": answer})
        max_items = config.HISTORY_MESSAGES * 2
        if len(self.history) > max_items:
            self.history = self.history[-max_items:]

        if self.tts and self.tts.available:
            self.tts.say(answer)

    # ------------------------------------------------------------------
    # Команды (раздел 14 ТЗ)
    # ------------------------------------------------------------------
    def handle_command(self, line: str) -> bool:
        """Обрабатывает строку, начинающуюся с '/' (или 'выход').

        Возвращает False, если приложение нужно завершить.
        """
        parts = line.strip().split(maxsplit=1)
        command = parts[0].lower()
        argument = parts[1] if len(parts) > 1 else ""

        if command in ("/exit", "/quit", "выход"):
            print("До свидания!")
            return False

        if command == "/help":
            print(HELP_TEXT)
        elif command == "/status":
            self.refresh_availability()
            self.show_status(brief=True)
        elif command == "/voices":
            self._cmd_voices()
        elif command == "/say":
            self._cmd_say(argument)
        elif command == "/voice":
            text = self.stt.listen()
            if text:
                self.process_question(text)
        elif command == "/index":
            self._cmd_index(argument)
        elif command == "/file":
            self._cmd_file(argument)
        elif command == "/reindex":
            self._cmd_reindex()
        elif command == "/add":
            self._cmd_add(argument)
        elif command == "/history":
            self._cmd_history(argument)
        else:
            print(f"Неизвестная команда: {command}. Введите /help для списка команд.")
        return True

    def refresh_availability(self) -> None:
        """Повторно проверяет Ollama (для /status)."""
        self.ollama_available = llm.check_ollama_available()
        self.model_available = llm.check_model_available() if self.ollama_available else False

    def _cmd_voices(self) -> None:
        if self.tts is None or not self.tts.available:
            print("Синтез речи недоступен, список голосов получить нельзя.")
            return
        voices = self.tts.list_voices()
        if not voices:
            print("Голоса не найдены.")
            return
        print(f"Доступные голоса ({len(voices)}):")
        for name, vid in voices:
            marker = " ← выбран" if vid == self.tts.voice_id else ""
            print(f"  • {name}\n    ID: {vid}{marker}")

    def _cmd_say(self, text: str) -> None:
        if not text:
            print("Использование: /say <текст>")
            return
        if self.tts is None or not self.tts.available:
            print("Синтез речи недоступен, озвучить текст нельзя.")
            return
        self.tts.say(text)

    def _require_db(self) -> bool:
        if self.conn is None:
            print("База данных недоступна, команда не выполнена. "
                  "Подробности в журнале logs/app.log.")
            return False
        return True

    def _cmd_index(self, argument: str) -> None:
        if not self._require_db():
            return
        if not argument:
            print("Использование: /index <папка>   (путь можно взять в кавычки)")
            return
        directory = Path(parse_quoted_argument(argument))
        if not directory.is_absolute():
            directory = (config.PROJECT_ROOT / directory).resolve()
        summary = search.index_directory(self.conn, directory, self.use_fts)
        print(f"Итог: новых/обновлённых файлов {summary['indexed']}, "
              f"пропущено {summary['skipped']}, ошибок {summary['failed']}.")

    def _cmd_file(self, argument: str) -> None:
        if not self._require_db():
            return
        if not argument:
            print("Использование: /file <файл>   (путь можно взять в кавычки)")
            return
        path = Path(parse_quoted_argument(argument))
        if not path.is_absolute():
            path = (config.PROJECT_ROOT / path).resolve()
        if not path.is_file():
            print(f"Файл не найден: {path}")
            return
        status = search.index_file(self.conn, path, self.use_fts)
        messages = {
            "indexed": "Файл проиндексирован.",
            "skipped": "Файл уже проиндексирован ранее и не изменился.",
            "too_large": f"Файл больше {config.MAX_INDEX_FILE_MB} МБ — пропущен.",
            "unsupported": "Формат файла не поддерживается (нужны .txt/.md/.markdown).",
            "unreadable": "Файл нечитаемый — пропущен.",
        }
        print(messages.get(status, "Готово."))

    def _cmd_reindex(self) -> None:
        if not self._require_db():
            return
        storage.clear_knowledge(self.conn)
        summary = search.index_directory(self.conn, config.KNOWLEDGE_DIR, self.use_fts)
        print(f"База знаний пересоздана. Проиндексировано файлов: {summary['indexed']}.")

    def _cmd_add(self, argument: str) -> None:
        if not self._require_db():
            return
        if not argument:
            print("Использование: /add <текст заметки>")
            return
        source = search.add_manual_note(self.conn, argument, self.use_fts)
        if source:
            print(f"Заметка сохранена как источник: {source}")
        else:
            print("Текст заметки пуст — ничего не сохранено.")

    def _cmd_history(self, argument: str) -> None:
        if not self._require_db():
            return
        count = 10
        if argument:
            try:
                count = max(1, min(100, int(argument)))
            except ValueError:
                print("N должно быть целым числом, например: /history 5")
                return
        rows = storage.recent_dialogues(self.conn, count)
        if not rows:
            print("История пуста.")
            return
        print(f"Последние записей: {len(rows)} (свежие сверху)")
        for row in reversed(rows):
            print("-" * 50)
            print(f"[{row['ts']}] Вопрос: {row['user_text']}")
            print(f"Ответ: {row['agent_text']}")
            if row["sources"]:
                print("Источники: " + "; ".join(row["sources"]))

    # ------------------------------------------------------------------
    # Главный цикл (раздел 15 ТЗ)
    # ------------------------------------------------------------------
    def run(self) -> None:
        # Самовосстановление: работаем от корня проекта (там, где папка app),
        # независимо от текущей папки запуска; гарантируем наличие data/logs/knowledge.
        try:
            os.chdir(str(config.PROJECT_ROOT))
        except OSError:
            pass
        for d in (config.DATA_DIR, config.LOGS_DIR, config.KNOWLEDGE_DIR):
            try:
                Path(d).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        self.startup()
        running = True
        while running:
            try:
                print()
                line = input("Нажмите Enter для записи голоса или введите текст/команду: ")
            except (KeyboardInterrupt, EOFError):
                print("\nДо свидания!")
                break

            stripped = line.strip()
            if stripped == "":
                # Пустая строка → голосовой ввод; пустой результат ничего не отправляет.
                text = self.stt.listen()
                if text:
                    self.process_question(text)
                continue

            if stripped.lower() == "выход":
                running = self.handle_command(stripped)
                continue

            if stripped.startswith("/"):
                running = self.handle_command(stripped)
                continue

            self.process_question(stripped)

        self.shutdown()

    def shutdown(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except sqlite3.Error:
                log.exception("Ошибка закрытия соединения с БД")
        log.info("Приложение завершено")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # Режим проверки: python app\main.py "Как называется проект?" —
    # отвечает на один вопрос и завершается. Удобно для диагностики.
    one_shot = len(args) == 1 and not args[0].startswith("/")
    app = VoiceAgentApp()
    try:
        if one_shot:
            app.startup()
            app.process_question(args[0])
            app.shutdown()
            return 0
        app.run()
    except Exception as exc:  # noqa: BLE001 — последний рубеж: логируем, не показываем трейс
        log.exception("Непредвиденная ошибка: %s", exc)
        print("Произошла непредвиденная ошибка. Подробности в журнале logs/app.log.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
