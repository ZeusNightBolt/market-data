#!/usr/bin/env python3
"""Test Polygon.io WebSocket — connect, auth, subscribe, collect bars."""
import json, time, threading, urllib.request
import websocket
from pathlib import Path

API_KEY = [l.split("=",1)[1].strip().strip('"').strip("'")
           for l in Path.home().joinpath(".hermes/.env").read_text().splitlines()
           if l.startswith("POLYGON_API_KEY=")][0]

auth_ok = threading.Event()
messages = []
bars = []

def on_message(ws, message):
    data = json.loads(message)
    if isinstance(data, list):
        for item in data:
            ev = item.get("ev", "")
            status = item.get("status", "")
            msg_text = item.get("message", "")
            messages.append(item)
            if ev in ("A", "AM"):
                bars.append(item)
                if len(bars) <= 3:
                    print(f"  [{ev}] {item.get('sym')} V={item.get('v',0):,} "
                          f"AV={item.get('av',0):,} O={item.get('o')} C={item.get('c')}")
            elif status in ("auth_success", "connected"):
                print(f"  STATUS: {status} — {msg_text}")
                auth_ok.set()
    elif isinstance(data, dict):
        print(f"  DICT: {json.dumps(data)[:200]}")
        if data.get("status") == "auth_success":
            auth_ok.set()

def on_open(ws):
    print("Connected. Authenticating...")
    ws.send(json.dumps({"action": "auth", "params": API_KEY}))

ws = websocket.WebSocketApp(
    "wss://delayed.massive.com/stocks",
    on_open=on_open, on_message=on_message
)
t = threading.Thread(target=ws.run_forever, daemon=True)
t.start()

# Wait for auth
if auth_ok.wait(timeout=10):
    print("Auth OK. Subscribing to A.* + AM.* (all tickers)...")
    ws.send(json.dumps({"action": "subscribe", "params": "A.*,AM.*"}))
    time.sleep(8)
else:
    print("Auth timeout!")

# Check market status
resp = json.loads(urllib.request.urlopen(
    f"https://api.polygon.io/v1/marketstatus/now?apiKey={API_KEY}"
).read().decode())
print(f"\nMarket: {resp.get('market', '?')} | NYSE: {resp.get('exchanges',{}).get('nyse','?')} | NASDAQ: {resp.get('exchanges',{}).get('nasdaq','?')}")

a_bars = [b for b in bars if b.get("ev") == "A"]
am_bars = [b for b in bars if b.get("ev") == "AM"]
print(f"\nTotal: {len(a_bars)} sec bars + {len(am_bars)} min bars | {len(messages)} messages")

if bars:
    print(f"\nSample second bar:\n{json.dumps(bars[0], indent=2)}")

ws.close()
print("Done")
