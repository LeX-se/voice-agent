# -*- coding: utf-8 -*-
"""check.bat step [6]: print installed Ollama models (one per line)."""
import os
import sys


def main() -> int:
    root = os.environ.get("VA_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, root)
    try:
        from app import config, llm
        response = llm.session.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
        response.raise_for_status()
        models = [m.get("name", "?") for m in response.json().get("models", [])]
    except Exception as exc:
        print("        (cannot list models:", exc, ")")
        return 0
    if not models:
        print("        (no models installed - run: ollama pull qwen2.5:3b)")
    for m in models:
        print("        -", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
