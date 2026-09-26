# SPDX-License-Identifier: AGPL-3.0-or-later
"""
RAG Journal — Core module.

Provides the shared pieces the rest of the system builds on:
    JOURNAL_DIR        — where human-readable markdown backups live
    get_collection()   — persistent ChromaDB collection for semantic search
    extract_metadata() — pull people, topics, mood, key_events from entry text
    query_journal()    — semantic search over everything imported

Configuration (all optional, via .env or environment) lives in config.py:
    MC_JOURNAL_DIR    — markdown backup directory (default: ./journal_entries)
    MC_CHROMA_DIR     — vector store directory (default: ./chroma_data)
"""

import re
from datetime import datetime, timezone

from config import CHROMA_DIR, JOURNAL_DIR

COLLECTION_NAME = "journal_entries"


# ---------------------------------------------------------------------------
# VECTOR STORE
# ---------------------------------------------------------------------------

def get_collection():
    """
    Return the persistent ChromaDB collection for journal entries.

    Uses ChromaDB's default local embedding model (all-MiniLM-L6-v2),
    so everything stays on this machine — no API calls, no cost.
    """
    # Imported lazily so extract_metadata() works without chromadb installed
    import chromadb

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def get_summary_collection():
    """
    The zoomed-out sibling of the main collection: entry summaries, weekly
    arcs, domain documents, and entity docs, each embedded whole. Chunks
    answer "find me that moment"; these answer "what was going on".
    Kept separate so whole-collection reads of journal_entries (entity
    extraction, conversation reassembly) never see summary documents.
    """
    import chromadb

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_or_create_collection(
        name="journal_summaries",
        metadata={"hnsw:space": "cosine"},
    )


# ---------------------------------------------------------------------------
# DAY GROUPING
# ---------------------------------------------------------------------------
# One journal entry = one calendar day. Claude-export timestamps are UTC;
# a late-evening entry belongs to the local day it was written, so
# timestamps convert to local time before the day is taken.

