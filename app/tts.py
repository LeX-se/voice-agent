"""Синтез речи через Windows SAPI5 (pyttsx3).

Стабильность озвучки (раздел 12.1 ТЗ):
- движок инициализируется один раз; пересоздаётся только после реальной
  неудачи (лечит баг pyttsx3 #412 без лишних пауз между ответами);
- все озвучки сериализованы мьютексом (исключает «run loop already started»);
- успех определяется по событию начала реального произнесения, конец фразы —
  по событию окончания; фиксированных «grace»-пауз нет;
- ожидание любого потока ограничено таймаутами из config, зависший движок
  выбрасывается без блокировки интерфейса;
- после серии неудач озвучка отключается до перезапуска, приложение
  продолжает работать в текстовом режиме.
"""

import threading
import time

from . import config
from .utils import clean_for_speech, log


def _cfg(name: str, default):
    """Константа из config с запасным значением (защита от старых конфигов)."""
    return getattr(config, name, default)


class TextToSpeech:
    """Управление голосом: выбор голоса, озвучка, список голосов.

    Если движок недоступен (например, нет SAPI5), объект остаётся
    неработоспособным, но приложение продолжает работать.
    """

    def __init__(self) -> None:
        self.engine = None
        self.available = False
        self.voice_name = ""
        self.voice_id = ""
        self.used_default_voice = False
        self._init_error = ""
        self._pyttsx3 = None
        # Флаг «движок сломан»: ставится после серии зависаний/ошибок озвучки.
        self._broken = False
        # Подряд провалившихся озвучек (после успеха обнуляется).
        self._fail_streak = 0
        # Защита от параллельных озвучек (приводит к «run loop already started»).
        self._speak_lock = threading.Lock()
        self._init_engine()

    # ------------------------------------------------------------------
    # Жизненный цикл движка
    # ------------------------------------------------------------------
    def _clear_engine_cache(self) -> None:
        """Чистит глобальный weakref-кэш pyttsx3 (обход бага issue #398)."""
        engines = getattr(self._pyttsx3, "_activeEngines", None) if self._pyttsx3 else None
        if engines is not None:
            try:
                engines.clear()
            except Exception:  # noqa: BLE001
                pass

    def _dispose_current_engine(self) -> None:
        """Бросает текущий движок, НЕ дожидаясь его внутреннего потока.

        На некоторых системах «спящий» движок sapi5 после runAndWait() не
        реагирует на stop(): попытка дождаться его потока или аккуратно
        собрать объект заново блокирует вызывающий код на десятки секунд —
        визуально это выглядит как зависание интерфейса между ответами.
        Поэтому движок просто бросаем, ссылки обнуляем, кэш _activeEngines
        чистим — новый движок создаётся уже без старого.
        """
        import gc

        engine = self.engine
        self.engine = None
        if engine is None:
            return
        try:
            engine.stop()
        except Exception:  # noqa: BLE001
            pass
        # Поток-«зомби» не должен блокировать нас: принудительно помечаем его
        # остановленным и отпускаем ссылки, сборщик мусора закроет COM сам.
        try:
            driver = getattr(getattr(engine, "proxy", None), "_driver", None)
            if driver is not None:
                for attr, value in (("_looping", False), ("_speaking", False),
                                    ("_stopping", True)):
                    try:
                        setattr(driver, attr, value)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass
        del engine, driver
        self._clear_engine_cache()
        try:
            gc.collect()
        except Exception:  # noqa: BLE001
            pass

    def _configure_engine(self, engine) -> None:
        """Применяет к движку голос и скорость речи."""
        try:
            if self.voice_id:
                engine.setProperty("voice", self.voice_id)
        except Exception:  # noqa: BLE001
            pass
        try:
            engine.setProperty("rate", config.TTS_RATE)
        except Exception:  # noqa: BLE001
            pass

    def _reset_engine(self) -> bool:
        """Создаёт свежий движок pyttsx3 (обход бага issue #412).

        Внутренний поток-обработчик sapi5 после первого runAndWait() может
        «уснуть»: следующие say()/runAndWait() возвращаются мгновенно и молча
        либо зависают навсегда. Обход — создать новый движок; старый
        выбрасывается без ожидания его потока (см. _dispose_current_engine).
        Возвращает True, если движок создан.
        """
        self._dispose_current_engine()
        try:
            engine = self._pyttsx3.init(driverName="sapi5")
            engine.getProperty("voice")  # проверка живости COM
            self.engine = engine
            self._configure_engine(engine)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось пересоздать движок TTS: %s", self._com_error_text(exc))
            return False

    # ------------------------------------------------------------------
    # Озвучка
    # ------------------------------------------------------------------
    def _connect_event(self, event: str, callback) -> bool:
        """Регистрирует обработчик событий движка (started/completed).

        Поддерживает оба механизма подписки разных версий pyttsx3:
        public ``connect`` и прямой доступ к драйверу ``tts.addObserver``.
        Возвращает True, если подписка удалась.
        """
        engine = self.engine
        if engine is None:
            return False
        for register in (
            lambda: engine.connect(event, callback),
            lambda: engine.tts.addObserver(event, callback),
        ):
            try:
                register()
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _speak_once(self, speech_text: str) -> tuple[bool, bool]:
        """Одна попытка озвучки через отдельный поток с таймаутами.

        Возвращает (вызов_завершён_без_ошибки, было_реальное_произнесение).
        Флаг «было произнесение» ставится коллбэком начала произнесения —
        он срабатывает только когда голос действительно заговорил, и потому
        надёжно отличается от молчаливого возврата бага #412. Если коллбэк
        недоступен (неожиданный pyttsx3), считаем озвучку успешной по факту
        штатного завершения runAndWait().
        ВАЖНО: при успехе движок НЕ пересоздаётся (это стоило десятков секунд
        паузы между ответами); сброс движка делается только после неудачи.
        """
        timeout = _cfg("TTS_SPEAK_TIMEOUT", 90)
        start_wait = _cfg("TTS_START_WAIT_SECONDS", 5)
        end_wait_limit = _cfg("TTS_END_WAIT_SECONDS", 1.5)
        zombie_join = _cfg("TTS_ZOMBIE_JOIN_SECONDS", 2)

        done = threading.Event()          # worker завершился
        spoke_ev = threading.Event()      # начало реального произнесения
        finished_ev = threading.Event()   # окончание произнесения (completed)
        result = {"ok": False, "spoke": False}

        engine = self.engine

        def on_started(*_args, **_kwargs) -> None:
            result["spoke"] = True
            spoke_ev.set()

        def on_completed(*_args, **_kwargs) -> None:
            finished_ev.set()

        can_detect = self._connect_event("started", on_started)
        if can_detect:
            # Событие окончания фразы нужно, чтобы не «спать» вслепую:
            # при баге #412 runAndWait() возвращается мгновенно, хотя голос
            # ещё говорит, а при нормальном движке возврат сам означает конец.
            self._connect_event("completed", on_completed)

        def worker() -> None:
            try:
                engine.say(speech_text)
                engine.runAndWait()
                result["ok"] = True
            except Exception as exc:  # noqa: BLE001
                # Исключение вроде RuntimeError("run loop already started") —
                # НЕ провал озвучки: SAPI продолжает говорить, событие started
                # подтвердит произнесение.
                log.debug("Исключение в потоке озвучки (обрабатывается): %s",
                          self._com_error_text(exc))
            finally:
                done.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        deadline = time.monotonic() + timeout

        # Ждём ЛИБО фактического начала произнесения (событие started —
        # достоверный признак успеха), либо штатного завершения потока.
        # Окно ожидания старта ограничено start_wait: за это время живой SAPI
        # обязательно успевает послать событие начала; если задержка больше —
        # сработает общий дедлайн timeout.
        start_deadline = time.monotonic() + min(start_wait, timeout)
        while not done.is_set() and not spoke_ev.is_set():
            remaining = start_deadline - time.monotonic()
            if remaining <= 0:
                break
            done.wait(min(remaining, 0.1))
            if spoke_ev.wait(0.05):
                break

        if not done.is_set() and not spoke_ev.is_set():
            # Зависание без звука: гасим движок, чтобы поток-«зомби» завершился.
            try:
                engine.stop()
            except Exception:  # noqa: BLE001
                pass
            thread.join(timeout=zombie_join)
            return False, result["spoke"]

        # Поток завершился, а события started ещё нет: даём короткое окно на
        # «опоздавший» коллбэк (на реальных системах он приходит с задержкой).
        if not spoke_ev.is_set() and can_detect:
            spoke_ev.wait(0.3)

        # Голос заговорил, а поток ещё не вернулся (баг #412: runAndWait()
        # «не замечает» окончания) — ждём реальное окончание произнесения по
        # событию completed, затем короткое окно на возврат потока.
        # Фиксированных длинных пауз нет: completed → возврат сразу.
        if result["spoke"] and not done.is_set():
            words = max(len(speech_text.split()), 1)
            est = min(words / 5.5 + 1.0, timeout)  # страховка: оценка длины фразы
            end_deadline = time.monotonic() + max(start_wait, est)
            while not finished_ev.is_set() and not done.is_set():
                remaining = end_deadline - time.monotonic()
                if remaining <= 0:
                    break
                finished_ev.wait(min(remaining, 0.25))
            done.wait(timeout=end_wait_limit)

        ok = result["ok"] or result["spoke"]
        spoke = result["spoke"] or (ok and not can_detect)
        if ok and spoke:
            # Движок живой и отработал — останавливаем очередь сразу, не
            # дожидаясь завершения внутреннего потока sapi5 (join стоил
            # десятков секунд паузы между ответами).
            try:
                engine.stop()
            except Exception:  # noqa: BLE001
                pass
        return ok, spoke

    def say(self, text: str) -> bool:
        """Озвучивает текст (полный ответ при этом уже показан в консоли).

        Экономим время между ответами: сначала пробуем текущий живой движок —
        если он реально произносит фразу (событие started), возврат мгновенный.
        Только при молчаливом возврате/зависании бага pyttsx3 #412 движок
        пересоздаётся и делается вторая попытка. Успех определяется по событию
        начала реального произнесения, а не по времени возврата.
        Возвращает True, если озвучка выполнена.
        """
        if not self.available or self._broken:
            return False
        speech_text = clean_for_speech(text, config.MAX_SPEECH_CHARS)
        if not speech_text:
            return False

        attempts = int(_cfg("TTS_MAX_RECOVERY_PER_PHRASE", 2))
        # Последовательные озвучки: параллельный запуск двух фраз через один
        # движок вызывает «run loop already started» и лишние пересоздания.
        with self._speak_lock:
            for attempt in range(1, max(attempts, 1) + 1):
                if attempt > 1 or self.engine is None:
                    # Первая попытка использует уже созданный движок; новый
                    # движок создаётся только после реальной неудачи (это
                    # лечит баг #412 без лишней паузы на каждый ответ).
                    if not self._reset_engine():
                        self._broken = True
                        return False
                ok, spoke = self._speak_once(speech_text)
                if spoke:
                    # Голос реально звучал (событие started) — озвучка удалась,
                    # даже если runAndWait() вернул исключение «run loop already
                    # started»: это безобидный симптом бага #412, движок живой.
                    self._fail_streak = 0
                    return True
                if ok:
                    log.warning("runAndWait() завершился без произнесания "
                                "(баг pyttsx3 #412), попытка %d — пересоздаю движок.",
                                attempt)
                else:
                    log.warning("Озвучка не состоялась (попытка %d) — "
                                "пересоздаю движок TTS.", attempt)
            # Зависания могут накапливать «зомби»-движки SAPI и тормозить
            # систему: после серии неудачных фраз подряд отключаем озвучку
            # до перезапуска.
            self._fail_streak += 1
            broken_after = int(_cfg("TTS_MAX_BROKEN_STREAK", 2))
            if self._fail_streak >= broken_after:
                self._broken = True
                log.warning("Озвучка отключена после %d неудач подряд "
                            "(перезапустите приложение для повторной попытки).",
                            self._fail_streak)
            print("Не удалось озвучить ответ (печатная версия выше).")
            return False

    # ------------------------------------------------------------------
    # Диагностика и инициализация
    # ------------------------------------------------------------------
    @staticmethod
    def _com_error_text(exc: BaseException) -> str:
        """Извлекает человекочитаемое описание из COMError (ctypes/pycaw)."""
        parts = []
        for arg in getattr(exc, "args", ()) or ():
            if isinstance(arg, str) and arg.strip():
                parts.append(arg.strip())
            elif isinstance(arg, tuple):
                parts.extend(str(x) for x in arg if isinstance(x, str) and x.strip())
        text = " | ".join(dict.fromkeys(parts))
        return text or str(exc)

    def _diagnose_sapi(self) -> str:
        """Прямая проверка SAPI через comtypes: возвращает текст ошибки или ''."""
        try:
            import comtypes.client  # noqa: F401
            sp = comtypes.client.CreateObject("SAPI.SpVoice")
            voices = sp.GetVoices()
            count = int(voices.Count)
            if count == 0:
                return ("В Windows не установлено ни одного голоса TTS. "
                        "Установите голос: Параметры → Время и язык → Речь → "
                        "Добавить голоса (например, Microsoft Irina Desktop).")
            return ""
        except Exception as exc:  # noqa: BLE001
            return self._com_error_text(exc)

    def _init_engine(self) -> None:
        """Инициализация pyttsx3/sapi5 с повтором и понятной диагностикой."""
        try:
            import pyttsx3  # локальный импорт: модуль может отсутствовать на других ОС
        except Exception as exc:  # noqa: BLE001
            self._init_error = str(exc)
            log.error("Модуль pyttsx3 не установлен: %s", exc)
            return

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                # Известный баг pyttsx3 (issue #398): глобальный weakref-кэш
                # _activeEngines при повторных init() в одном процессе иногда
                # содержит нерабочую ссылку -> KeyError/COMError. Чистим кэш
                # перед каждой попыткой.
                self._pyttsx3 = pyttsx3
                self._clear_engine_cache()
                engine = pyttsx3.init(driverName="sapi5")
                # Проверка работоспособности движка: getProperty("voice") бросает
                # COMError (-2147201006), если SAPI не может выбрать голос.
                engine.getProperty("voice")
                self.engine = engine
                self._configure_engine(engine)
                self._select_voice()
                self.available = True
                return
            except Exception as exc:  # noqa: BLE001 — любой сбой TTS не роняет приложение
                last_exc = exc
                log.warning("Попытка %d: инициализация TTS не удалась: %s",
                            attempt + 1, self._com_error_text(exc))
                self._dispose_current_engine()
                # Если причина — отсутствие установленного голоса (hr 0x80040212),
                # повторные попытки бессмысленны: выходим сразу с понятной подсказкой.
                if self._looks_like_no_voices(exc):
                    break
                time.sleep(0.4)

        sapi_hint = self._diagnose_sapi()
        detail = self._com_error_text(last_exc) if last_exc else ""
        combined = "; ".join(p for p in (detail, sapi_hint) if p)
        self._init_error = combined
        log.error("Синтез речи недоступен (%s). Голосовые ответы будут выводиться текстом.",
                  combined)
        print("Голосовой синтез недоступен:", combined or "причина неизвестна")
        print("Ответы продолжат печататься текстом; для голоса установите пакет речи")
        print("Windows: Параметры → Время и язык → Речь → Добавить голоса.")
        self.engine = None
        self.available = False

    @staticmethod
    def _looks_like_no_voices(exc: BaseException) -> bool:
        """COMError -2147201006 (0x80040212) = SAPI_E_NONAMES — нет ни одного голоса."""
        args = getattr(exc, "args", ()) or ()
        if args and isinstance(args[0], int) and args[0] in (-2147201006, 0x80040212):
            return True
        text = str(exc).lower()
        return "0x80040212" in text or "-2147201006" in text

    # ------------------------------------------------------------------
    # Голоса
    # ------------------------------------------------------------------
    def _iter_voices(self) -> list:
        if self.engine is None:
            return []
        try:
            return list(self.engine.getProperty("voices"))
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось получить список голосов: %s", exc)
            return []

    def _select_voice(self) -> None:
        """Выбирает голос по подстроке: Maxim → IVONA → Russian/ru → default."""
        voices = self._iter_voices()

        def name_of(v) -> str:
            try:
                return (v.name or "") if hasattr(v, "name") else ""
            except Exception:  # noqa: BLE001
                return ""

        def id_of(v) -> str:
            try:
                return (v.id or "") if hasattr(v, "id") else ""
            except Exception:  # noqa: BLE001
                return ""

        hint = (getattr(config, "TTS_VOICE_HINT", "") or "").lower()
        priorities = [p for p in (hint, "maxim", "ivona", "russian", "ru-") if p]
        for prio in dict.fromkeys(priorities):
            for voice in voices:
                haystack = (name_of(voice) + " " + id_of(voice)).lower()
                if prio in haystack:
                    self._apply_voice(voice)
                    log.info("Выбран голос TTS: %s (%s)", name_of(voice), id_of(voice))
                    return

        # Подходящий голос не найден — используем голос по умолчанию.
        if voices:
            self._apply_voice(voices[0])
            self.used_default_voice = True
            print("Подходящий русский голос (Maxim/IVONA) не найден — "
                  "используется голос по умолчанию.")
            log.warning("Русский голос не найден, используется голос по умолчанию")
        else:
            self.used_default_voice = True
            print("Голоса для озвучки не найдены. Озвучивание отключено.")
            log.warning("Список голосов SAPI5 пуст")

    def _apply_voice(self, voice) -> None:
        try:
            self.engine.setProperty("voice", voice.id)
            self.voice_id = voice.id
            self.voice_name = getattr(voice, "name", "") or voice.id
            # Порядок важен: rate применяется ПОСЛЕ выбора голоса (скорость
            # хранится относительно конкретного голоса).
            self.engine.setProperty("rate", config.TTS_RATE)
        except Exception as exc:  # noqa: BLE001
            log.exception("Не удалось установить голос: %s", exc)

    def list_voices(self) -> list[tuple[str, str]]:
        """Возвращает список голосов (имя, ID) для команды /voices."""
        result = []
        for voice in self._iter_voices():
            name = getattr(voice, "name", "") or "(без имени)"
            vid = getattr(voice, "id", "") or "(без ID)"
            result.append((name, vid))
        return result

    @property
    def error(self) -> str:
        return self._init_error
