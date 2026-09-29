"""RQ worker entrypoint, run as a separate container/process from the API.

Usage: python worker.py
"""

from rq import Worker

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.jobs.queue import get_redis_connection

if __name__ == "__main__":
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)

    worker = Worker(["default"], connection=get_redis_connection())
    worker.work(with_scheduler=False)
