# -*- coding: utf-8 -*-
"""check.bat step [8]: import every application module without running main()."""
import os
import sys


def main() -> int:
    root = os.environ.get("VA_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, root)
    try:
        from app import config, utils, storage, search, llm, tts, stt  # noqa: F401
        print("      Imports OK")
        print("      DB path:", config.DB_PATH)
        print("      Log path:", config.APP_LOG_PATH)
        import app.main  # noqa: F401
        print("      main.py loads OK (direct-run fix active)")
    except Exception:
        import traceback
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
