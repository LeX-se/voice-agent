# -*- coding: utf-8 -*-
"""check.bat step [6]: fast Ollama availability probe. exit 0 = reachable.

Uses the application's own check (TCP ping first + proxy bypass), which
fails instantly when the port is closed and never hangs longer than the
short bounded timeout - unlike a raw requests.get through a system proxy.
"""
import os
import sys


def main() -> int:
    root = os.environ.get("VA_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, root)
    try:
        from app import llm
    except Exception:
        return 2
    return 0 if llm.check_ollama_available() else 1


if __name__ == "__main__":
    sys.exit(main())
