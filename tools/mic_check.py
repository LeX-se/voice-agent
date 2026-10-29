# -*- coding: utf-8 -*-
"""check.bat step [4]: list recording devices via sounddevice.

Standalone script instead of an inline PowerShell here-string: the old
multi-line `-Command "$c = @' ... '@; ..."` construct was split by cmd.exe
at special characters (^ | > <), so only fragments reached powershell.exe
and the report contained "The string is missing the terminator: '@" plus a
chain of "'import'/'devs'/'try:' is not recognized..." errors.
"""
import sys


def main() -> int:
    try:
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover - environment dependent
        print("      FAILED: cannot import sounddevice:", exc)
        return 1

    try:
        devs = list(sd.query_devices())
    except Exception as exc:
        print("      FAILED: cannot query audio devices:", exc)
        return 1

    ins = [x for x in devs if x["max_input_channels"] > 0]
    print("      Input devices:", len(ins))

    default_in = -1
    try:
        dd = sd.default.device
        if isinstance(dd, (list, tuple)) and len(dd) >= 1 and dd[0] is not None:
            default_in = int(dd[0])
    except Exception:
        pass

    for i, x in enumerate(devs):
        if x["max_input_channels"] > 0:
            mark = "   <-- DEFAULT" if i == default_in else ""
            name = x["name"].splitlines()[0]
            print("        [%d] %s%s" % (i, name, mark))

    try:
        sd.check_input_status()
        print("      Windows microphone access: ALLOWED")
    except Exception as exc:  # noqa: BLE001
        # check_input_status() падает и когда доступ к микрофону закрыт
        # политикой конфиденциальности Windows, и просто когда у выбранного
        # по умолчанию устройства нет входных каналов. Различаем эти случаи,
        # чтобы не выдавать ложный "Windows denies microphone access".
        # "Access is denied" (0x80070005) — это и есть отказ политики
        # конфиденциальности Windows. "Unanticipated host error" тоже может
        # означать запрет доступа, но возникает и из-за битых драйверов:
        # поэтому для него делаем контрольную попытку открыть запись ниже.
        reason = str(exc).lower()
        hard_denial = ("access is denied" in reason or
                       "0x80070005" in reason or
                       "отказано в доступе" in reason)
        ambiguous = "unanticipated host error" in reason
        # Пробуем включить устройство напрямую (для всех ошибок, кроме
        # уже распознанного отказа доступа): если запись открывается —
        # никакая политика Windows не виновата.
        default_ok = False
        if not hard_denial and default_in >= 0:
            # Пробуем включить то же самое устройство напрямую: если оно
            # открывается — доступа это не касается, вины политики нет.
            try:
                with sd.RawInputStream(samplerate=16000, blocksize=8000,
                                       channels=1, dtype="int16",
                                       device=default_in):
                    default_ok = True
            except Exception:  # noqa: BLE001
                default_ok = False

        if default_ok:
            print("      NOTE: check_input_status() failed on the default "
                  "device, but recording works on device [%d]. "
                  "Microphone access is OK." % default_in)
        elif hard_denial:
            # Явный отказ доступа И запись не открывается — точно политика
            # конфиденциальности Windows.
            print("      FAILED: Windows denies microphone access to desktop apps.")
            print("      Fix: Settings - Privacy - Microphone - allow desktop apps.")
            return 1
        elif ambiguous:
            # Неоднозначная ошибка без возможности записи: скорее всего тоже
            # запрет, но формулируем осторожно.
            print("      WARN: microphone could not be opened (%s)." %
                  str(exc)[:120])
            print("      If voice input fails in the app, check "
                  "Settings - Privacy - Microphone - allow desktop apps.")
            return 2
        else:
            print("      NOTE: check_input_status() reported an unrelated "
                  "error (%s); treating microphone as available." %
                  str(exc)[:120])

    return 0


if __name__ == "__main__":
    sys.exit(main())
