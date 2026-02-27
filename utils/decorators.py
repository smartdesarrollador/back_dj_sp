"""
Utility decorators for RBAC permission and plan checks.
Stubs — full implementation in PASO 6.
"""
import functools
from typing import Any, Callable


def require_permission(codename: str) -> Callable:
    """
    Decorator: requires user to have the given permission codename.
    Usage: @require_permission('projects.create')

    Full implementation added in PASO 6 (RBAC middleware).
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)
        wrapper._required_permission = codename
        return wrapper
    return decorator


def require_feature(feature_codename: str) -> Callable:
    """
    Decorator: requires tenant to have the given plan feature.
    Usage: @require_feature('custom_roles')

    Full implementation added in PASO 6.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)
        wrapper._required_feature = feature_codename
        return wrapper
    return decorator


def check_plan_limit(resource: str, max_values: dict) -> Callable:
    """
    Decorator: checks plan-based resource limits before allowing creation.
    Usage: @check_plan_limit('projects', {'free': 2, 'starter': 10})

    Full implementation added in PASO 6.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)
        wrapper._plan_limit_resource = resource
        wrapper._plan_limit_values = max_values
        return wrapper
    return decorator
