"""
Bulk Import — Load Claude conversation exports into the RAG Journal.

This script reads your exported Claude data, extracts your journal entries,
and loads them into the vector store so the journal companion can reference them.

Setup:
    1. Go to claude.ai → Settings → Account → Export Data
    2. Download and unzip the export
    3. Find the conversations.json file
    4. Run this script pointing at it

Usage:
    python bulk_import.py /path/to/conversations.json

    Options:
        --dry-run       Preview what would be imported without storing anything
        --filter TEXT   Only import conversations whose title contains TEXT
        --ids FILE      Only import conversations whose UUID matches a line in FILE
                        (full UUIDs or 8-char prefixes; extra text per line is ignored)
        --min-length N  Skip user messages shorter than N characters (default: 100)
        --summaries     Prioritize conversations that look like summaries
"""

import os
import sys
import json
import hashlib
import argparse
from datetime import datetime
from pathlib import Path

# Reuse the core functions from rag_journal.py
from rag_journal import get_collection, extract_metadata, JOURNAL_DIR


# ---------------------------------------------------------------------------
# PARSING CLAUDE EXPORTS
# ---------------------------------------------------------------------------

def load_conversations(filepath: str) -> list[dict]:
    """Load conversations from a Claude export JSON file."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    # The export might be a list of conversations directly,
    # or wrapped in an object. Handle both.
    if isinstance(data, list):
        return data
    elif isinstance(data, dict):
        # Common keys: 'conversations', 'chats', or the data might be
        # at the top level with uuid/chat_messages
        for key in ["conversations", "chats", "data"]:
            if key in data and isinstance(data[key], list):
                return data[key]
        # Maybe it's a single conversation
        if "chat_messages" in data:
            return [data]

    print(f"  Warning: Could not parse export format. Keys found: {list(data.keys()) if isinstance(data, dict) else type(data)}")
    return []


def extract_user_entries(conversation: dict) -> list[dict]:
    """
    Pull out user messages from a conversation and group them into
    coherent journal entries.

    Strategy:
    - Concatenate all user messages in a conversation into one entry
    - This preserves the full context of a journaling session
    - Very long conversations get split at natural breaks (time gaps)
    """
    messages = conversation.get("chat_messages", [])
    if not messages:
        return []

    # Get conversation metadata
    conv_title = conversation.get("name", "Untitled")
    conv_date = conversation.get("created_at", "")
    conv_uuid = conversation.get("uuid", "unknown")

    # Collect just the human messages
    user_messages = []
    for msg in messages:
        # Handle different export formats
        sender = msg.get("sender", msg.get("role", ""))
        text = msg.get("text", msg.get("content", ""))

        # Content might be a list of blocks (newer export format)
        if isinstance(text, list):
            text = " ".join(
                block.get("text", "")
                for block in text
                if isinstance(block, dict) and block.get("type") == "text"
            )

        if sender in ("human", "user") and text.strip():
            user_messages.append({
                "text": text.strip(),
                "created_at": msg.get("created_at", conv_date),
            })

    if not user_messages:
        return []

    # Combine all user messages into one entry per conversation
    combined_text = "\n\n".join(m["text"] for m in user_messages)

    # Parse date for storage
    date_str = conv_date[:10] if conv_date else datetime.now().strftime("%Y-%m-%d")

    return [{
        "text": combined_text,
        "date": date_str,
        "title": conv_title,
        "uuid": conv_uuid,
        "message_count": len(user_messages),
    }]


def load_id_list(filepath: str) -> list[str]:
    """
    Load conversation UUID prefixes from a file, one per line.

    Lenient parsing: grabs the first hex-looking token (8+ chars) on each
    line, so a pasted list like "831adaf5 — journal chat (Dec 22)" works
    as-is. Lines with no hex token are ignored.
    """
    import re
    prefixes = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            match = re.search(r"\b[0-9a-f]{8,}\b", line.lower())
            if match:
                prefixes.append(match.group(0))
    return prefixes


def is_likely_summary(conversation: dict) -> bool:
    """Detect conversations that are probably weekly summaries."""
    title = (conversation.get("name", "") or "").lower()
    summary_keywords = [
        "summary", "recap", "week", "review", "roundup",
        "journal summary", "weekly", "update", "check-in"
    ]
    return any(kw in title for kw in summary_keywords)


def is_likely_journal(conversation: dict) -> bool:
    """Detect conversations that are probably journal entries."""
    title = (conversation.get("name", "") or "").lower()
    journal_keywords = [
        "journal", "entry", "diary", "morning", "evening",
        "vent", "thinking about", "feeling", "today",
        "rough day", "good day", "update"
    ]
    return any(kw in title for kw in journal_keywords)


# ---------------------------------------------------------------------------
# CHUNKING STRATEGY
# ---------------------------------------------------------------------------

def chunk_entry(entry: dict, max_tokens: int = 1500) -> list[dict]:
    """
    Split a long entry into smaller chunks if needed.

    For journal entries, we try to keep each chunk semantically coherent
    by splitting on double newlines (paragraph breaks).

    max_tokens is approximate — we estimate 1 token ≈ 4 characters.
    """
    text = entry["text"]
    max_chars = max_tokens * 4

    if len(text) <= max_chars:
        return [entry]

    # Split on paragraph breaks
    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = []
    current_length = 0

    for para in paragraphs:
        if current_length + len(para) > max_chars and current_chunk:
            chunks.append({
                **entry,
                "text": "\n\n".join(current_chunk),
                "chunk_index": len(chunks),
            })
            current_chunk = [para]
            current_length = len(para)
        else:
            current_chunk.append(para)
            current_length += len(para)

    if current_chunk:
        chunks.append({
            **entry,
            "text": "\n\n".join(current_chunk),
            "chunk_index": len(chunks),
        })

    return chunks


# ---------------------------------------------------------------------------
# IMPORT PIPELINE
# ---------------------------------------------------------------------------

def import_entry(entry: dict, collection, dry_run: bool = False) -> dict:
    """
    Import a single entry into the vector store.
    Returns the metadata extracted.
    """
    text = entry["text"]
    date = entry["date"]
    title = entry.get("title", "Untitled")
    chunk_idx = entry.get("chunk_index", 0)

    # Generate stable ID
    content_hash = hashlib.md5(text[:200].encode()).hexdigest()[:8]
    entry_id = f"{date}_{content_hash}_c{chunk_idx}"

    if dry_run:
        return {
            "id": entry_id,
            "date": date,
            "title": title,
            "length": len(text),
            "preview": text[:120].replace("\n", " ") + "...",
        }

    # Extract metadata
    metadata = extract_metadata(text)

    flat_metadata = {
        "date": date,
        "title": title,
        "people": ", ".join(metadata.get("people", [])),
        "topics": ", ".join(metadata.get("topics", [])),
        "mood": metadata.get("mood", "unknown"),
        "key_events": " | ".join(metadata.get("key_events", [])),
        "is_summary": str(entry.get("is_summary", False)),
        "source": "bulk_import",
    }

    # Store in vector DB
    collection.upsert(
        ids=[entry_id],
        documents=[text],
        metadatas=[flat_metadata],
    )

    # Also save raw text to disk
    JOURNAL_DIR.mkdir(exist_ok=True)
    safe_title = "".join(c if c.isalnum() or c in " -_" else "" for c in title)[:50]
    chunk_suffix = f"_part{chunk_idx + 1}" if chunk_idx else ""
    filepath = JOURNAL_DIR / f"{date}_{safe_title}{chunk_suffix}.md"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n_Date: {date}_\n\n{text}")

    return {
        "id": entry_id,
        "date": date,
        "title": title,
        "metadata": metadata,
    }


def run_import(
    filepath: str,
    dry_run: bool = False,
    filter_text: str = None,
    ids_file: str = None,
    min_length: int = 100,
    summaries_only: bool = False,
):
    """Main import pipeline."""

    print(f"\n{'=' * 60}")
    print(f"  RAG JOURNAL — BULK IMPORT")
    print(f"  {'DRY RUN' if dry_run else 'LIVE IMPORT'}")
    print(f"{'=' * 60}\n")

    # Load conversations
    print(f"  Loading: {filepath}")
    conversations = load_conversations(filepath)
    print(f"  Found {len(conversations)} conversations\n")

    if not conversations:
        print("  No conversations found. Check the file format.")
        return

    # Filter if requested
    if filter_text:
        conversations = [
            c for c in conversations
            if filter_text.lower() in (c.get("name", "") or "").lower()
        ]
        print(f"  After filter '{filter_text}': {len(conversations)} conversations\n")

    if ids_file:
        prefixes = load_id_list(ids_file)
        conversations = [
            c for c in conversations
            if any((c.get("uuid", "") or "").lower().startswith(p) for p in prefixes)
        ]
        print(f"  ID list: {len(prefixes)} IDs -> matched {len(conversations)} conversations")
        if len(conversations) < len(prefixes):
            matched_prefixes = {
                p for p in prefixes
                if any((c.get("uuid", "") or "").lower().startswith(p) for c in conversations)
            }
            for p in prefixes:
                if p not in matched_prefixes:
                    print(f"    WARNING: no conversation found for ID {p}")
        print()

    # Tag summaries
    summary_count = 0
    for conv in conversations:
        if is_likely_summary(conv):
            conv["_is_summary"] = True
            summary_count += 1

    print(f"  Detected {summary_count} likely summaries")

    if summaries_only:
        conversations = [c for c in conversations if c.get("_is_summary")]
        print(f"  Importing summaries only: {len(conversations)} conversations\n")

    # Extract entries
    all_entries = []
    skipped = 0

    for conv in conversations:
        entries = extract_user_entries(conv)
        for entry in entries:
            if len(entry["text"]) < min_length:
                skipped += 1
                continue
            entry["is_summary"] = conv.get("_is_summary", False)

            # Chunk long entries
            chunks = chunk_entry(entry)
            all_entries.extend(chunks)

    print(f"  Extracted {len(all_entries)} chunks ({skipped} short messages skipped)\n")

    if not all_entries:
        print("  Nothing to import.")
        return

    # Sort by date
    all_entries.sort(key=lambda e: e.get("date", ""))

    # Get collection (skip if dry run)
    collection = None if dry_run else get_collection()

    # Import
    results = []
    for i, entry in enumerate(all_entries):
        prefix = f"  [{i + 1}/{len(all_entries)}]"

        if dry_run:
            result = import_entry(entry, collection, dry_run=True)
            print(f"{prefix} {result['date']} | {result['title'][:40]}")
            print(f"         {result['length']} chars | {result['preview']}")
        else:
            print(f"{prefix} Processing: {entry.get('title', 'Untitled')[:50]}...")
            result = import_entry(entry, collection)
            meta = result.get("metadata", {})
            print(f"         {result['date']} | mood: {meta.get('mood', '?')} | topics: {', '.join(meta.get('topics', []))}")

        results.append(result)

    # Summary
    print(f"\n{'=' * 60}")
    if dry_run:
        print(f"  DRY RUN COMPLETE — {len(results)} entries would be imported")
        print(f"  Run without --dry-run to actually import")
    else:
        print(f"  IMPORT COMPLETE — {len(results)} entries stored")
        collection = get_collection()
        print(f"  Total entries in vector store: {collection.count()}")
    print(f"{'=' * 60}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bulk import Claude conversations into RAG Journal")
    parser.add_argument("filepath", help="Path to conversations.json from Claude export")
    parser.add_argument("--dry-run", action="store_true", help="Preview without importing")
    parser.add_argument("--filter", type=str, default=None, help="Only import conversations with this text in the title")
    parser.add_argument("--ids", type=str, default=None, help="Path to a file of conversation UUIDs (one per line, prefixes OK)")
    parser.add_argument("--min-length", type=int, default=100, help="Skip messages shorter than N chars (default: 100)")
    parser.add_argument("--summaries", action="store_true", help="Only import detected summaries")

    args = parser.parse_args()

    if not os.path.exists(args.filepath):
        print(f"  File not found: {args.filepath}")
        sys.exit(1)

    run_import(
        filepath=args.filepath,
        dry_run=args.dry_run,
        filter_text=args.filter,
        ids_file=args.ids,
        min_length=args.min_length,
        summaries_only=args.summaries,
    )