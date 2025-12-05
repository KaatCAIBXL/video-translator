"""Simple session-based role management for video-translator."""
from typing import Optional, Dict
from fastapi import Request
import secrets

# Simple in-memory session store (in production, use proper session storage)
_sessions: Dict[str, str] = {}  # session_id -> role

def create_session(role: str) -> str:
    """Create a new session with the given role."""
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = role
    return session_id

def get_role_from_session(session_id: Optional[str]) -> Optional[str]:
    """Get the role for a session ID."""
    if not session_id:
        return None
    return _sessions.get(session_id)

def get_role_from_request(request: Request) -> Optional[str]:
    """Get the role from the request's session cookie."""
    session_id = request.cookies.get("session_id")
    return get_role_from_session(session_id)

def is_editor(request: Request) -> bool:
    """Check if the user is an editor (I-tech).
    
    Users are considered editors if:
    1. They have editor or admin role in session, OR
    2. They have the 'module=itech' cookie (accessing I-tech privé module), OR
    3. They have ANY role (viewer, editor, admin) - this means they're on the index page (I-tech privé), not home
    """
    # Check if user is accessing I-tech privé module (cookie-based)
    module = request.cookies.get("module")
    if module == "itech":
        return True
    
    # Check if user has ANY role - if so, they're on index page (I-tech privé), so they're editor
    # The home page shows when there's no role, so if there's a role, they're in I-tech privé
    role = get_role_from_request(request)
    if role:  # Any role (viewer, editor, admin) means they're on index page, so editor
        return True
    
    # Fallback: check role-based access (shouldn't be needed with above logic)
    return role == "editor" or role == "admin"  # Admins can also edit

def is_admin(request: Request) -> bool:
    """Check if the user is an admin."""
    role = get_role_from_request(request)
    return role == "admin"

def is_viewer(request: Request) -> bool:
    """Check if the user is a viewer (les saints du MAI)."""
    role = get_role_from_request(request)
    return role == "viewer" or role == "editor" or role == "admin"  # All roles can view

def can_generate_video(request: Request) -> bool:
    """Check if the user can generate videos (admin only)."""
    role = get_role_from_request(request)
    return role == "admin"

def can_manage_characters(request: Request) -> bool:
    """Check if the user can manage characters (admin only)."""
    role = get_role_from_request(request)
    return role == "admin"

def can_read_admin_messages(request: Request) -> bool:
    """Check if the user can read admin messages (admin only)."""
    role = get_role_from_request(request)
    return role == "admin"

def get_session_count() -> int:
    """Get the number of active sessions (for debugging)."""
    return len(_sessions)

def session_exists(session_id: Optional[str]) -> bool:
    """Check if a session ID exists in the store."""
    if not session_id:
        return False
    return session_id in _sessions

