"""Minimal Redis worker bootstrap. It intentionally processes no clinical data."""

import logging
import os

from redis import Redis

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("sahyog.worker")


def main() -> None:
    if os.getenv("SYNTHETIC_DATA_ONLY", "true").lower() != "true":
        raise RuntimeError("This bootstrap worker is restricted to synthetic data")

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    queue_name = os.getenv("WORKER_QUEUE", "bootstrap:noop")
    client = Redis.from_url(redis_url, decode_responses=True)
    client.ping()
    logger.info("Worker ready; waiting on bootstrap queue %s", queue_name)

    while True:
        item = client.blpop(queue_name, timeout=30)
        if item is not None:
            # Do not log queue payloads: they may contain sensitive data in later steps.
            logger.warning("Ignoring queued item: bootstrap worker has no task handlers")


if __name__ == "__main__":
    main()