"""
Seed summary — the co-edited rolling life summary.

The one document the companion reads to know who the author is and where
life stands (Layer 1). Claude drafts updates by *integrating* the current
seed with a just-closed chat's full braid (both sides — the one place
companion replies are read, as voice, never as journal facts); the author
downloads the candidate, edits it in VS Code, and uploads it back. The
uploaded file is canonical — nothing auto-generated ever overwrites it.

  summaries/seed_summary.md            the live seed (author-owned)
  summaries/seed_summary.candidate.md  the post-close integrate output,
                                       awaiting review/edit/upload
  summaries/seed_backups/              prior seeds, kept on every upload

Usage:
    python seed.py bootstrap <previous.md> <archive.json>
        one-time chained catch-up: integrate a base summary with an
        archived session and write the result as the live seed
    python seed.py candidate <archive_key>
        integrate the live seed with an archived session -> candidate
        (this is what the server runs when a chat closes)
    python seed.py consolidate
        compress the live seed -> candidate (run when it feels too long)
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


from config import AUTHOR, MC_PROCESSING_MODEL as MODEL, SUMMARY_DIR, get_client, processing_thinking_kwargs

MAX_TOKENS = 32_000
SEED_FILE = SUMMARY_DIR / "seed_summary.md"
CANDIDATE_FILE = SUMMARY_DIR / "seed_summary.candidate.md"
BACKUP_DIR = SUMMARY_DIR / "seed_backups"
BRAID_MAX_CHARS = 300_000  # newest kept if a braid somehow exceeds this

INTEGRATE_PROMPT = """\
You maintain {author}'s rolling life summary — the one document a \
companion reads to know who they are and where things stand. Below is the \
current summary and the latest chat (both sides). Produce an updated \
summary that integrates them: fold in what's new, carry open threads \
forward, update or close threads that changed, and drop only what's truly \
resolved. Preserve the section structure and the voice of the current \
summary. Update the "Updated" date line to {today}. Output only the \
updated summary document, nothing else.

Voice: tell it as their story — {author} is the complex, flawed, \
rooted-for main character; honest about mistakes, always on their side. \
Name patterns and land resolved threads on grounded hope tied to what \
actually happened — never empty uplift. Anchor to dates; use their words \
and people's names as they do.

<current_summary>
{previous}
</current_summary>

<latest_chat>
{chat_braid}
</latest_chat>"""

CONSOLIDATE_PROMPT = """\
You maintain {author}'s rolling life summary. It has grown long. Produce \
a consolidated version: merge older day-by-day logs into period-level \
narrative, keep the most recent weeks in full detail, and keep every \
section, every open thread, and the Self-Knowledge material. Preserve the \
section structure and the voice — nothing about the lens changes, only \
the compression of the older record. Output only the consolidated \
document, nothing else. Today is {today}.

<current_summary>
{previous}
</current_summary>"""


def load_seed() -> str:
    """The live seed (empty string if none yet)."""
    return SEED_FILE.read_text(encoding="utf-8") if SEED_FILE.exists() else ""


def save_seed(text: str) -> dict:
    """The upload point: the edited file becomes the live seed. The prior
    seed is backed up first; the pending candidate (now superseded) is
    cleared."""
    text = text.strip()
    if len(text) < 200:
        raise ValueError("that file looks empty — not replacing the seed with it")
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    if SEED_FILE.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        (BACKUP_DIR / f"seed_summary.{stamp}.md").write_text(
            SEED_FILE.read_text(encoding="utf-8"), encoding="utf-8"
        )
    SEED_FILE.write_text(text + "\n", encoding="utf-8")
    if CANDIDATE_FILE.exists():
        CANDIDATE_FILE.unlink()
    return {"chars": len(text)}


def status() -> dict:
    def info(path: Path) -> dict:
        if not path.exists():
            return {"exists": False}
        return {
            "exists": True,
            "updated": datetime.fromtimestamp(path.stat().st_mtime)
            .strftime("%Y-%m-%d %H:%M"),
            "chars": path.stat().st_size,
        }
    cur, cand = info(SEED_FILE), info(CANDIDATE_FILE)
    return {
        "exists": cur["exists"],
        "updated": cur.get("updated"),
        "candidate_exists": cand["exists"],
        "candidate_updated": cand.get("updated"),
    }


def _render_messages(msgs: list[dict], header: str) -> str:
    lines = [header]
    for m in msgs:
        who = AUTHOR if m.get("role") == "you" else "Companion"
        ts = m.get("ts", "")
        lines.append(f"{who} ({ts}):\n{m.get('text', '')}")
    return "\n\n".join(lines)


def archive_braid_text(archive: dict) -> str:
    """One archived session as chat text, both sides, in order. Parts are
    heterogeneous: base parts carry a two-sided braid or user-side text;
    the closing day-parts are bare references whose content lives in the
    archive's top-level messages (the live braid), rendered last."""
    blocks = []
    for part in archive.get("parts", []):
        head = f"[{part.get('date', '?')}] {part.get('title', 'Untitled')}"
        if part.get("messages"):
            blocks.append(_render_messages(part["messages"], head))
        elif part.get("text"):
            blocks.append(f"{head} ({AUTHOR}'s entry)\n{part['text']}")
    if archive.get("messages"):
        blocks.append(_render_messages(archive["messages"], "[live session]"))
    text = "\n\n---\n\n".join(blocks)
    if len(text) > BRAID_MAX_CHARS:
        text = "(older part of this chat trimmed for length)\n\n…" \
            + text[-BRAID_MAX_CHARS:]
    return text


