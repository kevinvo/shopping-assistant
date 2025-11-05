import json
import logging
import uuid
from typing import Dict, Any, Tuple, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict

from chalicelib.aws.dynamo.tables import SessionInfo, SessionData

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def log_session_event(event_type: str, data: Dict[str, Any]):
    """Helper function for structured logging"""
    log_data = {
        "event_type": f"SESSION_API_{event_type}",
        "timestamp": datetime.now().isoformat(),
        **data,
    }
    logger.info(json.dumps(log_data, indent=2))


@dataclass
class ApiResponse:
    status: str
    message: Optional[str] = None
    valid: Optional[bool] = None
    session: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class SessionHandler:
    def __init__(self):
        pass

    def get_session_id(self, cookies: Optional[str]) -> Optional[str]:
        """Extract session_id from cookie string"""
        if not cookies:
            return None

        # Parse cookies
        cookie_dict = {}
        for item in cookies.split("; "):
            if "=" in item:
                key, value = item.split("=", 1)
                cookie_dict[key] = value

        return cookie_dict.get("session_id")

    def store_session(self, session_id: str, session_data: SessionData) -> SessionInfo:
        expiry_time = int((datetime.now() + timedelta(days=30)).timestamp())
        session_info = SessionInfo(
            id=session_id, data=session_data, expiry_time=expiry_time
        )
        session_info.save()
        log_session_event(
            "SESSION_STORED", {"session_id": session_id, "expiry_time": expiry_time}
        )
        return session_info

    def get_session_info(self, session_id: Optional[str]) -> Optional[SessionInfo]:
        try:
            if not session_id:
                return None
            session_info = SessionInfo.get_by_session_id(session_id=session_id)
            log_session_event(
                "SESSION_RETRIEVED",
                {"session_id": session_id, "found": bool(session_info)},
            )
            return session_info
        except Exception as e:
            log_session_event(
                "ERROR",
                {
                    "error": str(e),
                    "operation": "get_session_info",
                    "session_id": session_id,
                },
            )
            return None

    def validate_session(
        self,
        session_data: Optional[SessionData],
        session_id: Optional[str],
    ) -> Tuple[SessionData, ApiResponse, Optional[str]]:

        log_session_event(
            "VALIDATION_START",
            {"session_id": session_id, "has_session_data": bool(session_data)},
        )

        now = datetime.now().isoformat()

        if session_data:
            session_data.last_active = now
            log_session_event(
                "VALIDATION_SUCCESS", {"session_id": session_id, "last_active": now}
            )
            return (
                session_data,
                ApiResponse(status="success", valid=True, session=asdict(session_data)),
                session_id,
            )

        if not session_id:
            new_session_id = str(uuid.uuid4())
            session_data = SessionData(created_at=now, last_active=now)
            self.store_session(session_id=new_session_id, session_data=session_data)
            log_session_event(
                "NEW_SESSION_CREATED", {"session_id": new_session_id, "created_at": now}
            )

            return (
                session_data,
                ApiResponse(
                    status="success",
                    valid=True,
                    session=asdict(session_data),
                    message="New session created",
                ),
                new_session_id,
            )

        empty_session = SessionData(created_at=now, last_active=now)
        log_session_event(
            "INVALID_SESSION",
            {"session_id": session_id, "reason": "Invalid or expired session"},
        )

        return (
            empty_session,
            ApiResponse(
                status="error", valid=False, message="Invalid or expired session"
            ),
            None,
        )
