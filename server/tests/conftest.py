import os
import sys
import tempfile
from pathlib import Path

# Isolate all caches/outputs for the test session.
os.environ.setdefault("CAIRN_DATA_DIR", tempfile.mkdtemp(prefix="cairn-test-"))
os.environ["CAIRN_WRITER"] = "template"
for var in ("CAIRN_KOKORO_MODEL", "CAIRN_KOKORO_VOICES"):
    os.environ.pop(var, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
