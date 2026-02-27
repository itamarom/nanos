from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.config import DATABASE_URL, DATABASE_URL_SYNC

# Async engine (for FastAPI services)
async_engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

# Sync engine (for Celery worker, CLI, Alembic)
sync_engine = create_engine(DATABASE_URL_SYNC, echo=False, pool_pre_ping=True)
SyncSessionLocal = sessionmaker(sync_engine, expire_on_commit=False)


async def get_async_session():
    async with AsyncSessionLocal() as session:
        yield session


def get_sync_session():
    session = SyncSessionLocal()
    try:
        yield session
    finally:
        session.close()
