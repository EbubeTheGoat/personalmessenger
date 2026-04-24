import hashlib
import os
import os
import json
import logging
import hashlib
import re
import sentry_sdk

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Absolute imports for Vercel
from api.database import SessionLocal, User, SentContent
import api.cache
from api.newsduplicator import is_unique_message, store_hash, is_unique, cosine_similarity, store_embeddings

load_dotenv()

# --- Configuration ---
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") # Replaced Twilio keys

# Headers to prevent being blocked by news sites
SCRAPE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}
logger = logging.getLogger("worker")
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

session = requests.Session()
session.headers.update(SCRAPE_HEADERS)

def search_web(query: str) -> list[str]:
    """Uses SerpAPI to find the latest links on a topic."""
    url = "https://serpapi.com/search.json"
    params = {
        "q": query,
        "api_key": SERPAPI_API_KEY,
        "engine": "google_news",
        "num": 6,
        "gl": "us",
        "tbs": "qdr:d" 
    }
    try:
        response = session.get(url, params=params, timeout=45)
        data = response.json()
        return [r["link"] for r in data.get("news_results", []) if "link" in r]
    except Exception as e:
        logger.error(f"Search failed: {e}")
        return []
def scrape_and_summarize(url: str) -> dict:
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    
    # 1. Global Cache Check
    cached = api.cache.get_user_cache(f"summary:{url_hash}")
    if cached:
        return cached
    try:
        res = session.get(url=url,timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "aside"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)[:4500]

        response = openai_client.chat.completions.create(
            model=os.getenv("LLM_MODEL", "gpt-4o-mini"), # FIXED: Removed tuple
            messages= [{"role": "user", "content": f"Summarize this in ONE sentence:\n\n{text}"}],
            max_tokens=100
        )
        summary = response.choices[0].message.content.strip()
        result = {"url": url,"summary": summary}
        
        # FIXED: Changed from get_user_cache to set_user_cache and passed the dict
        api.cache.set_user_cache(f"summary:{url_hash}", result) 
        
        return result
    except Exception as e:
        logger.warning(f"Failed to summarise: {e}")
        return None

@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=4),
    retry=retry_if_exception_type((TimeoutError, ConnectionError)),
)


def send_telegram(chat_id: str, message: str) -> bool:
    """Delivers the update via Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Telegram failed for {chat_id}: {e}")
        return False
    
def run_ai_research(topic: str, seen_hashes: set) -> list[dict]:
    """Coordinates search and scraping while pre-filtering seen content."""
    # 1. Define 'Fast' sources (Reddit, Hacker News, Tech blogs)
    # You can customize this list!
    fast_sources = "(site:reddit.com OR site:news.ycombinator.com OR site:techcrunch.com OR site:theverge.com)"
    
    # 2. Merge topic with sources
    # Example query: "AI Agents (site:reddit.com OR site:techcrunch.com...)"
    optimized_query = f"{topic} {fast_sources}"
    
    urls = search_web(optimized_query)
    
    # Filter out URLs the user has already seen before scraping
    scraped = []
    for url in urls:
        u_hash = hashlib.sha256(url.encode()).hexdigest()
        if u_hash not in seen_hashes:
            data = scrape_and_summarize(url)
            if data:
                scraped.append(data)
            if len(scraped) >= 3: # Limit to 3 to avoid Vercel timeout
                break

    if not scraped:
        return []

    # Final Relevance Check via LLM
    response = openai_client.chat.completions.create(
        model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        response_format={ "type": "json_object" },
        messages=[
            {
                "role": "system",
                "content": "You are a curator. Return a JSON object with a 'results' key containing the most relevant items."
            },
            {
                "role": "user",
                "content": f"Topic: {topic}\nData: {json.dumps(scraped)}"
            }
        ]
    )
    return json.loads(response.choices[0].message.content).get("results", [])

def job_fetch_and_send():
    """Main loop: Fetches users and delivers personalized research."""
    with sentry_sdk.start_transaction(op="task", name="job_fetch_and_send"):
        db = SessionLocal()
        try:
            # Only process users who have selected a topic
            users = db.query(User).filter(User.notification_topic != None).all()
            
            for user in users:
                topics = [t.strip() for t in re.split(r',|;\band\b', user.notification_topic) if t.strip()]
                # 1. Fetch user history

                history = db.query(SentContent).filter(SentContent.user_id == user.id).all()
                hist_urls = {h.url_hash for h in history}
                hist_sums = [h.summary for h in history]

                # 2. Run Agentic Research
                try:
                    scraped_data = []
                    for ideas in topics:
                        scraped = run_ai_research(ideas, hist_urls)
                        scraped_data.extend(scraped)
                except Exception:
                    continue

                to_send = []
                for item in scraped_data:
                    url = item.get('url')
                    summary = item.get('summary')
                    url_hash = hashlib.sha256(url.encode()).hexdigest()

                    # 3. Semantic Deduplication
                    if is_unique(summary):
                        to_send.append(item)
                        db.add(SentContent(user_id=user.id, url_hash=url_hash, summary=summary))
                        hist_sums.append(summary)

                # 4. Delivery
                if to_send:
                    lines = [f"<b>• {i['summary']}</b>\n{i['url']}" for i in to_send]
                    message = f"🔍 <b>UpToDate: {user.notification_topic}</b>\n\n" + "\n\n".join(lines)
                    
                    if send_telegram(user.phone_number, message): # phone_number column now holds Chat ID
                        db.commit()
                        logger.info(f"Delivered to {user.phone_number}")
                else:
                    logger.info(f"No new content for {user.phone_number}. Sending fallback message.")
                    
                    fallback_msg = (
                        f"🕵️‍♂️ <b>Intelligence Agency Update: {user.notification_topic}</b>\n\n"
                        f"I scanned the web today, but there is no new "
                        f"information or breaking news to report since my last update.\n\n"
                        f"I will keep monitoring!"
                    )
                    
                    # Send it via Telegram using your existing function
                    send_telegram(user.phone_number, fallback_msg)
        except Exception as e:
            sentry_sdk.capture_exception(e)
        finally:
            db.close()
