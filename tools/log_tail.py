# -*- coding: utf-8 -*-
"""check.bat step [9]: extract recent errors from app.log into a UTF-8 file.

Usage: python log_tail.py <app.log path> <output path>

Written in pure Python on purpose: PowerShell one-liners behaved
differently across Windows versions (UTF-8 BOM on PS 2.0/3.0, OEM code
page redirection by cmd.exe), which produced unreadable mojibake inside
logs\\check_report.txt. This helper always writes plain UTF-8 without BOM.
"""
import re
import sys


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: log_tail.py <log> <out>")
        return 2
    log_path, out_path = sys.argv[1], sys.argv[2]
    pattern = re.compile(r"ERROR|Traceback|Exception")
    lines_out = []
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()
    except OSError as exc:
        lines_out.append("      (cannot read log: %s)" % exc)
    else:
        tail = all_lines[-200:]
        hits = [ln.rstrip("\r\n") for ln in tail if pattern.search(ln)]
        lines_out = hits[-15:]
        if not lines_out:
            lines_out = ["      (no ERROR lines found in the last 200 log lines)"]
    try:
        with open(out_path, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write("\n".join(lines_out) + "\n")
    except OSError as exc:
        # console fallback if temp file cannot be written
        for ln in lines_out:
            print(ln)
        print("      (cannot write temp file: %s)" % exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
