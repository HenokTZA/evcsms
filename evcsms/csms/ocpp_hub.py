# csms/ocpp_hub.py
from __future__ import annotations
import asyncio
from typing import Dict, Any, Optional

class OcppHub:
    """
    Stores live CP connections (set by your OCPP WS server) and lets the API
    send calls to them. You just need to register/unregister in runocpp.py.
    """
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_id: Dict[int, Any] = {}  # cp_id -> live ocpp ChargePoint instance

    async def register(self, cp_id: int, cp_any: Any) -> None:
        async with self._lock:
            self._by_id[cp_id] = cp_any

    async def unregister(self, cp_id: int) -> None:
        async with self._lock:
            self._by_id.pop(cp_id, None)

    async def get(self, cp_id: int) -> Optional[Any]:
        async with self._lock:
            return self._by_id.get(cp_id)

    async def call(self, cp_id: int, action: str, payload: dict) -> Any:
        """
        Calls e.g. await cp.call('Reset', {...}) on the live connection.
        Adapt this if your CP wrapper has a different API.
        """
        cp = await self.get(cp_id)
        if cp is None:
            raise RuntimeError("Charge point is offline or not connected.")
        # Most ocpp servers expose a .call(action, payload) or similar.
        # If you’ve built named helpers, map them here.
        return await cp.call(action, payload)

# global singleton
hub = OcppHub()

