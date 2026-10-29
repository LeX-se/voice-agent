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
        default_in = int(sd.default.device[0])
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
    except Exception:
        print("      FAILED: Windows denies microphone access to desktop apps.")
        print("      Fix: Settings - Privacy - Microphone - allow desktop apps.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
