import time
import logging
import json
from dataclasses import asdict, dataclass
from typing import Optional
from functools import wraps

logger = logging.getLogger()

_container_initialized = False
_init_start_time: Optional[float] = None
_init_end_time: Optional[float] = None


def reset_cold_start_state():
    global _container_initialized, _init_start_time, _init_end_time
    _container_initialized = False
    _init_start_time = None
    _init_end_time = None


def mark_init_start():
    global _init_start_time
    if _init_start_time is None:
        _init_start_time = time.time()


def mark_init_end():
    global _init_end_time
    if _init_end_time is None:
        _init_end_time = time.time()


def is_cold_start() -> bool:
    global _container_initialized
    if not _container_initialized:
        _container_initialized = True
        return True
    return False


def get_init_duration() -> Optional[float]:
    if _init_start_time is not None and _init_end_time is not None:
        return _init_end_time - _init_start_time
    return None


@dataclass
class ColdStartMetrics:
    metric_type: str
    handler_name: str
    is_cold_start: bool
    timestamp: float
    request_id: Optional[str] = None
    function_name: Optional[str] = None
    function_version: Optional[str] = None
    memory_limit_mb: Optional[int] = None
    remaining_time_ms: Optional[int] = None
    init_duration_ms: Optional[float] = None
    handler_duration_ms: Optional[float] = None


def log_cold_start_metrics(
    handler_name: str,
    context: Optional[object] = None,
    init_duration: Optional[float] = None,
    handler_start_time: Optional[float] = None,
    handler_end_time: Optional[float] = None,
) -> ColdStartMetrics:
    metrics = ColdStartMetrics(
        metric_type="cold_start",
        handler_name=handler_name,
        is_cold_start=is_cold_start(),
        timestamp=time.time(),
    )

    if context:
        metrics.request_id = getattr(context, "aws_request_id", None)
        metrics.function_name = getattr(context, "function_name", None)
        metrics.function_version = getattr(context, "function_version", None)
        metrics.memory_limit_mb = getattr(context, "memory_limit_in_mb", None)
        metrics.remaining_time_ms = getattr(
            context, "get_remaining_time_in_millis", lambda: None
        )()

    if init_duration is not None:
        metrics.init_duration_ms = init_duration * 1000

    if handler_start_time is not None and handler_end_time is not None:
        handler_duration = handler_end_time - handler_start_time
        metrics.handler_duration_ms = handler_duration * 1000

    logger.info(
        "COLD_START_METRICS",
        extra={"cold_start_metrics": json.dumps(asdict(metrics))},
    )
    if metrics.is_cold_start:
        init_str = f"Init: {init_duration*1000:.2f}ms" if init_duration else "Init: N/A"
        logger.info(
            f"🚀 Cold start detected for {handler_name} | "
            f"{init_str} | "
            f"Request ID: {metrics.request_id or 'N/A'}"
        )
    else:
        logger.info(
            f"⚡ Warm start for {handler_name} | "
            f"Request ID: {metrics.request_id or 'N/A'}"
        )

    return metrics


def measure_cold_start(handler_name: Optional[str] = None):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            context = None
            if len(args) >= 2:
                context = args[1]
            elif "context" in kwargs:
                context = kwargs["context"]

            name = handler_name or func.__name__
            handler_start_time = time.time()
            init_duration = get_init_duration()

            try:
                result = func(*args, **kwargs)
                handler_end_time = time.time()
                log_cold_start_metrics(
                    handler_name=name,
                    context=context,
                    init_duration=init_duration,
                    handler_start_time=handler_start_time,
                    handler_end_time=handler_end_time,
                )
                return result
            except Exception as e:
                handler_end_time = time.time()
                metrics = log_cold_start_metrics(
                    handler_name=name,
                    context=context,
                    init_duration=init_duration,
                    handler_start_time=handler_start_time,
                    handler_end_time=handler_end_time,
                )
                error_metrics = {**asdict(metrics), "error": str(e)}
                logger.error(
                    "COLD_START_METRICS_ERROR",
                    extra={"cold_start_metrics": json.dumps(error_metrics)},
                )
                raise

        return wrapper

    return decorator
