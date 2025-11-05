from __future__ import annotations

import json
from typing import Any, Dict


def create_response(status_code: int, message: str) -> Dict[str, Any]:
    """Build a simple JSON HTTP response payload."""

    return {"statusCode": status_code, "body": json.dumps(message)}
