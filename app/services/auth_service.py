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


async def _get_jwks() -> List[Dict]:
    """Fetch and cache Supabase JWKS keys."""
    global _jwks_cache
    if _jwks_cache is not None:
        return _jwks_cache
    async with httpx.AsyncClient() as client:
        resp = await client.get(settings.jwks_url, timeout=10.0)
        resp.raise_for_status()
        _jwks_cache = resp.json().get("keys", [])
    return _jwks_cache


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
            token_alg = header.get("alg", "ES256")

            keys = await _get_jwks()

            # Find matching key by kid
            matching_key = None
            for key_data in keys:
                if token_kid and key_data.get("kid") == token_kid:
                    matching_key = key_data
                    break

            if not matching_key and keys:
                matching_key = keys[0]  # fallback to first key

            if not matching_key:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="No JWKS keys available for JWT verification",
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
    async def register(email: str, password: str, full_name: str, phone: Optional[str], role: UserRole, db: AsyncSession) -> Dict[str, Any]:
        """Register a new user via Supabase Auth and sync to local DB."""
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

        if response.status_code not in (200, 201):
            error_detail = response.json().get("msg", "Registration failed")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_detail,
            )

        supabase_user = response.json()
        auth_id = uuid.UUID(supabase_user["id"])

        # Check if already synced (edge case)
        existing = await db.execute(select(User).where(User.auth_id == auth_id))
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User already registered",
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

    @staticmethod
    async def login(email: str, password: str) -> Dict[str, Any]:
        """Authenticate user via Supabase Auth and return tokens."""
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

        if response.status_code != 200:
            error_detail = response.json().get("error_description", "Invalid credentials")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=error_detail,
            )

        return response.json()

    @staticmethod
    async def refresh_token(refresh_token: str) -> Dict[str, Any]:
        """Refresh access token using refresh token."""
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

        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token",
            )

        return response.json()

    @staticmethod
    async def logout(access_token: str) -> None:
        """Invalidate the user's session in Supabase."""
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{AuthService.SUPABASE_AUTH_URL}/logout",
                headers={
                    "apikey": settings.SUPABASE_ANON_KEY,
                    "Authorization": f"Bearer {access_token}",
                },
                timeout=60.0,
            )

    @staticmethod
    async def change_password(access_token: str, new_password: str) -> None:
        """Change user password via Supabase Auth."""
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

        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to change password",
            )

    @staticmethod
    async def forgot_password(email: str) -> None:
        """Send password reset email via Supabase Auth."""
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
        # Always succeed to prevent email enumeration

    @staticmethod
    async def reset_password(token: str, new_password: str) -> None:
        """Reset password using a recovery token."""
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

        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token",
            )
