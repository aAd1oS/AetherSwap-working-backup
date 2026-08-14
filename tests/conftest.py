import atexit
import os
import shutil
import tempfile
from pathlib import Path


_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="aetherswap-pytest-"))
os.environ["AETHERSWAP_DB_PATH"] = str(_TEST_DATA_DIR / "app.db")


@atexit.register
def _remove_test_data_dir():
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)
