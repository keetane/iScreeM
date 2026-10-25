from redis import Redis
from rq import Queue

from ..config import settings


def redis_connection() -> Redis:
    return Redis.from_url(settings.redis_url, socket_connect_timeout=2)


def queue(name: str = "default") -> Queue:
    connection = redis_connection()
    connection.ping()
    return Queue(name, connection=connection, default_timeout=settings.command_timeout_seconds)

