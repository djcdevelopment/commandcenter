#!/usr/bin/python3
"""Approve one Moonlight PIN using Web UI credentials received on stdin."""

import base64
import json
import ssl
import sys
import time
import urllib.request


request_data = json.load(sys.stdin)
username = request_data.pop("username")
password = request_data.pop("password")

body = json.dumps(
    {"pin": request_data["pin"], "name": request_data["name"]}
).encode("utf-8")
token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")

context = ssl.create_default_context()
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE

for attempt in range(15):
    request = urllib.request.Request(
        "https://192.168.12.220:47990/api/pin",
        data=body,
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, context=context, timeout=10) as response:
        result = json.load(response)
    if result.get("status") is True:
        break
    time.sleep(1)
else:
    raise SystemExit("Sunshine rejected the Moonlight PIN for 15 seconds.")

print("Sunshine accepted the AM4 pairing PIN.")
