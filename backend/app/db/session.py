from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

settings = get_settings()

# NullPool: no connections are held open/reused between requests. This avoids
# asyncpg connections becoming bound to an event loop that later closes (a
# real failure mode across test runs and worker/reload cycles), at the cost
# of a fresh connection per checkout. Revisit if profiling under real load
# in Phase 11 shows this is a bottleneck.
engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool, future=True)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
