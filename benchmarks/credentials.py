import json
import os
from pathlib import Path

# FLAME Hub endpoints (defaults to production .net)
AUTH_URL = "https://auth.staging.privateaim.net"
CORE_URL = "https://core.staging.privateaim.net"
STORAGE_URL = "https://storage.staging.privateaim.net"

# Authentication credentials
_path = Path(__file__).parent / "credentials.json"
if _path.exists():
    _creds = json.loads(_path.read_text(encoding="utf-8"))
    USERNAME = _creds["FLAME_USERNAME"]
    PASSWORD = _creds["FLAME_PASSWORD"]
else:
    USERNAME = os.environ["FLAME_USERNAME"]
    PASSWORD = os.environ["FLAME_PASSWORD"]
