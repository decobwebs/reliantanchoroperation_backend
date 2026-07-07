from typing import Optional, Dict, Any, List
from datetime import datetime
import uuid
import httpx
from jose import jwt, jwk, JWTError
from jose.utils import base64url_decode
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, status

from app.config import settings
from app.models.user import User
from app.models.enums import UserRole

# ── JWKS cache ────────────────────────────────────────────────────────────────
_jwks_cache: Optional[List[Dict]] = None


def _service_unavailable(exc: httpx.RequestError, service: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            f"{service} is unreachable. Check SUPABASE_URL, DNS/internet "
            f"connectivity, and firewall/VPN settings. ({exc.__class__.__name__})"
        ),
    )


def _error_detail(response: httpx.Response, fallback: str) -> str:
    """Extract an error message from a Supabase response without assuming JSON.

    Supabase/GoTrue normally returns JSON errors, but gateways, paused projects,
    and 5xx pages can return empty or HTML bodies. Falls back gracefully so the
    error path never raises JSONDecodeError.
    """
    try:
        data = response.json()
    except ValueError:
        return fallback
    if isinstance(data, dict):
        return (
            data.get("error_description")
            or data.get("msg")
            or data.get("message")
            or fallback
        )
    return fallback


async def _get_jwks(force_refresh: bool = False) -> List[Dict]:
    """Fetch and cache Supabase JWKS keys. force_refresh re-fetches (e.g. on key rotation)."""
    global _jwks_cache
    if _jwks_cache is not None and not force_refresh:
        return _jwks_cache
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(settings.jwks_url, timeout=10.0)
            resp.raise_for_status()
            _jwks_cache = resp.json().get("keys", [])
    except httpx.RequestError as exc:
        raise _service_unavailable(exc, "Supabase JWKS") from exc
    return _jwks_cache


def _find_jwk(keys: List[Dict], kid: Optional[str]) -> Optional[Dict]:
    """Match a signing key strictly by kid. Only fall back to a lone key when the
    token carries no kid. Never guess keys[0] on a kid mismatch."""
    if kid:
        for key_data in keys:
            if key_data.get("kid") == kid:
                return key_data
        return None
    return keys[0] if len(keys) == 1 else None


