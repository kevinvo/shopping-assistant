"""REST API handlers for session management and authentication."""

import logging
from dataclasses import asdict
from chalice import Response, CORSConfig
from chalicelib.session_handler import SessionHandler
from chalicelib.error_notifications import notify_on_exception

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# CORS configuration
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

# Initialize session handler
session_handler = SessionHandler()


def create_response_with_cookie(
    body: dict, session_id: str = None, origin: str = None
) -> Response:
    """Create a response with optional session cookie"""
    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": origin or "http://localhost:3000",
        "Access-Control-Allow-Credentials": "true",
    }

    if session_id:
        # Check if request is from localhost
        is_localhost = origin and origin.startswith("http://localhost")

        if is_localhost:
            # Less strict settings for localhost
            headers["Set-Cookie"] = (
                f"session_id={session_id}; Path=/; HttpOnly; SameSite=Lax"
            )
        else:
            # Strict settings for production
            headers["Set-Cookie"] = (
                f"session_id={session_id}; Path=/; HttpOnly; Secure; SameSite=None"
            )

    logger.info(f"Response headers: {headers}")
    return Response(body=body, status_code=200, headers=headers)


def register_rest_routes(app):
    """Register all REST API routes with the Chalice app."""

    @app.route("/session", methods=["GET"], cors=cors_config)
    @notify_on_exception
    def validate_session():
        """Validate or create a new session"""
        try:
            # Get cookies from headers
            cookies = app.current_request.headers.get("cookie")
            origin = app.current_request.headers.get("origin", "http://localhost:3000")

            logger.info(f"Session validation request from origin: {origin}")
            logger.info(f"Cookies: {cookies}")

            # Extract session ID from cookies
            session_id = session_handler.get_session_id(cookies)

            # Get session info if session_id exists
            session_info = (
                session_handler.get_session_info(session_id) if session_id else None
            )
            session_data = session_info.data if session_info else None

            # Validate session
            session_data, response, new_session_id = session_handler.validate_session(
                session_data=session_data, session_id=session_id
            )

            # Store session if new
            if new_session_id and new_session_id != session_id:
                session_handler.store_session(
                    session_id=new_session_id, session_data=session_data
                )
                session_id = new_session_id
            elif session_id and session_data:
                # Update existing session
                session_handler.store_session(
                    session_id=session_id, session_data=session_data
                )

            response_body = asdict(response)
            return create_response_with_cookie(
                body=response_body, session_id=session_id, origin=origin
            )

        except Exception as e:
            import traceback

            logger.error(f"Error in validate_session: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return Response(
                body={"error": "Internal server error", "details": str(e)},
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
        """Authenticate user and create session"""
        try:
            # Get request body
            request_body = app.current_request.json_body or {}
            origin = app.current_request.headers.get("origin", "http://localhost:3000")

            logger.info(f"Auth request from origin: {origin}")
            logger.info(f"Request body: {request_body}")

            # For now, this is a placeholder
            # In the original code, this endpoint wasn't fully implemented
            # You can add authentication logic here

            return create_response_with_cookie(
                body={
                    "status": "success",
                    "message": "Authentication endpoint placeholder",
                },
                origin=origin,
            )

        except Exception as e:
            import traceback

            logger.error(f"Error in authenticate: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return Response(
                body={"error": "Internal server error", "details": str(e)},
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
        """Health check endpoint"""
        return {"status": "healthy", "service": "shopping-assistant-api"}
