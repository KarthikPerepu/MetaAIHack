import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Tests never call a real model API, so they cost nothing and always give the same result.
os.environ["KHATA_OFFLINE"] = "1"
os.environ.setdefault("KHATA_LOG", "WARNING")


@pytest.fixture
def state(tmp_path):
    return tmp_path / "state"
