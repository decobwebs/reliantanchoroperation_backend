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


# asyncpg with PgBouncer session pooler requires statement_cache_size=0
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.is_development,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
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
                            "DNS and the network are fine — the pooler connection string is stale. "
                            "Copy the current URI from Supabase → Project Settings → Database → "
                            "Connection pooling, and update DATABASE_URL / SYNC_DATABASE_URL."
                        ),
                    )
                exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
            raise
        finally:
            await session.close()
