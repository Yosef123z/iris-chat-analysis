"""
Rate Limiter — Shared slowapi instance.

Separated from main.py to avoid circular imports when routers
need the limiter reference.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
