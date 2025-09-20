# csms/tx_cache.py
from django.core.cache import cache

def _key(user_id: int, cp_id: str) -> str:
    return f"active_tx:{user_id}:{cp_id}"

def set_active_tx(user_id: int, cp_id: str, tx_id: int, ttl_seconds: int = 7200):
    cache.set(_key(user_id, cp_id), tx_id, ttl_seconds)

def get_active_tx(user_id: int, cp_id: str):
    return cache.get(_key(user_id, cp_id))

def clear_active_tx(user_id: int, cp_id: str):
    cache.delete(_key(user_id, cp_id))

