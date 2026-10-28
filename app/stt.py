"""Распознавание русской речи офлайн: Vosk + sounddevice.

Аудиофайлы на диск не сохраняются — только потоковая передача PCM в распознаватель.
"""

import json
import math

from . import config
from .utils import log


class SpeechToText:
    """Загрузка модели Vosk и запись с микрофона."""

    def __init__(self) -> None:
        self.model = None
        self.model_loaded = False
        self.vosk_available = False
        self.mic_error = ""

        try:
            from vosk import Model  # локальный импорт: библиотека может отсутствовать
            self.vosk_available = True
        except Exception as exc:  # noqa: BLE001
            log.warning("Библиотека Vosk не доступна: %s", exc)

    # ------------------------------------------------------------------
    # Модель
    # ------------------------------------------------------------------
    def model_exists_on_disk(self) -> bool:
        """Проверяет наличие распакованной модели в папке models/."""
        return config.VOSK_MODEL_PATH.is_dir()

    def load_model(self) -> bool:
        """Загружает модель один раз. Возвращает True при успехе."""
        if self.model_loaded:
            return True
        if not self.vosk_available:
            print("Библиотека Vosk не установлена. Голосовой ввод недоступен, "
                  "текстовый режим работает.")
            return False
        if not self.model_exists_on_disk():
            self.print_missing_model_instructions()
            return False
        try:
            from vosk import Model
            log.info("Загрузка модели Vosk: %s", config.VOSK_MODEL_PATH)
            self.model = Model(str(config.VOSK_MODEL_PATH))
            self.model_loaded = True
            print("Модель распознавания речи загружена.")
            log.info("Модель Vosk загружена успешно")
            return True
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка загрузки модели Vosk: %s", exc)
            print("Не удалось загрузить модель Vosk. Подробности в журнале logs/app.log.")
            return False

    @staticmethod
    def print_missing_model_instructions() -> None:
        print("Модель распознавания речи не найдена в папке models\\vosk-model-small-ru-0.22.")
        print("Как включить голосовой ввод:")
        print("  1. Скачайте архив vosk-model-small-ru-0.22.zip со страницы моделей Vosk:")
        print("     https://alphacephei.com/vosk/models")
        print("  2. Распакуйте его так, чтобы получилась папка:")
        print("     models\\vosk-model-small-ru-0.22 (с подпапками conf, am и т.д.)")
        print("  3. Перезапустите приложение.")
        print("До этого можно работать в текстовом режиме: печатайте вопрос и нажмите Enter.")
        log.warning("Модель Vosk отсутствует: %s", config.VOSK_MODEL_PATH)

    # ------------------------------------------------------------------
    # Запись и распознавание
    # ------------------------------------------------------------------
    def listen(self) -> str:
        """Один цикл «говорите — пауза — готово».

        Возвращает распознанный текст или пустую строку.
        При проблемах с микрофоном выводит понятное сообщение и возвращает "".
        """
        if not self.load_model_once_for_listen():
            return ""

        try:
            import numpy as np
            import sounddevice as sd
            from vosk import KaldiRecognizer
        except Exception as exc:  # noqa: BLE001
            log.exception("Не удалось подготовить модули распознавания: %s", exc)
            print("Голосовой ввод временно недоступен: не хватает компонентов. "
                  "Подробности в журнале logs/app.log.")
            return ""

        recognizer = KaldiRecognizer(self.model, config.STT_SAMPLE_RATE)
        samples_per_block = config.STT_BLOCK_SIZE  # int16-отсчётов на блок

        elapsed = 0.0
        silence_after_speech = 0.0
        waited_for_speech = 0.0
        speech_started = False
        texts: list[str] = []

        block_seconds = samples_per_block / config.STT_SAMPLE_RATE

        print("Говорите... (пауза завершает фразу)")
        try:
            with sd.RawInputStream(
                samplerate=config.STT_SAMPLE_RATE,
                blocksize=samples_per_block,
                channels=1,
                dtype="int16",
            ) as stream:
                while True:
                    data, overflowed = stream.read(samples_per_block)
                    if overflowed:
                        log.warning("Переполнение аудиопотока")
                    # sounddevice (CFFI backend) возвращает buffer-объект cffi,
                    # а не bytes. Vosk (ctypes) принимает только bytes/bytearray:
                    # TypeError "initializer for ctype 'char *' must be a cdata
                    # pointer, not _cffi_backend.buffer". Нормализуем в bytes.
                    if not isinstance(data, bytes):
                        data = bytes(data)
                    buf = np.frombuffer(data, dtype=np.int16)
                    rms = _rms(buf)

                    elapsed += block_seconds

                    has_speech = rms >= config.STR_THRESHOLD * 32768.0
                    if has_speech:
                        speech_started = True
                        silence_after_speech = 0.0
                    elif speech_started:
                        silence_after_speech += block_seconds

                    if not speech_started:
                        waited_for_speech += block_seconds
                        if waited_for_speech >= config.STT_WAIT_FOR_SPEECH_SECONDS:
                            print("Речь не обнаружена. Проверьте микрофон и повторите "
                                  "команду /voice.")
                            log.info("Ожидание речи превысило лимит")
                            return ""

                    # Передаём аудио в распознаватель, пока идёт речь.
                    if recognizer.AcceptWaveform(data):
                        part = _extract_text(recognizer.Result())
                        if part:
                            texts.append(part)

                    if speech_started and silence_after_speech >= config.STT_SILENCE_SECONDS:
                        break
                    if elapsed >= config.STT_MAX_SECONDS:
                        log.info("Достигнут максимум длительности записи")
                        break
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка записи с микрофона: %s", exc)
            self._report_mic_problem(exc)
            return ""

        # Хвост unrecognized-фрагментов + финальный результат.
        final_part = _extract_text(recognizer.FinalResult())
        if final_part:
            texts.append(final_part)

        result = " ".join(t for t in texts if t).strip()
        if result:
            print(f"Распознано: {result}")
        else:
            print("Речь не распознана. Повторите громче и ближе к микрофону.")
        return result

    def load_model_once_for_listen(self) -> bool:
        """Ленивая загрузка модели перед прослушиванием."""
        if self.model_loaded:
            return True
        return self.load_model()

    @staticmethod
    def _report_mic_problem(exc: Exception) -> None:
        text = str(exc).lower()
        if "in_use" in text or "busy" in text or "unavailable device" in text \
                or "invalid device" in text:
            print("Микрофон недоступен или занят другим приложением.")
        else:
            print("Не удалось начать запись с микрофона.")
        print("Проверьте разрешение: «Параметры → Конфиденциальность → Микрофон» "
              "и что микрофон подключён.")
        print("Возврат в главное меню; текстовый ввод доступен.")


def _rms(buf) -> float:
    """Среднеквадратичный уровень сигнала int16-буфера (0..32767)."""
    if len(buf) == 0:
        return 0.0
    values = buf.astype("float64")
    return float(math.sqrt(float((values * values).mean())))


def _extract_text(raw_json: str) -> str:
    """Достаёт поле text из JSON-результата Vosk."""
    try:
        return json.loads(raw_json).get("text", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return ""
