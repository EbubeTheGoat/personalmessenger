from typing import List

from redis import RedisError
from thefuzz import fuzz
import requests
import hashlib
from api.cache import redis_client
from logging import getLogger
from openai import OpenAI
import numpy as np
import json

logger = getLogger("Duplicator")

client = OpenAI()

def hash_summary(summary : str) -> str:
    """Creates a hash of the summary for quick comparison."""
    return hashlib.sha256(summary.lower().encode()).hexdigest()

def is_unique_message(text: str) -> bool:
    key = f"unique{hash_summary(text)}"
    try:
        return redis_client.exists(key)  # Check if key exists
    except RedisError as e:
        logger.error(f"Redis error during uniqueness check: {e}")
        return False

def store_hash(text: str):
    key = f"summary_hash:{hash_summary(text)}"
    try:
        redis_client.set(key, 1, ex=86400)  # 1 day TTL
    except RedisError as e:
        logger.error(f"Redis error storing hash: {e}")
    
def get_embedding(text: str) -> list[float]:
    try:
        request = client.embeddings.create(
            model="text-embedding-3-small",
            input=text
            )
        return request.data[0].embedding
    except Exception as e:
        logger.error(f"Error getting embedding: {e}")
        return []
    
def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    if not vec1 or not vec2:
        return 0.0
    vec1 = np.array(vec1)
    vec2 = np.array(vec2)       
    dot_product = np.dot(vec1, vec2)
    norm_vec1 = np.linalg.norm(vec1)
    norm_vec2 = np.linalg.norm(vec2)
    if norm_vec1 == 0 or norm_vec2 == 0:
        return 0.0
    return dot_product / (norm_vec1 * norm_vec2)

VECTOR_KEY = "news_vectors"
def store_embeddings(text: str,embedding: list[float]):
    try:
        redis_client.rpush(VECTOR_KEY, json.dumps({"text": text, "embedding": embedding}))
    except RedisError as e:
        logger.error(f"Error storing embedding: {e}")



def get_recent_embeddings(limit: int = 50):
    try:
        items = redis_client.lrange(VECTOR_KEY, -limit, -1)
        return [json.loads(item) for item in items]
    except RedisError as e:
        logger.error(f"Redis error fetching embeddings: {e}")
        return []
    


def llm_dedup_check(new_summary: str, candidates: List[str]) -> bool:
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Reply ONLY with UNIQUE or REPEAT."
                },
                {
                    "role": "user",
                    "content": f"New summary:\n{new_summary}\n\nCompare with:\n{candidates}"
                }
            ],
            timeout=3
        )

        result = response.choices[0].message.content.strip().upper()
        return result == "UNIQUE"

    except Exception as e:
        logger.error(f"LLM dedup error: {e}")
        return True  # fail open



def is_unique(summary: str) -> bool:

    if is_unique_message(summary):
        logger.info("Duplicate detected via hash")
        return False

    embedding = get_embedding(summary)
    if not embedding:
        logger.warning("Embedding failed, accepting as unique")
        return True

    recent_items = get_recent_embeddings(limit=50)

    similarities = []
    for item in recent_items:
        sim = cosine_similarity(embedding, item["embedding"])
        similarities.append((sim, item["text"]))

        if sim > 0.9:
            logger.info("Duplicate detected via embedding similarity")
            return False

    borderline = [text for sim, text in similarities if sim > 0.75]

    if borderline:
        is_unique_llm = llm_dedup_check(summary, borderline)
        if not is_unique_llm:
            logger.info("Duplicate detected via LLM")
            return False

    store_hash(summary)
    store_embeddings(summary, embedding)

    logger.info("Summary accepted as unique")
    return True