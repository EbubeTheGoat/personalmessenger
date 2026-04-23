import openai
from thefuzz import fuzz

def is_semantically_unique(new_summary: str, history_summaries: list[str]) -> bool:
    for old_sum in history_summaries:
        if fuzz.ratio(new_summary.lower(), old_sum.lower()) > 85:
            return False

    if not history_summaries:
        return True

    recent_history = history_summaries[-20:]

    try:
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a news deduplicator. Reply UNIQUE or REPEAT only."
                },
                {
                    "role": "user",
                    "content": f"New article summary: {new_summary}\n\nRecent history:\n{recent_history}"
                }
            ]
        )
        return "UNIQUE" in response.choices[0].message.content.upper()
    except Exception:
        return True  