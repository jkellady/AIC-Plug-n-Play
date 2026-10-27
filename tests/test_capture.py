"""Test for src/capture.py against the validated May 2026 sample.

Read-only on the raw files in 02_RAW_ARCHIVE. Capture output is written only
to the pytest tmp_path (outside the project), never over the validated
05_CAPTURED_DATA/2026-05 files.
"""
from pathlib import Path

import pandas as pd
import pytest

from src.capture import PUNCHES_LONG_COLUMNS, CLOCK_ROSTER_COLUMNS, capture_folder

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLOCK_EXPORTS_DIR = PROJECT_ROOT / "02_RAW_ARCHIVE" / "2026-05" / "clock_exports"
CAPTURED_DIR = PROJECT_ROOT / "05_CAPTURED_DATA" / "2026-05"

# The validated May 2026 sample (raw clock exports + captured CSVs) lives only on
# the build PC. Where it is absent, skip instead of failing.
_DATA_PRESENT = (
    CLOCK_EXPORTS_DIR.is_dir()
    and any(CLOCK_EXPORTS_DIR.glob("*.xls"))
    and (CAPTURED_DIR / "punches_long.csv").is_file()
    and (CAPTURED_DIR / "clock_roster.csv").is_file()
)
pytestmark = pytest.mark.skipif(
    not _DATA_PRESENT,
    reason="validated May 2026 sample (02_RAW_ARCHIVE / 05_CAPTURED_DATA) not present on this PC",
)


def _normalize(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    df = df.copy()
    df["clock_id"] = df["clock_id"].astype(str)
    return df[columns].sort_values(columns).reset_index(drop=True)


def test_raw_files_untouched_mtimes():
    before = {p: p.stat().st_mtime for p in CLOCK_EXPORTS_DIR.glob("*.xls")}
    capture_folder(CLOCK_EXPORTS_DIR)
    after = {p: p.stat().st_mtime for p in CLOCK_EXPORTS_DIR.glob("*.xls")}
    assert before == after


def test_capture_counts_and_matches_validated_output(tmp_path):
    punches_long_rows, roster_rows = capture_folder(CLOCK_EXPORTS_DIR)

    punches_long = pd.DataFrame(punches_long_rows, columns=PUNCHES_LONG_COLUMNS)
    roster = pd.DataFrame(roster_rows, columns=CLOCK_ROSTER_COLUMNS)

    # write capture output to a temp folder only, never over the validated files
    punches_long.to_csv(tmp_path / "punches_long.csv", index=False)
    roster.to_csv(tmp_path / "clock_roster.csv", index=False)

    # counts from CLAUDE.md section 5 / capture_log.md
    assert len(roster) == 54
    assert len(punches_long) == 211
    people_with_punches = roster.loc[roster["days_with_punches"] > 0, "clock_name"]
    assert len(people_with_punches) == 12
    # 10 distinct raw clock_names here; staff_c_alt (OUT2) and staff_c_clock (OUT1) are the
    # same person (STAFF_C) but that merge happens in staff_aliases.csv at the
    # masterdata step, not here. The "9 different people" figure in
    # CLAUDE.md/capture_log.md is checked after alias mapping, not in this test.
    assert people_with_punches.nunique() == 10

    # compare to the existing validated capture
    expected_punches_long = pd.read_csv(CAPTURED_DIR / "punches_long.csv")
    expected_roster = pd.read_csv(CAPTURED_DIR / "clock_roster.csv")

    pd.testing.assert_frame_equal(
        _normalize(punches_long, PUNCHES_LONG_COLUMNS),
        _normalize(expected_punches_long, PUNCHES_LONG_COLUMNS),
    )
    pd.testing.assert_frame_equal(
        _normalize(roster, CLOCK_ROSTER_COLUMNS),
        _normalize(expected_roster, CLOCK_ROSTER_COLUMNS),
    )
