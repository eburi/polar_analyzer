"""SignalK device authentication — access request protocol.

Implements the headless device registration flow:
1. Load saved clientId + JWT from disk
2. Validate existing token via REST API
3. If invalid, POST access request and poll until approved
4. Save token on approval
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

from config import Config

logger = logging.getLogger(__name__)


class SignalKAuth:
    """Manages device authentication with a SignalK server."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._token_path = Path(config.token_file)
        self._client_id: str | None = None
        self._token: str | None = None

    @property
    def token(self) -> str | None:
        return self._token

    async def authenticate(self) -> str:
        """Get a valid auth token. Blocks until approved by admin if needed.

        Returns the JWT token string.
        """
        self._load_saved_state()

        if self._client_id is None:
            self._client_id = str(uuid.uuid4())
            self._save_state()  # Persist clientId immediately

        # Try existing token
        if self._token:
            if await self._validate_token():
                logger.info("Existing auth token is valid")
                return self._token
            else:
                logger.info("Existing auth token invalid — requesting new one")
                self._token = None

        # Request new access
        return await self._request_access()

    async def _validate_token(self) -> bool:
        """Check if the current token is still valid."""
        url = f"{self._config.signalk_http_url}/signalk/v1/api/vessels/self"
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    return resp.status == 200
        except Exception as exc:
            logger.debug("Token validation failed: %s", exc)
            return False

    async def _request_access(self) -> str:
        """POST access request and poll until approved."""
        url = f"{self._config.signalk_http_url}/signalk/v1/access/requests"
        payload = {
            "clientId": self._client_id,
            "description": self._config.device_description,
            "permissions": "readwrite",
        }

        async with aiohttp.ClientSession() as session:
            # Submit the request
            logger.info(
                "Requesting device access — approve '%s' in SignalK admin UI",
                self._config.device_name,
            )
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status not in (200, 202):
                    body = await resp.text()
                    raise RuntimeError(
                        f"Access request failed ({resp.status}): {body}"
                    )
                result = await resp.json()

            # Get the polling href
            href = result.get("href")
            if not href:
                raise RuntimeError(f"No href in access request response: {result}")

            poll_url = f"{self._config.signalk_http_url}{href}"
            logger.info("Polling %s for approval...", poll_url)

            # Poll until approved or denied
            while True:
                await asyncio.sleep(self._config.auth_poll_interval)
                try:
                    async with session.get(
                        poll_url,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        poll_result = await resp.json()
                except Exception as exc:
                    logger.debug("Poll error: %s — retrying", exc)
                    continue

                state = poll_result.get("state", "")
                if state == "COMPLETED":
                    access = poll_result.get("accessRequest", {})
                    permission = access.get("permission", "")
                    if permission == "APPROVED":
                        self._token = access.get("token")
                        if not self._token:
                            raise RuntimeError("Approved but no token in response")
                        self._save_state()
                        logger.info("Device access approved — token saved")
                        return self._token
                    else:
                        raise RuntimeError(
                            f"Access request denied: {permission}"
                        )
                elif state == "PENDING":
                    logger.debug("Still pending approval...")
                else:
                    logger.warning("Unexpected poll state: %s", state)

    def _load_saved_state(self) -> None:
        """Load clientId and token from disk."""
        if not self._token_path.exists():
            return
        try:
            data = json.loads(self._token_path.read_text())
            self._client_id = data.get("clientId")
            self._token = data.get("token")
            logger.debug("Loaded auth state from %s", self._token_path)
        except Exception as exc:
            logger.warning("Failed to load auth state: %s", exc)

    def _save_state(self) -> None:
        """Persist clientId and token to disk."""
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {"clientId": self._client_id}
        if self._token:
            data["token"] = self._token
        self._token_path.write_text(json.dumps(data, indent=2))
        logger.debug("Saved auth state to %s", self._token_path)
