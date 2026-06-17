import os

# FLAME Hub endpoints (defaults to production .net)
AUTH_URL = "https://auth.staging.privateaim.net"
CORE_URL = "https://core.staging.privateaim.net"
STORAGE_URL = "https://storage.staging.privateaim.net"

# Authentication credentials
USERNAME = os.environ["FLAME_USERNAME"]
PASSWORD = os.environ["FLAME_PASSWORD"]
