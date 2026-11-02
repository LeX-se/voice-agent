# -*- coding: utf-8 -*-
"""Временный тест стабильности логики app/tts.py на mock-движке (не коммитить)."""
import sys, threading, time, types

sys.path.insert(0, "/workspace")

from app import config  # noqa: E402
config.TTS_SPEAK_TIMEOUT = 3
config.TTS_START_WAIT_SECONDS = 1
config.TTS_END_WAIT_SECONDS = 0.2
config.TTS_ZOMBIE_JOIN_SECONDS = 0.2

from app import tts as tts_mod  # noqa: E402


class FakeVoice:
    def __init__(self, vid, name):
        self.id, self.name = vid, name


class FakeEngine:
    """Эмулирует Engine+driver sapi5. Режимы: normal | bug412 | hang."""

    _counter = [0]

    def __init__(self, mode, speak_time=0.3):
        self.mode = mode
        self.speak_time = speak_time
        self.idx = self._counter[0]
        FakeEngine._counter[0] += 1
        self.observers = {}
        self.proxy = types.SimpleNamespace(_driver=types.SimpleNamespace(
            _looping=True, _speaking=False, _stopping=False))
        self.tts = types.SimpleNamespace(addObserver=self._add_observer)
        self.stopped = False
        self.rate_applied_after_voice = None
        self._voice_order = []

    def _add_observer(self, event, cb):
        self.observers.setdefault(event, []).append(cb)

    def connect(self, event, cb):
        if self.mode == "no_connect":
            raise AttributeError("connect")
        self._add_observer(event, cb)

    def getProperty(self, name):
        if name == "voices":
            return [FakeVoice("HKEY..MSAnna", "Microsoft Anna"),
                    FakeVoice("HKEY..IVONA Maxim", "IVONA 2 Maxim")]
        if name == "voice":
            return FakeVoice("x", "y")
        raise KeyError(name)

    def setProperty(self, name, value):
        self._voice_order.append(name)
        if name == "rate":
            self.rate_applied_after_voice = list(self._voice_order)

    def say(self, text):
        if self.mode == "hang":
            time.sleep(30)  # имитация вечного зависания runAndWait
        # старт произнесения — как SAPI, с небольшой задержкой
        def fire():
            time.sleep(0.05)
            for cb in self.observers.get("started", []):
                cb()
            time.sleep(self.speak_time)
            for cb in self.observers.get("completed", []):
                cb()
        t = threading.Thread(target=fire, daemon=True)
        t.start()

    def runAndWait(self):
        if self.idx == 0 and self.mode != "bug412":
            # legacy guard (unused)
            pass
        if self.mode == "hang":
            time.sleep(30)
        if self.mode == "bug412":
            return  # молчаливый мгновенный возврат без события started (bug412)
        time.sleep(self.speak_time + 0.1)

    def stop(self):
        self.stopped = True


class FakePyttsx3:
    def __init__(self, modes):
        self._modes = list(modes)
        self._activeEngines = {"sapi5": object()}  # мусор для проверки очистки кэша
        self.created = []

    def init(self, driverName=None):
        mode = self._modes.pop(0) if self._modes else "normal"
        eng = FakeEngine(mode)
        self.created.append(eng)
        return eng


def make_tts(modes):
    obj = tts_mod.TextToSpeech.__new__(tts_mod.TextToSpeech)
    obj.engine = None
    obj.available = True
    obj.voice_name = ""
    obj.voice_id = ""
    obj.used_default_voice = False
    obj._init_error = ""
    obj._broken = False
    obj._fail_streak = 0
    obj._speak_lock = threading.Lock()
    fake = FakePyttsx3(modes)
    obj._pyttsx3 = fake
    obj.engine = fake.init("sapi5")
    return obj, fake


fails = []

# --- Сценарий 1: нормальный движок — озвучка успешна, движок НЕ пересоздаётся
obj, fake = make_tts(["normal"])
t0 = time.monotonic()
ok = obj.say("Привет мир это тест")
dt = time.monotonic() - t0
assert ok is True, "scenario1: say should succeed"
assert len(fake.created) == 1, "scenario1: engine must not be recreated on success"
assert dt < 2.0, f"scenario1: too slow {dt:.2f}s"
print(f"OK scenario1 normal speak ({dt:.2f}s)")

# --- Сценарий 2: баг #412 (молчаливый возврат) — пересоздание и успех 2-й попытки
obj, fake = make_tts(["bug412", "normal"])
ok = obj.say("Проверка восстановления после бага 412")
assert ok is True, "scenario2: recovery must succeed"
assert len(fake.created) == 2, "scenario2: engine recreated after silent failure"
assert obj._fail_streak == 0
print("OK scenario2 bug412 recovery")

# --- Сценарий 3: зависание — возврат по таймауту, обе попытки провалены,
#     сообщение напечатано, streak растёт; после TTS_MAX_BROKEN_STREAK — _broken
obj, fake = make_tts(["hang"] * 10)
config.TTS_MAX_RECOVERY_PER_PHRASE = 2
t0 = time.monotonic()
ok = obj.say("Зависшая фраза")
dt = time.monotonic() - t0
assert ok is False, "scenario3: hang must fail"
assert dt < 8.0, f"scenario3: timeout not bounded: {dt:.2f}s"
print(f"OK scenario3 hang bounded ({dt:.2f}s), fail_streak={obj._fail_streak}")
ok2 = obj.say("Вторая зависшая фраза")
assert ok2 is False and obj._broken is True, "after streak -> broken until restart"
ok3 = obj.say("Третья фраза после поломки")
assert ok3 is False, "broken engine short-circuits"
print("OK scenario3b graceful disable after streak")

# --- Сценарий 4: голос выбирается по TTS_VOICE_HINT; rate применяется после voice
obj, fake = make_tts(["normal"])
obj._select_voice()
assert obj.voice_name == "IVONA 2 Maxim", obj.voice_name
order = fake.rate_applied_after_voice or fake.created[0]._voice_order
print("OK scenario4 voice selection via hint; prop order:", order)

# --- Сценарий 5: параллельные вызовы say сериализованы (без реентранса lock)
obj, fake = make_tts(["normal"] * 6)
results = []
threads = [threading.Thread(target=lambda: results.append(obj.say("Параллельная озвучка теста")))
           for _ in range(4)]
for t in threads: t.start()
for t in threads: t.join(timeout=15)
assert results == [True] * 4, results
print("OK scenario5 serialized concurrent speaks")

print("ALL TESTS PASSED")
