"""
Import this before anything from curriculum. Points the data directory at a
throwaway folder, so a test that goes wrong can never touch the real database.
"""
import atexit
import os
import shutil
import tempfile

_dir = tempfile.mkdtemp(prefix="curriculum-test-")
os.environ["CURRICULUM_DATA_DIR"] = _dir
os.environ["CURRICULUM_HOSTED_DIR"] = os.path.join(_dir, "hosted")
atexit.register(shutil.rmtree, _dir, ignore_errors=True)
