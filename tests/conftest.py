import pytest

from config import DRAFT_THRESHOLDS, THRESHOLDS


@pytest.fixture(autouse=True)
def draft_thresholds():
    """Tuned thresholds (data/<pack>/thresholds.json) change with every evaluation; tests exercise the logic against the drafts."""
    saved = dict(THRESHOLDS)
    THRESHOLDS.clear()
    THRESHOLDS.update(DRAFT_THRESHOLDS)
    yield
    THRESHOLDS.clear()
    THRESHOLDS.update(saved)
