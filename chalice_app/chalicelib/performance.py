import time
import logging
import functools
from typing import Callable, Any

logger = logging.getLogger()


def measure_execution_time(func: Callable) -> Callable:
    """
    Decorator to measure and log the execution time of a function.

    Args:
        func: The function to be decorated

    Returns:
        The wrapped function with execution time measurement
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        execution_time = end_time - start_time

        # Get function name and class name if it's a method
        if hasattr(args[0].__class__, func.__name__) if args else False:
            # It's a method, get the class name
            class_name = args[0].__class__.__name__
            function_name = f"{class_name}.{func.__name__}"
        else:
            # It's a regular function
            function_name = func.__name__

        logger.info(
            f"PERFORMANCE: {function_name} executed in {execution_time:.4f} seconds"
        )
        return result

    return wrapper
