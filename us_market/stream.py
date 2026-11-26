"""Optional Alpaca IEX quote stream feeding the same local paper ledger."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json

from us_market.feed import _price, _stamp
from us_market.research import SYMBOLS


async def stream_forever():
    """Reconnect indefinitely; authentication is sent only to Alpaca's fixed TLS endpoint."""
    from websockets.asyncio.client import connect
    from tools.us_paper import _feed, _read, paths
    from tools.paper_portfolio import locked, write
    from us_market.ledger import tick

    account_path, plan_path = paths()
    feed = _feed()
    delay = 2
    while True:
        try:
            async with connect("wss://stream.data.alpaca.markets/v2/iex",
                               open_timeout=12, ping_interval=20, ping_timeout=20,
                               max_size=256_000) as ws:
                await ws.recv()  # connected status
                await ws.send(json.dumps({"action": "auth", "key": feed._headers["APCA-API-KEY-ID"],
                                          "secret": feed._headers["APCA-API-SECRET-KEY"]}))
                auth = json.loads(await ws.recv())
                if not any(x.get("msg") == "authenticated" for x in auth if isinstance(x, dict)):
                    raise RuntimeError("Alpaca stream authentication failed")
                await ws.send(json.dumps({"action": "subscribe", "quotes": list(SYMBOLS)}))
                delay = 2
                quotes = {}
                last_flush = 0.0
                clock = None
                clock_checked = 0.0
                while True:
                    try:
                        batch = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                    except asyncio.TimeoutError:
                        batch = []
                    now = datetime.now(timezone.utc)
                    for item in batch if isinstance(batch, list) else []:
                        if not isinstance(item, dict) or item.get("T") != "q" or item.get("S") not in SYMBOLS:
                            continue
                        try:
                            bid, ask, stamp = _price(item["bp"]), _price(item["ap"]), _stamp(item["t"])
                            if bid > ask or not 0 <= (now - stamp).total_seconds() <= 120:
                                continue
                            quotes[item["S"]] = {"symbol": item["S"], "bid": bid, "ask": ask,
                                                  "at": stamp.isoformat(), "source": "Alpaca IEX",
                                                  "feed": "iex"}
                        except (KeyError, ValueError, TypeError):
                            continue
                    loop_time = asyncio.get_running_loop().time()
                    if loop_time - clock_checked >= 60 or clock is None:
                        clock = await asyncio.to_thread(feed.clock)
                        clock_checked = loop_time
                    if loop_time - last_flush < 15:
                        continue
                    with locked(account_path):
                        state = _read(account_path)
                        if state is None:
                            raise ValueError("Initialize the U.S. paper ledger before streaming")
                        updated = tick(state, _read(plan_path), quotes, clock, now)
                        if updated != state:
                            write(account_path, updated)
                    last_flush = loop_time
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:
            # Do not print provider responses, URLs with tokens, or the auth payload.
            print(f"US paper stream reconnecting ({type(exc).__name__})")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
