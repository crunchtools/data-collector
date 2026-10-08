"""What the collector's own OpenRouter key has spent, and has left.

The key is the collector's alone and carries a spending limit, so that a
collection run can never exhaust the key some other service depends on.
Reading the key's usage before and after a judge model's pass is what turns
"about two dollars a day" into a number in the database.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

KEY_URL = "https://openrouter.ai/api/v1/key"


@dataclass(frozen=True)
class Budget:
    """Dollars the key has used, and dollars left under its limit (None when
    the key has no limit)."""

    used: float
    left: float | None

    def covers(self, floor: float) -> bool:
        """Whether more than ``floor`` dollars are left to spend."""
        return self.left is None or self.left > floor


async def budget(key: str, transport: httpx.AsyncBaseTransport | None = None) -> Budget:
    """Ask OpenRouter what ``key`` has used. Raises ``httpx.HTTPError`` when
    it cannot be read: a run that cannot see its budget does not spend."""
    async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
        reply = await client.get(KEY_URL, headers={"Authorization": f"Bearer {key}"})
        reply.raise_for_status()
    key_state = reply.json()["data"]
    left = key_state.get("limit_remaining")
    return Budget(float(key_state["usage"]), None if left is None else float(left))
