"""REST API handlers for session management and authentication."""

import logging
from dataclasses import asdict
from typing import Optional

from chalice import CORSConfig, Response

from chalicelib.core.error_notifications import notify_on_exception
from chalicelib.sessions.session_handler import SessionHandler


logger = logging.getLogger()
logger.setLevel(logging.INFO)


cors_config = CORSConfig(
    allow_origin="http://localhost:3000",
    allow_headers=[
        "Content-Type",
        "Cookie",
        "X-Amz-Date",
        "Authorization",
        "X-Api-Key",
    ],
    allow_credentials=True,
    max_age=86400,
)


session_handler = SessionHandler()


def create_response_with_cookie(
    body: dict, session_id: Optional[str] = None, origin: Optional[str] = None
) -> Response:
    """Create a response with optional session cookie."""

    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": origin or "http://localhost:3000",
        "Access-Control-Allow-Credentials": "true",
    }

    if session_id:
        is_localhost = origin and origin.startswith("http://localhost")

        if is_localhost:
            headers["Set-Cookie"] = (
                f"session_id={session_id}; Path=/; HttpOnly; SameSite=Lax"
            )
        else:
            headers["Set-Cookie"] = (
                f"session_id={session_id}; Path=/; HttpOnly; Secure; SameSite=None"
            )

    logger.info("Response headers: %s", headers)
    return Response(body=body, status_code=200, headers=headers)


def register_rest_routes(app):
    """Register all REST API routes with the Chalice app."""

    @app.route("/session", methods=["GET"], cors=cors_config)
    @notify_on_exception
    def validate_session():
        """Validate or create a new session."""

        try:
            cookies = app.current_request.headers.get("cookie")
            origin = app.current_request.headers.get("origin", "http://localhost:3000")

            logger.info("Session validation request from origin: %s", origin)
            logger.info("Cookies: %s", cookies)

            session_id = session_handler.get_session_id(cookies)
            session_info = (
                session_handler.get_session_info(session_id) if session_id else None
            )
            session_data = session_info.data if session_info else None

            session_data, response, new_session_id = session_handler.validate_session(
                session_data=session_data, session_id=session_id
            )

            if new_session_id and new_session_id != session_id:
                session_handler.store_session(
                    session_id=new_session_id, session_data=session_data
                )
                session_id = new_session_id
            elif session_id and session_data:
                session_handler.store_session(
                    session_id=session_id, session_data=session_data
                )

            response_body = asdict(response)
            return create_response_with_cookie(
                body=response_body, session_id=session_id, origin=origin
            )

        except Exception as exc:  # pragma: no cover - defensive logging
            import traceback

            logger.error("Error in validate_session: %s", exc)
            logger.error("Traceback: %s", traceback.format_exc())
            return Response(
                body={"error": "Internal server error", "details": str(exc)},
                status_code=500,
                headers={
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": app.current_request.headers.get(
                        "origin", "http://localhost:3000"
                    ),
                    "Access-Control-Allow-Credentials": "true",
                },
            )

    @app.route("/auth", methods=["POST"], cors=cors_config)
    @notify_on_exception
    def authenticate():
        """Authenticate user and create session."""

        try:
            request_body = app.current_request.json_body or {}
            origin = app.current_request.headers.get("origin", "http://localhost:3000")

            logger.info("Auth request from origin: %s", origin)
            logger.info("Request body: %s", request_body)

            return create_response_with_cookie(
                body={
                    "status": "success",
                    "message": "Authentication endpoint placeholder",
                },
                origin=origin,
            )

        except Exception as exc:  # pragma: no cover - defensive logging
            import traceback

            logger.error("Error in authenticate: %s", exc)
            logger.error("Traceback: %s", traceback.format_exc())
            return Response(
                body={"error": "Internal server error", "details": str(exc)},
                status_code=500,
                headers={
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": app.current_request.headers.get(
                        "origin", "http://localhost:3000"
                    ),
                    "Access-Control-Allow-Credentials": "true",
                },
            )

    @app.route("/health", methods=["GET"])
    def health_check():
        """Health check endpoint."""

        return {"status": "healthy", "service": "shopping-assistant-api"}
