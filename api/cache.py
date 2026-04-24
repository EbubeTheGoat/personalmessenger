from api.logging_config import get_logger
import redis
from dotenv import load_dotenv
load_dotenv()
import json, os 
import os
import redis
import logging
from redis.exceptions import RedisError

ENV = os.getenv("ENV", "development")
REDIS_URL = os.getenv("REDIS_URL")
logger = get_logger("cache2")

if not REDIS_URL:
    if ENV == "production":
        raise RuntimeError("REDIS_URL is required in production")
    REDIS_URL = "redis://localhost:6379"
    logger.warning("REDIS_URL not set. Using local Redis for development")



def create_redis_client(url: str) -> redis.Redis:
    try:
        pool = redis.ConnectionPool.from_url(
            url,
            decode_responses=True,
            socket_timeout=2,
            retry_on_timeout=True,
        )

        client = redis.Redis(connection_pool=pool)

        client.ping()

        logger.info("Successfully connected to Redis")
        return client

    except (ValueError, RedisError) as e:
        logger.exception(f"Redis connection failed for URL: {url}")
        raise RuntimeError("Redis initialization failed") from e

redis_client = create_redis_client(REDIS_URL)

def get_user_cache(phone: str):
    try:
        data = redis_client.get(f"user:{phone}")
        if data:
            return json.loads(data)
        return None                                         
    except (redis.ConnectionError,redis.TimeoutError) as e:
        logger.error(f"Redis get error for phone {phone}: {e}")
        return None
    
def set_user_cache(phone: str, data: dict):
    try:
        redis_client.set(f"user:{phone}", json.dumps(data), ex=3600)
    except redis.RedisError as e:
        logger.error(f"Redis set error for phone {phone}: {e}")