def _call(prompt: str) -> str:
    client = get_client()
    parts = []
    with client.messages.stream(
        model=MODEL, max_tokens=MAX_TOKENS,
        **processing_thinking_kwargs(),
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for text in stream.text_stream:
            parts.append(text)
        final = stream.get_final_message()
    if final.stop_reason == "refusal":
        raise RuntimeError("model declined")
    return "".join(parts).strip()


def integrate(previous_summary_text: str, chat_braid_text: str) -> str:
    """Fold one chat (both sides) into the rolling summary."""
    return _call(INTEGRATE_PROMPT.format(
        author=AUTHOR, today=datetime.now().strftime("%B %d, %Y"),
        previous=previous_summary_text, chat_braid=chat_braid_text,
    ))


def consolidate(summary_text: str) -> str:
    """Compress the rolling summary when it has grown too long."""
    return _call(CONSOLIDATE_PROMPT.format(
        author=AUTHOR, today=datetime.now().strftime("%B %d, %Y"),
        previous=summary_text,
    ))


def generate_candidate(archive_key: str) -> Path:
    """Integrate-at-close: fold the just-archived session into the live
    seed and write the candidate for review. Never touches the seed."""
    import sessions
    archive = sessions.load_archive(archive_key)
    if archive is None:
        raise ValueError(f"archive '{archive_key}' not found")
    previous = load_seed()
    if not previous:
        raise ValueError("no live seed yet — bootstrap one first (python seed.py)")
    updated = integrate(previous, archive_braid_text(archive))
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    CANDIDATE_FILE.write_text(updated + "\n", encoding="utf-8")
    return CANDIDATE_FILE


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    if args[0] == "bootstrap" and len(args) == 3:
        previous = Path(args[1]).read_text(encoding="utf-8")
        archive = json.loads(Path(args[2]).read_text(encoding="utf-8"))
        braid = archive_braid_text(archive)
        print(f"  integrating {Path(args[1]).name} ({len(previous):,} chars) "
              f"+ {Path(args[2]).name} ({len(braid):,} chars braid)…")
        updated = integrate(previous, braid)
        SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
        SEED_FILE.write_text(updated + "\n", encoding="utf-8")
        print(f"  seed -> {SEED_FILE} ({len(updated):,} chars). "
              "Review and edit it — it's yours now.")

    elif args[0] == "candidate" and len(args) == 2:
        path = generate_candidate(args[1])
        print(f"  candidate -> {path}")

    elif args[0] == "consolidate":
        previous = load_seed()
        if not previous:
            print("  no live seed yet")
            return
        updated = consolidate(previous)
        CANDIDATE_FILE.write_text(updated + "\n", encoding="utf-8")
        print(f"  consolidated candidate -> {CANDIDATE_FILE} "
              f"({len(previous):,} -> {len(updated):,} chars). "
              "Review and upload it to make it the live seed.")

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
