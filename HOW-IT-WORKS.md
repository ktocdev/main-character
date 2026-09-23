# How it works

What the journal does when you write, reply, close a chat or ask a question:
which parts run on your machine for free, which parts call Claude with your key,
and how the pieces feed each other.

The code references name files and functions rather than line numbers, so they
stay correct as the code moves.

## The short version

There are two moments, and they do very different work.

- **Every entry or message** gets one Claude call: the companion's reply. Before
  that call, the server searches your journal locally and packs a small,
  fixed amount of what it finds into the prompt.
- **Closing a chat** (*summarize & close chat*) turns what you wrote into
  journal memory. It adds the text to the search index, then runs a background
  pass of Claude calls that tag it, extract people and places, and update the
  summaries. Only new or changed material is processed.

The companion never reads the whole journal. It reads what the search picked,
plus a few things it always gets.

## Where things live

| On disk | What it holds | Searchable by meaning |
|---|---|---|
| `sessions/current.json` | The open chat: entries, messages and replies | No, but the companion gets it as the conversation |
| `journal_entries/` | Every entry as markdown | Through the journal index |
| `chroma_data/` | Two local vector indexes: the journal (entry passages) and the summaries | Yes |
| `summaries/` | Entry summaries, weekly arcs, domain docs, the seed summary | Through the summary index |
| `entity_graph/` | People, projects and places, with a profile doc each | Through the summary index, and by name |
| `patterns/`, `categories/`, `dreams/` | Pattern library, tags, dream realm | Dreams have their own index |

Embeddings are computed locally with all-MiniLM-L6-v2, so building and
searching the indexes never calls an API.

## When you save an entry

`POST /api/entry` in `server.py`, `write_entry`.

1. **It is stored.** The text goes into a markdown backup and onto the open
   chat (`sessions.save_entry`). There is no Claude call. A save without a reply
   stops here and costs nothing.
2. **Spend caps are checked** (`caps.check`), before anything is spent.
3. **The context is assembled, locally.** The entry text itself is the search
   query. See [what the companion sees](#what-the-companion-sees).
4. **One call to the companion model** (Opus by default) streams the reply.

The new entry is not in the search indexes yet. The companion knows about it
because it is part of the open conversation. It gets indexed when the chat
closes.

A **chat message** (`POST /api/chat`) works the same way, except it counts as
conversation rather than as a saved entry. It still becomes journal memory at
close.

## What the companion sees

`companion.py`: `_stream_turn` builds the call and `build_context_block`
assembles the context.

The call has three parts.

**The system prompt**, cached so repeated turns are cheaper:

- The companion's persona (`SYSTEM_PROMPT`).
- The seed summary: the rolling life summary you co-edit.

**The conversation so far**: every entry, message and reply in the open chat.

**Your new entry, with a context block attached**:

| Layer | How it is chosen | How much |
|---|---|---|
| Current time | Always | One line |
| Dream weather | Always, if there are dreams | One line |
| This week's arc | Always, the most recent weekly arc | 1 |
| Recent entries | Always, the newest passages by date | 3 passages |
| Related history | Search by meaning over entry passages | 6 passages |
| Related summaries | Search by meaning over the summary index: entry summaries, weekly arcs, domain docs, entity profiles | 3 |
| People and places | Anyone the entry names, matched by name or alias | Up to 3 profiles |
| Dreams | Only when the entry is a dream or mentions dreaming | 4 |
| Patterns | Always, the whole pattern library up to a size budget | One line per pattern |

Passages are cut at 2,000 characters. The passage counts can be changed in
`.env` (`MC_N_RECENT`, `MC_N_SEMANTIC`, `MC_EXCERPT_CHARS`; see `config.py`).
The companion is told to bring up a pattern only when the conversation
genuinely echoes it.

### Why it can know *when*, but not *what exactly*

The seed summary and the open chat are always there. Everything older arrives
through six passages and three summaries. When a summary matches but the
passage it summarizes does not, the companion has the outline and the date,
not your words. It can tell you when you wrote something, and you can read it
in full in History.

## When you close a chat

`POST /api/sessions/close` in `server.py`, `close_session`, and
`sessions.close_session`.

**Right away:**

1. Your side of the chat is joined into a journal entry, one per day.
2. A new chat gets a title. Claude writes it (one call on the processing model)
   unless you gave one. A continued chat reuses the original title.
3. The text is chunked and embedded into the journal index. This is local and
   free. From here on, search can find it.
4. The chat is archived, and a fresh one opens.

**Then in the background**, all on the processing model (Sonnet by default).
The steps run one after another, and a failed step does not stop the rest.

| Step | What Claude is asked to do | Code |
|---|---|---|
| Seed | Draft an updated seed summary from the closed chat. It is saved as a candidate for you to review, and never replaces the seed on its own | `seed.generate_candidate` |
| Categories | Tag the new entry | `categories.build` |
| Entities | Extract people, places and projects from the new entry. Entries already processed are cached and skipped | `entities.build` |
| Summaries | Write the new entry's summary, and regenerate weekly arcs and domain docs whose entries changed. Then re-embed every summary and entity profile into the summary index, locally | `summarizer.build` |
| Dreams | Scan the new entry for dreams | `dreams.extract` |

The write screen shows these steps as they run (`/api/sessions/close/progress`).

Everything here is incremental. That is why a close costs more than any one
reply, and why the processing model, not the companion, uses most of the
tokens.

## The chat screen

`POST /api/lookup`. It uses the same search as a companion reply, but keeps its
own conversation. Lookups never join the open chat and never become journal
memory. It is the place to ask things like "when did I last mention…", then
go to History to read the entry in full.

## When the companion speaks first

`POST /api/reflect`, `companion.stream_reflection`. The same assembly as a
reply, but the search is seeded with your most recent entry, and the companion
is asked to connect threads across time instead of responding to something new.

## What never calls Claude

- Saving without a reply.
- All searching, and building or rebuilding the indexes (`rebuild_index.py`).
- Browsing History, entities, categories and patterns.
- Export and backup (`export.py`, `backup.py`).
- The demo journal, which replays recorded replies and never builds a client.

## Costs and limits

Every Claude call above, background ones included, goes through the same spend
caps, one per session and one per month. The metering wrapper around the
client (`metering.py`, `caps.py`) checks them before each call. The caps and every cost figure in the
app are estimates from a price table in the code. A
[spend limit on your Anthropic account](https://console.anthropic.com/settings/limits)
is the one that holds for certain.
