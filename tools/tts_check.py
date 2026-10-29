# -*- coding: utf-8 -*-
"""check.bat step [7]: report TTS engine status and available Windows voices.

Standalone script (see mic_check.py docstring for why inline here-strings
in check.bat were replaced).
"""
import os
import sys


def main() -> int:
    root = os.environ.get("VA_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, root)
    try:
        from app.tts import TextToSpeech
    except Exception as exc:
        print("      FAILED: cannot import app.tts:", exc)
        return 1

    try:
        t = TextToSpeech()
    except Exception as exc:
        print("      FAILED: TTS engine crashed on init:", exc)
        return 1

    print("      TTS available:", t.available)
    print("      Voice:", getattr(t, "voice_name", "") or "(none)")
    try:
        voices = t.list_voices()
    except Exception as exc:
        print("      WARN: cannot enumerate voices:", exc)
        voices = []
    print("      Voices in Windows:", len(voices))
    for item in voices[:10]:
        try:
            name, _ident = item
        except (TypeError, ValueError):
            name = str(item)
        print("        -", name)
    if not t.available and getattr(t, "error", ""):
        print("      Reason:", t.error)
    return 0


if __name__ == "__main__":
    sys.exit(main())