def local_day(ts: str, assume_utc: bool = True) -> str:
    """The local calendar day (YYYY-MM-DD) a timestamp belongs to.

    Accepts export timestamps ("2025-10-06T00:30:38.173062Z", UTC) and
    session stamps ("2026-07-08 21:14", already local — pass
    assume_utc=False). Falls back to the raw date prefix if unparseable.
    """
    raw = (ts or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return raw[:10]
    if parsed.tzinfo is None:
        if not assume_utc:
            return parsed.strftime("%Y-%m-%d")
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().strftime("%Y-%m-%d")


def group_messages_by_local_day(
    messages: list[dict], ts_key: str = "created_at", assume_utc: bool = True
) -> dict[str, list[dict]]:
    """Group an ordered message list by local calendar day, preserving
    order within each day. Messages with no usable timestamp group under
    "" — callers substitute their own fallback date."""
    groups: dict[str, list[dict]] = {}
    for msg in messages:
        day = local_day(msg.get(ts_key, ""), assume_utc=assume_utc)
        groups.setdefault(day, []).append(msg)
    return dict(sorted(groups.items()))


# ---------------------------------------------------------------------------
# METADATA EXTRACTION (heuristics v1)
# ---------------------------------------------------------------------------
# Keyword-based extraction so bulk import runs free and offline.
# A Claude-powered extractor can replace this later — same signature,
# better people/events detection (see roadmap: Key Design Decisions).

_MOOD_KEYWORDS = {
    "happy": [
        "happy", "excited", "great day", "amazing", "wonderful", "grateful",
        "proud", "joy", "laughed", "fun", "love this", "so good", "won",
    ],
    "sad": [
        "sad", "cried", "crying", "miss him", "miss her", "lonely", "grief",
        "heartbroken", "depressed", "down today", "empty",
    ],
    "anxious": [
        "anxious", "anxiety", "worried", "nervous", "stressed", "stress",
        "overwhelmed", "panic", "scared", "afraid", "dread",
    ],
    "angry": [
        "angry", "furious", "pissed", "annoyed", "frustrated", "frustrating",
        "irritated", "fed up", "sick of",
    ],
    "tired": [
        "tired", "exhausted", "drained", "burnt out", "burned out",
        "no energy", "couldn't sleep", "insomnia",
    ],
    "hopeful": [
        "hopeful", "optimistic", "looking forward", "excited about",
        "can't wait", "fresh start", "new chapter", "better lately",
    ],
    "calm": [
        "calm", "peaceful", "content", "relaxed", "at ease", "settled",
        "quiet day", "slow day",
    ],
}

_TOPIC_KEYWORDS = {
    "work": [
        "work", "job", "boss", "meeting", "coworker", "deadline", "project",
        "interview", "career", "office", "client", "laid off", "promotion",
    ],
    "relationships": [
        "date", "dating", "crush", "boyfriend", "girlfriend", "relationship",
        "partner", "spouse", "wife", "husband", "married", "marriage",
        "engaged", "anniversary", "divorce",
        "ex", "broke up", "breakup", "texted him", "texted her",
    ],
    "health": [
        "doctor", "sick", "injury", "injured", "pain", "knee", "sleep",
        "workout", "gym", "therapy", "medication", "healing", "recovery",
    ],
    "creative": [
        "drawing", "painting", "writing", "music", "song", "art", "design",
        "creative", "sketch", "poem", "project idea", "built", "made",
    ],
    "social": [
        "friend", "friends", "party", "hung out", "dinner with", "drinks",
        "karaoke", "concert", "show", "bar", "met up", "visited",
    ],
    "family": [
        "mom", "dad", "mother", "father", "sister", "brother", "parents",
        "family", "grandma", "grandpa", "aunt", "uncle", "cousin",
    ],
    "pets": [
        "cat", "dog", "kitten", "puppy", "vet", "pet", "litter", "leash",
        "squirrel", "bird feeder",
    ],
    "emotional": [
        "feeling", "feelings", "emotional", "therapy", "processing", "vent",
        "reflecting", "realized", "pattern", "spiral",
    ],
    "home": [
        "apartment", "house", "moving", "lease", "rent", "landlord",
        "cleaning", "furniture", "neighbor", "kitchen", "garden",
    ],
    "ai_reflection": [
        "claude", "chatgpt", "ai", "llm", "chatbot", "talking to you",
    ],
}

_EVENT_PATTERNS = [
    r"\b(decided to [^.!?\n]+)",
    r"\b(started [^.!?\n]+)",
    r"\b(finished [^.!?\n]+)",
    r"\b(quit [^.!?\n]+)",
    r"\b(won [^.!?\n]+)",
    r"\b(got (?:a|the|an|my) [^.!?\n]+)",
    r"\b(moved (?:to|into|out) [^.!?\n]+)",
    r"\b(met [A-Z][^.!?\n]+)",
    r"\b(broke up [^.!?\n]*)",
    r"\b(signed up for [^.!?\n]+)",
]

# Capitalized words that are almost never people's names in journal text
_NAME_STOPWORDS = {
    "I", "I'm", "I've", "I'll", "I'd", "The", "A", "An", "And", "But", "Or",
    "So", "Then", "That", "This", "These", "Those", "It", "It's", "He", "She",
    "They", "We", "You", "My", "Me", "Not", "No", "Yes", "OK", "Okay",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
    "Today", "Tomorrow", "Yesterday", "Tonight", "Morning", "Evening",
    "God", "TV", "AI", "Claude", "ChatGPT", "Google", "YouTube", "Instagram",
    "America", "American", "English", "Spanish",
    "Christmas", "Thanksgiving", "Halloween", "Easter", "New",
}


def _count_keyword(text_lower: str, keyword: str) -> int:
    """Count whole-word keyword occurrences ("art" must not match "started")."""
    return len(re.findall(r"\b" + re.escape(keyword.strip()) + r"\b", text_lower))


def _detect_mood(text_lower: str) -> str:
    """Score each mood bucket by keyword hits; return the strongest."""
    scores = {
        mood: sum(_count_keyword(text_lower, kw) for kw in keywords)
        for mood, keywords in _MOOD_KEYWORDS.items()
    }
    best_mood, best_score = max(scores.items(), key=lambda item: item[1])
    return best_mood if best_score > 0 else "neutral"


def _detect_topics(text_lower: str) -> list[str]:
    """Return every topic with at least one keyword hit, strongest first."""
    scores = {
        topic: sum(_count_keyword(text_lower, kw) for kw in keywords)
        for topic, keywords in _TOPIC_KEYWORDS.items()
    }
    hits = [(topic, score) for topic, score in scores.items() if score > 0]
    hits.sort(key=lambda item: item[1], reverse=True)
    return [topic for topic, _ in hits[:5]]


def _detect_people(text: str) -> list[str]:
    """
    Find likely names: capitalized words that appear mid-sentence.

    Deliberately conservative — a word only counts if it shows up
    capitalized somewhere other than a sentence start, so ordinary
    sentence-initial words don't flood the list.
    """
    candidates = re.findall(r"(?<![.!?\n]\s)(?<!^)\b([A-Z][a-z]{2,})\b", text)
    people = []
    for word in candidates:
        if word in _NAME_STOPWORDS or word in people:
            continue
        people.append(word)
    return people[:10]


def _detect_events(text: str) -> list[str]:
    """Pull short phrases that look like concrete events or decisions."""
    events = []
    for pattern in _EVENT_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            event = match.group(1).strip()
            if len(event) > 15 and event not in events:
                events.append(event[:120])
    return events[:5]


def extract_metadata(text: str) -> dict:
    """
    Extract structured metadata from journal entry text.

    Returns:
        {
            "people":     list[str] — likely names mentioned
            "topics":     list[str] — matched category tags
            "mood":       str      — dominant mood bucket or "neutral"
            "key_events": list[str] — concrete events/decisions found
        }
    """
    text_lower = text.lower()
    return {
        "people": _detect_people(text),
        "topics": _detect_topics(text_lower),
        "mood": _detect_mood(text_lower),
        "key_events": _detect_events(text),
    }


# ---------------------------------------------------------------------------
# QUERY
# ---------------------------------------------------------------------------

def query_journal(question: str, n_results: int = 5) -> list[dict]:
    """
    Semantic search over the journal. Returns the most relevant passages,
    each with its text, metadata, and similarity distance (lower = closer).

    Searches the passage index (passages.py), where every part of an entry
    is findable. Until that index has been built for the configured model --
    an install that has not run rebuild_index.py since it arrived, or since
    the model changed -- or while the model can't be downloaded, it falls
    back to the journal chunks, whose embeddings only cover each chunk's
    opening.
    """
    import passages
    try:
        hits = passages.search(question, n_results)
    except passages.ModelUnavailable as exc:
        print(f"  [search] {exc}")
        hits = []
    if hits:
        return hits

    collection = get_collection()
    if collection.count() == 0:
        return []
    # Chroma's own embedder: the one these chunks were embedded with.
    results = collection.query(query_texts=[question],
                               n_results=min(n_results, collection.count()))

    matches = []
    for i, doc in enumerate(results["documents"][0]):
        matches.append({
            "text": doc,
            "metadata": results["metadatas"][0][i],
            "distance": results["distances"][0][i],
        })
    return matches


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        print(f'\n  Searching journal for: "{question}"\n')
        for i, match in enumerate(query_journal(question)):
            meta = match["metadata"]
            print(f"  [{i + 1}] {meta.get('date', '?')} | {meta.get('title', 'Untitled')[:50]}")
            print(f"      mood: {meta.get('mood', '?')} | distance: {match['distance']:.3f}")
            print(f"      {match['text'][:150].replace(chr(10), ' ')}...\n")
    else:
        print("Usage: python rag_journal.py <question>")
        print('Example: python rag_journal.py "when did I last feel proud of my work"')
