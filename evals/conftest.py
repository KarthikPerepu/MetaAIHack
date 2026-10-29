import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Evals are deterministic: never call a model API from the test suite.
os.environ["KHATA_OFFLINE"] = "1"
os.environ.setdefault("KHATA_LOG", "WARNING")


@pytest.fixture
def state(tmp_path):
    return tmp_path / "state"
