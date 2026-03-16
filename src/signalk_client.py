"""SignalK WebSocket client with automatic reconnection.

Connects to a SignalK server, subscribes to configured paths,
and feeds incoming delta updates into an asyncio queue.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

from config import Config
from models import SignalKUpdate
from paths import SUBSCRIPTIONS

logger = logging.getLogger(__name__)


class SignalKClient:
    """WebSocket client for SignalK server."""

    def __init__(
        self,
        config: Config,
        queue: asyncio.Queue[SignalKUpdate],
    ) -> None:
        self._config = config
        self._queue = queue
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._self_context: str | None = None
        self._auth_token: str | None = None
        self._running = False
        self._reconnect_index = 0
        self._reconnect_task: asyncio.Task[None] | None = None

    @property
    def self_context(self) -> str | None:
        """The vessel URN from the SignalK hello message."""
        return self._self_context

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    def set_auth_token(self, token: str) -> None:
        """Set the auth token. Forces reconnect if already connected."""
        self._auth_token = token
        if self.is_connected:
            logger.info("Auth token set — forcing reconnect for authenticated session")
            self._reconnect_task = asyncio.ensure_future(self._force_reconnect())

    async def _force_reconnect(self) -> None:
        """Close the current WebSocket to trigger reconnection."""
        if self._ws and not self._ws.closed:
            await self._ws.close()

    async def run(self) -> None:
        """Main run loop — connect, subscribe, receive. Reconnects forever."""
        self._running = True
        while self._running:
            try:
                await self._connect_and_receive()
            except asyncio.CancelledError:
                logger.info("SignalK client cancelled")
                break
            except Exception as exc:
                delay = self._next_reconnect_delay()
                logger.warning(
                    "SignalK connection error: %s — reconnecting in %.0fs",
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

    async def send(self, message: dict[str, Any]) -> None:
        """Send a JSON message over the WebSocket."""
        if self._ws and not self._ws.closed:
            await self._ws.send_json(message)

    async def close(self) -> None:
        """Shut down the client."""
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()

    # --- internal ---

    async def _connect_and_receive(self) -> None:
        """Single connection lifecycle: connect → subscribe → receive loop."""
        headers: dict[str, str] = {}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

        logger.info("Connecting to %s", self._config.signalk_ws_url)
        self._ws = await self._session.ws_connect(
            self._config.signalk_ws_url,
            headers=headers,
            heartbeat=30,
        )
        logger.info("WebSocket connected")
        self._reconnect_index = 0  # Reset backoff on successful connect

        # Wait for hello message
        hello = await self._ws.receive_json()
        self._self_context = hello.get("self", "vessels.self")
        logger.info("SignalK hello: name=%s, self=%s", hello.get("name"), self._self_context)

        # Subscribe to our paths
        await self._subscribe()

        # Receive loop
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    await self._process_delta(data)
                except json.JSONDecodeError:
                    logger.debug("Non-JSON message: %s", msg.data[:100])
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                logger.warning("WebSocket closed/error: %s", msg.type)
                break

    async def _subscribe(self) -> None:
        """Send subscription message for all configured paths."""
        subscribe_list = [{"path": sub.path, "period": sub.period_ms} for sub in SUBSCRIPTIONS]
        msg = {
            "context": "vessels.self",
            "subscribe": subscribe_list,
        }
        await self.send(msg)
        logger.info("Subscribed to %d paths", len(subscribe_list))

    async def _process_delta(self, data: dict[str, Any]) -> None:
        """Extract individual path updates from a SignalK delta message."""
        context = data.get("context", "")
        # Only process self-vessel data (also accept "vessels.self" shorthand)
        if self._self_context and context != self._self_context and context != "vessels.self":
            return

        for update in data.get("updates", []):
            timestamp = update.get("timestamp", "")
            source_label = ""
            source = update.get("source", {})
            if isinstance(source, dict):
                source_label = source.get("label", "")

            for value_entry in update.get("values", []):
                path = value_entry.get("path", "")
                value = value_entry.get("value")
                if path and value is not None:
                    sk_update = SignalKUpdate(
                        path=path,
                        value=value,
                        timestamp=timestamp,
                        source=source_label,
                    )
                    try:
                        self._queue.put_nowait(sk_update)
                    except asyncio.QueueFull:
                        # Drop oldest on overflow
                        import contextlib

                        with contextlib.suppress(asyncio.QueueEmpty):
                            self._queue.get_nowait()
                        with contextlib.suppress(asyncio.QueueFull):
                            self._queue.put_nowait(sk_update)

    def _next_reconnect_delay(self) -> float:
        """Exponential backoff for reconnection."""
        delays = self._config.reconnect_delays
        delay = delays[min(self._reconnect_index, len(delays) - 1)]
        self._reconnect_index += 1
        return delay
