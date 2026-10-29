"""Синтез речи через Windows SAPI5 (pyttsx3). Движок инициализируется один раз."""

import threading
import time

from . import config
from .utils import clean_for_speech, log


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
        # Флаг «движок сломан»: ставится после зависания/ошибки озвучки.
        self._broken = False
        # Подряд провалившихся озвучек (после восстановления обнуляется).
        self._fail_streak = 0
        # Защита от параллельных озвучек (приводит к «run loop already started»).
        self._speak_lock = threading.Lock()
        self._init_engine()

    # ------------------------------------------------------------------
    def _dispose_current_engine(self) -> None:
        """Бросает текущий движок и отпускает ссылки, НЕ дожидаясь его потока.

        На некоторых системах «спящий» движок sapi5 после runAndWait() не
        реагирует на stop(): попытка дождаться его внутреннего потока или
        собрать объект заново блокирует вызывающий код на десятки секунд —
        визуально это выглядит как зависание интерфейса между ответами.
        Поэтому движок просто бросаем (с гарантированным потолком ожидания),
        ссылки обнуляем, а кэш _activeEngines чистим — новый движок создаётся
        уже без старого.
        """
        import gc

        engine = self.engine
        self.engine = None
        if engine is None:
            return
        join_s = float(getattr(config, "TTS_ZOMBIE_JOIN_SECONDS", 2))
        try:
            engine.stop()
        except Exception:  # noqa: BLE001
            pass
        # Поток-«зомби» не должен блокировать нас: принудительно помечаем его
        # остановленным и отпускаем ссылки, сборщик мусора закроет COM сам.
        try:
            proxy = getattr(engine, "proxy", None)
            tts_iter = getattr(proxy, "_tts", None) if proxy is not None else None
            for attr in ("_iterating", "_queue"):
                obj = getattr(tts_iter, attr, None) if tts_iter is not None else None
                if obj is not None:
                    try:
                        obj.set(False) if attr == "_iterating" else obj.queue.clear()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass
        del engine, tts_iter, proxy
        engines = getattr(self._pyttsx3, "_activeEngines", None) if self._pyttsx3 else None
        if engines is not None:
            try:
                engines.clear()
            except Exception:  # noqa: BLE001
                pass
        try:
            gc.collect()
        except Exception:  # noqa: BLE001
            pass

    def _reset_engine(self) -> bool:
        """Создаёт свежий движок pyttsx3 (обход бага issue #412).

        Внутренний поток-обработчик sapi5 после первого runAndWait() может
        «уснуть»: следующие say()/runAndWait() возвращаются мгновенно и молча
        либо зависают навсегда. Обход — каждый раз создавать новый движок;
        старый выбрасывается без ожидания его потока (см. _dispose_current_engine).
        Возвращает True, если движок живой.
        """
        self._dispose_current_engine()
        try:
            engine = self._pyttsx3.init(driverName="sapi5")
            engine.getProperty("voice")
            engine.setProperty("rate", config.TTS_RATE)
            if self.voice_id:
                try:
                    engine.setProperty("voice", self.voice_id)
                except Exception:  # noqa: BLE001
                    pass
            self.engine = engine
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось пересоздать движок TTS: %s", self._com_error_text(exc))
            return False

    def _speak_once(self, speech_text: str) -> tuple[bool, bool]:
        """Одна попытка озвучки через отдельный поток с таймаутом.

        Возвращает (вызов_завершён_без_ошибки, было_реальное_произнесение).
        Флаг «было произнесение» ставится коллбэком onStart движка SAPI —
        он срабатывает только когда голос действительно заговорил, и потому
        надёжно отличается от молчаливого возврата бага #412. Если коллбэк
        недоступен (неожиданный pyttsx3), считаем озвучку успешной по факту
        штатного завершения runAndWait().
        ВАЖНО: при успехе движок НЕ пересоздаётся (это стоило десятков секунд
        паузы между ответами); сброс движка делается только после неудачи.
        """
        import threading

        timeout = getattr(config, "TTS_SPEAK_TIMEOUT", 90)
        done = threading.Event()
        result = {"ok": False, "spoke": False}
        can_detect = {"yes": False}

        engine = self.engine
        for attempt_attr in ("connect", "tts.addObserver"):
            try:
                if attempt_attr == "connect":
                    engine.connect("started", lambda name: result.__setitem__("spoke", True))
                else:
                    engine.tts.addObserver(
                        "started", lambda name: result.__setitem__("spoke", True))
                can_detect["yes"] = True
                break
            except Exception:  # noqa: BLE001
                continue

        def worker(text: str = speech_text, ev=done, spoke=None,
                   finished=None) -> None:
            try:
                engine.say(text)
                engine.runAndWait()
                result["ok"] = True
            except Exception as exc:  # noqa: BLE001
                # Исключение вроде RuntimeError("run loop already started") —
                # НЕ провал озвучки: SAPI продолжает говорить, событие started
                # подтвердит произнесение.
                log.debug("Исключение в потоке озвучки (обрабатывается): %s",
                          self._com_error_text(exc))
            # При баге #412 runAndWait() возвращается мгновенно, хотя движок
            # ещё говорит. Не даём потоку завершиться раньше реального
            # окончания произнесения: сначала дождаться события started, затем
            # «простоять» остаток фразы (grace), чтобы следующая попытка не
            # упёрлась в «run loop already started» и не пересоздала движок.
            if spoke is not None and finished is not None:
                spoke.wait(timeout=getattr(config, "TTS_SPEAK_TIMEOUT", 90))
                # Оцениваем реальную длительность фразы (скорость речи ~5.5
                # слов/сек при TTS_RATE 175) и ждём её окончания с запасом,
                # но НЕ фиксированные десятки секунд — иначе озвучка коротких
                # ответов искусственно замедлялась бы до 30 с.
                words = max(len(text.split()), 1)
                est = words / 5.5 + 1.0
                if not finished.is_set():
                    finished.wait(timeout=min(est, getattr(config, "TTS_SPEAK_FINISH_GRACE", 120)))
            ev.set()

        spoke_ev = threading.Event()
        finished_ev = threading.Event()
        if can_detect["yes"]:
            def on_started(name=None, _ev=spoke_ev) -> None:
                result["spoke"] = True
                _ev.set()
            # pyttsx3 генерирует событие 'completed' по окончании фразы —
            # регистрируем сразу, чтобы worker мог дождаться реального конца.
            def on_completed(_name=None, _f=finished_ev) -> None:
                _f.set()
            for ev_name, cb in (("started", on_started), ("completed", on_completed)):
                for attempt_attr in ("connect", "tts.addObserver"):
                    try:
                        if attempt_attr == "connect":
                            engine.connect(ev_name, cb)
                        else:
                            engine.tts.addObserver(ev_name, cb)
                        break
                    except Exception:  # noqa: BLE001
                        continue
            for attempt_attr in ("connect", "tts.addObserver"):
                try:
                    if attempt_attr == "connect":
                        engine.connect("started", on_started)
                    else:
                        engine.tts.addObserver("started", on_started)
                    break
                except Exception:  # noqa: BLE001
                    continue

        thread = threading.Thread(
            target=worker, daemon=True,
            kwargs={"spoke": spoke_ev, "finished": finished_ev}
            if can_detect["yes"] else {})
        thread.start()

        # Ждём ЛИБО фактического начала произнесения (событие started —
        # достоверный признак успеха), либо штатного завершения потока.

        deadline = time.monotonic() + timeout
        while not done.is_set() and not spoke_ev.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done.wait(min(remaining, 0.1))
            spoke_ev.wait(0.05)

        if not done.is_set() and not spoke_ev.is_set():
            # Зависание без звука: гасим движок, чтобы поток-«зомби» завершился.
            try:
                engine.stop()
            except Exception:  # noqa: BLE001
                pass
            thread.join(timeout=getattr(config, "TTS_ZOMBIE_JOIN_SECONDS", 2))
            return False, result["spoke"]

        # Поток завершился, а события started не было: даём короткое окно на
        # «опоздавший» коллбэк (на реальных системах он приходит с задержкой).
        if not spoke_ev.is_set() and can_detect["yes"]:
            spoke_ev.wait(0.3)

        # Голос заговорил: даём произнесению штатно завершиться (до
        # TTS_SPEAK_FINISH_GRACE секунд), иначе при раннем stop() следующая
        # фраза упиралась бы в «run loop already started» и движок зря
        # пересоздавался (лишние секунды между ответами).
        if result["spoke"] and not done.is_set():
            done.wait(timeout=getattr(config, "TTS_SPEAK_FINISH_GRACE", 120))
        ok = result["ok"] or result["spoke"]
        spoke = result["spoke"] or (ok and not can_detect["yes"])
        if ok and spoke:
            # Движок живой — отпускаем его сразу, не дожидаясь завершения
            # внутреннего потока sapi5 (join стоил десяткам секунд паузы).
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
        начала реального произнесения (onStart), а не по времени возврата.
        Возвращает True, если озвучка выполнена.
        """
        if not self.available or self._broken:
            return False
        speech_text = clean_for_speech(text, config.MAX_SPEECH_CHARS)
        if not speech_text:
            return False

        # Последовательные озвучки: параллельный запуск двух фраз через один
        # движок вызывает «run loop already started» и лишние пересоздания.
        with self._speak_lock:
            for attempt in (1, 2):
                if attempt == 2 or self.engine is None:
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
            # систему: после двух неудачных фраз подряд отключаем озвучку
            # до перезапуска.
            self._fail_streak += 1
            if self._fail_streak >= 2:
                self._broken = True
                log.warning("Озвучка отключена после %d неудач подряд "
                            "(перезапустите приложение для повторной попытки).",
                            self._fail_streak)
            print("Не удалось озвучить ответ (печатная версия выше).")
            return False

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
                engines = getattr(pyttsx3, "_activeEngines", None)
                if engines is not None:
                    try:
                        engines.clear()
                    except Exception:  # noqa: BLE001
                        pass
                engine = pyttsx3.init(driverName="sapi5")
                # Проверка работоспособности движка: getProperty("voice") бросает
                # COMError (-2147201006), если SAPI не может выбрать голос.
                engine.getProperty("voice")
                engine.setProperty("rate", config.TTS_RATE)
                self._pyttsx3 = pyttsx3
                self.engine = engine
                self._select_voice()
                self.available = True
                return
            except Exception as exc:  # noqa: BLE001 — любой сбой TTS не роняет приложение
                last_exc = exc
                log.warning("Попытка %d: инициализация TTS не удалась: %s",
                            attempt + 1, self._com_error_text(exc))
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

        priorities = ["maxim", "ivona", "russian", "ru-"]
        for hint in priorities:
            for voice in voices:
                haystack = (name_of(voice) + " " + id_of(voice)).lower()
                if hint in haystack:
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
