import json
import os
from pathlib import Path

# API endpoints advertised by the benchmark hub.
AUTH_URL = "https://hub.benchmark.privateaim.dev/auth/"
CORE_URL = "https://hub.benchmark.privateaim.dev/core/"
STORAGE_URL = "https://hub.benchmark.privateaim.dev/storage/"

# Authentication credentials
_path = Path(__file__).parent / "credentials.json"
if _path.exists():
    _creds = json.loads(_path.read_text(encoding="utf-8"))
    USERNAME = _creds["FLAME_USERNAME"]
    PASSWORD = _creds["FLAME_PASSWORD"]
else:
    USERNAME = os.environ["FLAME_USERNAME"]
    PASSWORD = os.environ["FLAME_PASSWORD"]
