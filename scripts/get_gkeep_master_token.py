import gpsoauth
import json
from pathlib import Path

SERVICE_AUTH_PATH  = Path(__file__).parent.parent / "config" / "service_auth.json"
RUNTIME_CREDS_PATH = Path(__file__).parent.parent / "config" / "runtime_credentials.json"

with open(SERVICE_AUTH_PATH, "r") as f:
    service_auth = json.load(f)

email       = service_auth["google"]["username"]
oauth_token = service_auth["google"]["oauth_token"]
android_id  = "0123456789abcdef"

master_response = gpsoauth.exchange_token(email, oauth_token, android_id)
master_token = master_response["Token"]  # if there's no token check the response for more details

# Partial-update the google section only — preserve any existing alexa section
runtime = {}
if RUNTIME_CREDS_PATH.exists():
    with open(RUNTIME_CREDS_PATH, "r") as f:
        runtime = json.load(f)

runtime.setdefault("google", {})
runtime["google"]["username"]     = email
runtime["google"]["master_token"] = master_token

with open(RUNTIME_CREDS_PATH, "w") as f:
    json.dump(runtime, f, indent=4)

print(f"Google master token written to {RUNTIME_CREDS_PATH}")
