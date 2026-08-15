import socket

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import MetaData
from app.config import settings

# Naming convention for constraints (useful for alembic autogenerate)
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=convention)


class Base(DeclarativeBase):
    metadata = metadata


# asyncpg with PgBouncer session pooler requires statement_cache_size=0.
#
# CONNECTION BUDGET — Supabase's session-mode pooler caps this project at 15
# total connections, and the limit is per-PROJECT, not per-process. Render
# runs uvicorn with --workers 2, and each worker gets its own independent
# pool, so the real ceiling is:
#
#     workers x (pool_size + max_overflow)  <=  15, with headroom
#
# The previous 3+5 was sized as though there were one worker: 2 x 8 = 16,
# one over the cap. Under load both workers filled, the 16th connection was
# refused with EMAXCONNSESSION, and requests died with an unhandled 500 —
# which strips CORS headers off the response, so the browser reported it as
# a CORS failure and sent debugging in entirely the wrong direction.
#
# 2+3 => 2 x 5 = 10, leaving 5 spare for the pre-deploy `alembic upgrade
# head`, the /health/deep check, and any ad-hoc tooling connecting at the
# same time. Raising these numbers without re-checking the worker count and
# the pooler cap will reproduce the outage.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.is_development,
    pool_pre_ping=True,
    pool_size=2,
    max_overflow=3,
    pool_timeout=10,
    connect_args={"statement_cache_size": 0},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncSession:
    """FastAPI dependency that provides an async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as error:
            await session.rollback()
            exc = error
            while exc:
                if isinstance(exc, socket.gaierror):
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail=(
                            "Database host could not be resolved (DNS). Check DATABASE_URL, "
                            "DNS/internet connectivity, and the Supabase pooler hostname."
                        ),
                    )
                # The pooler resolves and accepts TCP but rejects the tenant. This is
                # NOT a DNS problem — reporting it as one sends debugging the wrong way.
                msg = str(exc)
                if "ENOTFOUND" in msg or "Tenant or user not found" in msg.lower().replace("tenant/user", "tenant or user"):
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail=(
                            "Database pooler rejected the connection: tenant/user not found. "
                            "DNS and the network are fine. This is USUALLY a transient Supabase "
                            "pooler fault that clears on its own — retry in a few minutes before "
                            "changing anything. Only if it persists, verify the pooler host/user "
                            "against Supabase → Connect → Session pooler."
                        ),
                    )
                exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
            raise
        finally:
            await session.close()
