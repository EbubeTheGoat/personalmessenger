import redis, json, logging, os

logger = logging.getLogger("research_worker")

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
try:
    pool = redis.ConnectionPool.from_url(
        redis_url,
        decode_responses = True,
        socket_timeout = 2
    )
    r = redis.Redis(connection_pool=pool)
except ValueError as e:
    logger.error(f"Invalid REDIS_URL '{redis_url}': {e}. Falling back to redis://localhost:6379")
    pool = redis.ConnectionPool.from_url(
        "redis://localhost:6379",
        decode_responses = True,
        socket_timeout = 2
    )
    r = redis.Redis(connection_pool=pool)

def get_user_cache(phone: str):
    try:
        data = r.get(f"user:{phone}")
        return json.loads(data) if data else None
    except (redis.ConnectionError, redis.TimeoutError) as e:
        logger.error(f"Redis get error: {e}")
        return None

def set_user_cache(phone: str, data: dict):
    try:
        r.set(f"user:{phone}", json.dumps(data), ex=3600)
    except Exception as e:
        logger.error(f"Redis set error: {e}")
