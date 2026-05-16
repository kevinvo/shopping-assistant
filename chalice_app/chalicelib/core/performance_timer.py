import time
import logging
import functools
from contextlib import contextmanager
from typing import Callable, Any, Iterator

logger = logging.getLogger()


@contextmanager
def timed_block(name: str) -> Iterator[None]:
    start = time.time()
    try:
        yield
    finally:
        logger.info(
            f"PERFORMANCE: {name} executed in {time.time() - start:.4f} seconds"
        )


def measure_execution_time(func: Callable) -> Callable:
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        if args and hasattr(args[0].__class__, func.__name__):
            name = f"{args[0].__class__.__name__}.{func.__name__}"
        else:
            name = func.__name__
        with timed_block(name):
            return func(*args, **kwargs)

    return wrapper


def measure_performance(func: Callable) -> Callable:
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        with timed_block(func.__qualname__):
            return func(*args, **kwargs)

    return wrapper
