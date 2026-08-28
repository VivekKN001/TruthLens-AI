"""
Retry Logic - Exponential backoff for API calls
"""

import time
import functools
from typing import Callable, TypeVar, Any, Optional
from dataclasses import dataclass

from utils.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


@dataclass
class RetryConfig:
    """Configuration for retry behavior"""
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    exponential_base: float = 2.0
    exceptions: tuple = (Exception,)


def with_retry(
    config: Optional[RetryConfig] = None,
    fallback: Optional[Callable[[], T]] = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator for retrying functions with exponential backoff.

    Args:
        config: Retry configuration
        fallback: Optional fallback function if all retries fail

    Returns:
        Decorated function with retry logic
    """
    if config is None:
        config = RetryConfig()

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception = None

            for attempt in range(1, config.max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except config.exceptions as e:
                    last_exception = e
                    if attempt < config.max_attempts:
                        delay = min(
                            config.base_delay * (config.exponential_base ** (attempt - 1)),
                            config.max_delay,
                        )
                        logger.warning(
                            f"Attempt {attempt}/{config.max_attempts} failed: {e}. "
                            f"Retrying in {delay:.1f}s..."
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            f"All {config.max_attempts} attempts failed for {func.__name__}"
                        )

            # All retries failed
            if fallback is not None:
                logger.info(f"Using fallback for {func.__name__}")
                return fallback()

            raise last_exception

        return wrapper

    return decorator


def retry_on_api_error(func: Callable[..., T]) -> Callable[..., T]:
    """
    Convenience decorator for retrying on common API errors.
    Uses default retry configuration.
    """
    return with_retry(
        config=RetryConfig(
            max_attempts=3,
            base_delay=1.0,
            exceptions=(ConnectionError, TimeoutError, Exception),
        )
    )(func)
