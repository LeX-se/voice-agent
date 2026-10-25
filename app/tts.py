"""Синтез речи через Windows SAPI5 (pyttsx3). Движок инициализируется один раз."""

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

        try:
            import pyttsx3  # локальный импорт: модуль может отсутствовать на других ОС
            self.engine = pyttsx3.init(driverName="sapi5")
            self.engine.setProperty("rate", config.TTS_RATE)
            self._select_voice()
            self.available = True
        except Exception as exc:  # noqa: BLE001 — любой сбой TTS не должен ронять приложение
            self._init_error = str(exc)
            log.exception("Синтез речи недоступен: %s", exc)
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
