from redis import Redis
from rq import Queue

from app.core.config import get_settings

_redis_conn: Redis | None = None
_queue: Queue | None = None


def get_redis_connection() -> Redis:
    global _redis_conn
    if _redis_conn is None:
        _redis_conn = Redis.from_url(get_settings().REDIS_URL)
    return _redis_conn


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = Queue("default", connection=get_redis_connection())
    return _queue