class AuthService:
    """Handles Supabase Auth integration and JWT validation."""

    SUPABASE_AUTH_URL = f"{settings.SUPABASE_URL}/auth/v1"

    @staticmethod
    async def decode_jwt(token: str) -> Dict[str, Any]:
        """Decode and validate a Supabase JWT using JWKS."""
        try:
            # Get unverified header to find matching key
            header = jwt.get_unverified_header(token)
            token_kid = header.get("kid")
            # Pin the algorithm server-side — never trust the token's own `alg`.
            token_alg = settings.JWT_ALGORITHM

            keys = await _get_jwks()
            matching_key = _find_jwk(keys, token_kid)

            if matching_key is None and token_kid:
                # kid not in cache — signing keys may have rotated. Refresh once.
                keys = await _get_jwks(force_refresh=True)
                matching_key = _find_jwk(keys, token_kid)

            if matching_key is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Signing key not found for token",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            public_key = jwk.construct(matching_key, algorithm=token_alg)

            payload = jwt.decode(
                token,
                public_key,
                algorithms=[token_alg],
                options={"verify_aud": False},
            )
            return payload

        except JWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid or expired token: {str(exc)}",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Token verification failed: {str(exc)}",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @staticmethod
    async def get_user_from_token(token: str, db: AsyncSession) -> User:
        """Extract user from JWT and fetch from database."""
        payload = await AuthService.decode_jwt(token)
        auth_id_str = payload.get("sub")
        if not auth_id_str:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing subject claim",
            )

        try:
            auth_id = uuid.UUID(auth_id_str)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid user ID in token",
            )

        result = await db.execute(select(User).where(User.auth_id == auth_id))
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found in system",
            )

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is deactivated",
            )

        return user

    @staticmethod
    async def _delete_supabase_auth_user(auth_id: uuid.UUID) -> None:
        """Best-effort deletion of a Supabase auth user — used to undo a failed local sync
        so an orphaned auth account can't wedge re-registration."""
        try:
            async with httpx.AsyncClient() as client:
                await client.delete(
                    f"{AuthService.SUPABASE_AUTH_URL}/admin/users/{auth_id}",
                    headers={
                        "apikey": settings.SUPABASE_SERVICE_KEY,
                        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
                    },
                    timeout=30.0,
                )
        except Exception:
            pass  # best-effort; must not mask the original error

    @staticmethod
    async def register(email: str, password: str, full_name: str, phone: Optional[str], role: UserRole, db: AsyncSession) -> Dict[str, Any]:
        """Register a new user via Supabase Auth and sync to local DB."""
        try:
            async with httpx.AsyncClient() as client:
                # Create user in Supabase Auth using service key
                response = await client.post(
                    f"{AuthService.SUPABASE_AUTH_URL}/admin/users",
                    headers={
                        "apikey": settings.SUPABASE_SERVICE_KEY,
                        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "email": email,
                        "password": password,
                        "email_confirm": True,
                        "user_metadata": {"full_name": full_name},
                    },
                    timeout=60.0,
                )
        except httpx.RequestError as exc:
            raise _service_unavailable(exc, "Supabase Auth") from exc

        if response.status_code not in (200, 201):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_error_detail(response, "Registration failed"),
            )

        supabase_user = response.json()
        auth_id = uuid.UUID(supabase_user["id"])

        try:
            # Check if already synced (edge case)
            existing = await db.execute(select(User).where(User.auth_id == auth_id))
            if existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="User already registered",
                )

            # Guard against a local email collision under a different auth_id
            email_existing = await db.execute(select(User).where(User.email == email))
            if email_existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A user with this email already exists",
                )

            user = User(
                auth_id=auth_id,
                email=email,
                full_name=full_name,
                phone=phone,
                role=role,
            )
            db.add(user)
            await db.flush()
            await db.refresh(user)
            return user
        except Exception:
            # Local sync failed — undo the Supabase auth account we just created so it
            # isn't orphaned (which would block re-registration with the same email).
            await db.rollback()
            await AuthService._delete_supabase_auth_user(auth_id)
            raise

    @staticmethod
    async def login(email: str, password: str) -> Dict[str, Any]:
        """Authenticate user via Supabase Auth and return tokens."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{AuthService.SUPABASE_AUTH_URL}/token?grant_type=password",
                    headers={
                        "apikey": settings.SUPABASE_ANON_KEY,
                        "Content-Type": "application/json",
                    },
                    json={"email": email, "password": password},
                    timeout=60.0,
                )
        except httpx.RequestError as exc:
            raise _service_unavailable(exc, "Supabase Auth") from exc

        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=_error_detail(response, "Invalid credentials"),
            )

        return response.json()

    @staticmethod
    async def refresh_token(refresh_token: str) -> Dict[str, Any]:
        """Refresh access token using refresh token."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{AuthService.SUPABASE_AUTH_URL}/token?grant_type=refresh_token",
                    headers={
                        "apikey": settings.SUPABASE_ANON_KEY,
                        "Content-Type": "application/json",
                    },
                    json={"refresh_token": refresh_token},
                    timeout=60.0,
                )
        except httpx.RequestError as exc:
            raise _service_unavailable(exc, "Supabase Auth") from exc

        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token",
            )

        return response.json()

    @staticmethod
    async def logout(access_token: str) -> None:
        """Invalidate the user's session in Supabase."""
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{AuthService.SUPABASE_AUTH_URL}/logout",
                    headers={
                        "apikey": settings.SUPABASE_ANON_KEY,
                        "Authorization": f"Bearer {access_token}",
                    },
                    timeout=60.0,
                )
        except httpx.RequestError as exc:
            raise _service_unavailable(exc, "Supabase Auth") from exc

    @staticmethod
    async def change_password(access_token: str, new_password: str) -> None:
        """Change user password via Supabase Auth."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.put(
                    f"{AuthService.SUPABASE_AUTH_URL}/user",
                    headers={
                        "apikey": settings.SUPABASE_ANON_KEY,
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json={"password": new_password},
                    timeout=60.0,
                )
        except httpx.RequestError as exc:
            raise _service_unavailable(exc, "Supabase Auth") from exc

        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to change password",
            )

    @staticmethod
    async def forgot_password(email: str) -> None:
        """Send password reset email via Supabase Auth."""
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{AuthService.SUPABASE_AUTH_URL}/recover",
                    headers={
                        "apikey": settings.SUPABASE_ANON_KEY,
                        "Content-Type": "application/json",
                    },
                    json={"email": email},
                    timeout=60.0,
                )
        except httpx.RequestError:
            pass
        # Always succeed to prevent email enumeration

    @staticmethod
    async def reset_password(token: str, new_password: str) -> None:
        """Reset password using a recovery token."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.put(
                    f"{AuthService.SUPABASE_AUTH_URL}/user",
                    headers={
                        "apikey": settings.SUPABASE_ANON_KEY,
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={"password": new_password},
                    timeout=60.0,
                )
        except httpx.RequestError as exc:
            raise _service_unavailable(exc, "Supabase Auth") from exc

        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token",
            )
