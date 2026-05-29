from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fernkam.config import get_settings

_async_engine = None
_sync_engine = None


def get_async_engine():
    global _async_engine
    if _async_engine is None:
        settings = get_settings()
        _async_engine = create_async_engine(
            settings.pg_url,
            pool_size=50,
            max_overflow=100,
            pool_pre_ping=True,
            echo=settings.debug,
        )
    return _async_engine


def get_sync_engine():
    global _sync_engine
    if _sync_engine is None:
        settings = get_settings()
        _sync_engine = create_engine(
            settings.pg_url_sync,
            pool_size=25,
            max_overflow=50,
            pool_pre_ping=True,
            echo=settings.debug,
        )
    return _sync_engine


AsyncSessionLocal = async_sessionmaker(
    bind=None,
    class_=AsyncSession,
    expire_on_commit=False,
)

SyncSessionLocal = sessionmaker(autocommit=False, autoflush=False)


def get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    factory = async_sessionmaker(
        bind=get_async_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return factory


def get_sync_session_factory() -> sessionmaker:
    return sessionmaker(bind=get_sync_engine(), autocommit=False, autoflush=False)


def async_session_factory() -> AsyncSession:
    """Convenience callable used by FastAPI deps: `async with async_session_factory() as session`."""
    return get_async_session_factory()()
