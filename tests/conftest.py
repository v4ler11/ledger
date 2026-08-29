from pathlib import Path

import pytest

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "main.bean"


@pytest.fixture
def sample_path() -> Path:
    return SAMPLE


@pytest.fixture
def scratch_ledger(tmp_path: Path) -> Path:
    """Copy of the sample ledger (root + includes) in a tmp dir."""
    ledger = tmp_path / "ledger.bean"
    ledger.write_text((SAMPLE.parent / "main.bean").read_text())
    (tmp_path / "accounts.bean").write_text(
        (SAMPLE.parent / "accounts.bean").read_text()
    )
    (tmp_path / "commodities.bean").write_text(
        (SAMPLE.parent / "commodities.bean").read_text()
    )
    return ledger
