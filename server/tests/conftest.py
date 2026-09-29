import os
import sys
import tempfile
from pathlib import Path

# Isolate all caches/outputs for the test session.
os.environ.setdefault("TRAILSIDE_DATA_DIR", tempfile.mkdtemp(prefix="trailside-test-"))
os.environ["TRAILSIDE_WRITER"] = "template"
for var in ("TRAILSIDE_KOKORO_MODEL", "TRAILSIDE_KOKORO_VOICES"):
    os.environ.pop(var, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
