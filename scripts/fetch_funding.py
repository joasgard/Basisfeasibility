"""Fetch full SOL funding rate history from Hyperliquid."""
import json, os, time, requests
from datetime import datetime, timedelta

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "sol_funding_history.json")

coin = "SOL"
# Go back ~14 months
end_time = int(time.time() * 1000)
start_time = int((datetime(2025, 1, 1)).timestamp() * 1000)

all_records = []
current_start = start_time

while current_start < end_time:
    resp = requests.post(
        "https://api.hyperliquid.xyz/info",
        json={"type": "fundingHistory", "coin": coin, "startTime": current_start},
        headers={"Content-Type": "application/json"},
    )
    data = resp.json()
    if not data:
        break
    all_records.extend(data)
    # Move start past the last record
    last_time = data[-1]["time"]
    current_start = last_time + 1
    if len(data) < 500:
        break  # No more data
    time.sleep(0.3)  # Rate limit

# Deduplicate by timestamp
seen = set()
unique = []
for r in all_records:
    if r["time"] not in seen:
        seen.add(r["time"])
        unique.append(r)

unique.sort(key=lambda x: x["time"])

print(f"Fetched {len(unique)} hourly records")
print(f"Date range: {datetime.fromtimestamp(unique[0]['time']/1000)} to {datetime.fromtimestamp(unique[-1]['time']/1000)}")

with open(OUTPUT_FILE, "w") as f:
    json.dump(unique, f)
print(f"Saved to {OUTPUT_FILE}")
