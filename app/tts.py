"""Синтез речи через Windows SAPI5 (pyttsx3). Движок инициализируется один раз."""

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
        self._init_engine()

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
                time.sleep(0.4)

        sapi_hint = self._diagnose_sapi()
        detail = self._com_error_text(last_exc) if last_exc else ""
        combined = "; ".join(p for p in (detail, sapi_hint) if p)
        self._init_error = combined
        log.error("Синтез речи недоступен (%s). Голосовые ответы будут выводиться текстом.",
                  combined)
        self.engine = None
        self.available = False

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

    # ------------------------------------------------------------------
    def say(self, text: str) -> bool:
        """Озвучивает текст (полный ответ при этом уже показан в консоли).

        Возвращает True, если озвучка выполнена.
        """
        if not self.available or self.engine is None:
            return False
        speech_text = clean_for_speech(text, config.MAX_SPEECH_CHARS)
        if not speech_text:
            return False
        try:
            self.engine.say(speech_text)
            self.engine.runAndWait()
            return True
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка озвучки: %s", exc)
            print("Не удалось озвучить ответ (печатная версия выше).")
            return False

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
