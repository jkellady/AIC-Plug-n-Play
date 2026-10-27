"""Read-only pre-check of one month folder in 01_DROP_FOLDER.

Usage:  python -m src.precheck 2026-09

Tells Admin, in plain words, whether the three kinds of input are there
(clock exports, schedule workbook, HR record workbook) using the same
classification the daily run uses. Writes nothing. Exit code 0 = ready,
1 = something is missing.
"""
import sys

from src.daily import CLOCK_EXPORT_GLOB, DROP_DIR, _classify_month_folder


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python -m src.precheck YYYY-MM")
        return 1
    month = argv[1]
    folder = DROP_DIR / month
    if not folder.is_dir():
        print(f"There is no folder {folder.name} inside 01_DROP_FOLDER.")
        return 1

    clock_files, schedule_path, hr_path, notes = _classify_month_folder(folder)
    print(f"Checking 01_DROP_FOLDER\\{month} ...")
    print(f"  Clock exports ({CLOCK_EXPORT_GLOB}): {len(clock_files)} found")
    print(f"  Schedule workbook: {'found' if schedule_path else 'MISSING'}")
    print(f"  HR record workbook: {'found' if hr_path else 'MISSING'}")
    if notes:
        print(f"  Files needing a look: {len(notes)} (see NEEDS_ATTENTION.txt after the run)")

    if clock_files and schedule_path and hr_path:
        print("All three kinds of file are present. Starting the check.")
        return 0
    print("Not ready. Nothing was run and 06_REPORTS was not changed.")
    print(f"Put the missing file(s) in 01_DROP_FOLDER\\{month}, then click this again.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
