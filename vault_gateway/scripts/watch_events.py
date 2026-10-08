"""Print the Vault's live decision feed in the terminal, one coloured line per decision.

    .venv/bin/python scripts/watch_events.py [ws://localhost:8000/ws/events]
"""

import asyncio
import json
import sys

import websockets

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:8000/ws/events"
RED, GREEN, BOLD, RESET = "\033[31m", "\033[32m", "\033[1m", "\033[0m"


async def main():
    async with websockets.connect(URL) as ws:
        print(f"watching {URL} (Ctrl+C to stop)", flush=True)
        async for message in ws:
            event = json.loads(message)
            colour = GREEN if event["decision"] == "ALLOW" else RED
            weight = BOLD if event["severity"] == "critical" else ""
            gate = f" [{event['gate_label']}]" if event["gate_label"] else ""
            tool = event["tool"] or "(unreadable request)"
            print(f"{weight}{colour}#{event['seq']} {event['decision']}{gate}{RESET} {tool}: {event['reason']}", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
