"""
Runs the actual cutover_check.py against the real project data as part of
the test suite, so a future schema or backfill change that silently breaks
parity with the old dashboards' numbers fails CI instead of only being
caught by someone remembering to run the script by hand.
"""
import os

import pytest

import cutover_check

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DASHBOARD_PATH = os.path.join(_PROJECT_ROOT, "data", "NJ_LOC_Work_Order_Dashboard.html")
HIERARCHY_FILES_PRESENT = (
    os.path.exists(os.path.join(_PROJECT_ROOT, "data", "name_to_branch.json"))
    and os.path.exists(os.path.join(_PROJECT_ROOT, "data", "name_to_camp_letter.json"))
)


@pytest.mark.skipif(
    not (os.path.exists(REAL_DASHBOARD_PATH) and HIERARCHY_FILES_PRESENT),
    reason="project data files not present",
)
def test_cutover_check_all_comparable_metrics_match(tmp_path):
    exit_code = cutover_check.main()
    assert exit_code == 0
