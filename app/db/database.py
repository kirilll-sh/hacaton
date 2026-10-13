from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import DATABASE_URL
from app.db.models import Base

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
async_session = async_sessionmaker(engine, expire_on_commit=False)
_schema_initialized = False
_schema_lock = asyncio.Lock()


async def create_db_and_tables() -> None:
    global _schema_initialized
    if _schema_initialized:
        return
    async with _schema_lock:
        if not _schema_initialized:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            _schema_initialized = True


async def close_database() -> None:
    global _schema_initialized
    await engine.dispose()
    _schema_initialized = False
