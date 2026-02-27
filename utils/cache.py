"""
Cache helpers and utilities.
"""
import hashlib
from functools import wraps
from typing import Any, Callable, Optional
from django.core.cache import cache


def make_cache_key(*parts: Any) -> str:
    """Creates a stable cache key from multiple parts."""
    raw = ':'.join(str(p) for p in parts)
    return hashlib.md5(raw.encode()).hexdigest()


def cache_result(timeout: int = 300, key_prefix: str = '') -> Callable:
    """
    Decorator that caches a function's return value in Redis.
    Cache key includes function name + all positional args.

    Usage:
        @cache_result(timeout=60, key_prefix='tenant_report')
        def get_tenant_summary(tenant_id):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key_parts = [key_prefix or func.__name__] + list(args) + [str(v) for v in kwargs.values()]
            cache_key = make_cache_key(*key_parts)
            result = cache.get(cache_key)
            if result is None:
                result = func(*args, **kwargs)
                cache.set(cache_key, result, timeout=timeout)
            return result
        return wrapper
    return decorator


def invalidate_tenant_cache(tenant_id: str, pattern: Optional[str] = None) -> None:
    """
    Invalidates all cache keys for a given tenant.
    If pattern provided, deletes keys matching that pattern.
    """
    if pattern:
        cache.delete_pattern(f'rbac:{tenant_id}:{pattern}:*')
    else:
        cache.delete_pattern(f'rbac:{tenant_id}:*')
