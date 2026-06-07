# ERG Chunking & Cleaning Pipeline

Transforms raw Unsiloed output (`erg_chunks.json`) into Moss-ready chunks (`erg_chunks_clean.json`).

```bash
uv run --extra ingest python ingest/parse_erg.py --clean-only
```

---

## Steps (applied in order)

### 1. Whitespace normalization
Strip trailing spaces from every line and collapse runs of 3+ blank lines into 2. Applied before any filtering so length checks aren't skewed by whitespace.

No information loss — Markdown structure (headers, bullets, tables) is preserved intentionally (see rationale below).

### 2. Noise filtering
Drop a chunk if any condition is true:
- **Length < 50 chars** — catches page markers ("Page 4"), stray headings, and other PDF artefacts
- **OCR corruption heuristic** — if a single word appears in > 15% of all words in the chunk, the text is a repeated/garbled OCR output; drop it

### 3. Split on `##` headers
Unsiloed sometimes returns multiple `##` sections stitched into one chunk. Split on `\n## ` so each section becomes its own document. Sub-chunk IDs are suffixed `-{j}` (e.g. `erg-13-0`, `erg-13-1`).

### 4. Sliding-window split (overflow safety)
Any chunk still > 1,500 chars after step 3 is broken into 1,200-char windows with 200-char overlap (~17%). Sub-chunk IDs are suffixed `-w{j}`. This mainly catches the long VLM image descriptions Unsiloed generates for placard diagrams.

### 5. Merge short adjacent chunks
Greedily merge consecutive chunks up to 1,200 chars to bring most chunks into the 200–500 token range Moss recommends (~800–2,000 chars). The first chunk's `id` and `metadata` are kept for the merged result.

**Overlap at merge boundaries:** when a buffer is flushed and the next chunk does *not* start with a `#` header (i.e. it's a mid-flow continuation, not a new section), the last 150 chars of the previous buffer are prepended as context. If the next chunk opens with `#`, it's a distinct section and no overlap is added.

---

## Why Markdown is kept

Stripping `##`, `**`, `|` etc. recovers only ~5–10 tokens per chunk (marginal at our 200–350 token median) and introduces risk:
- `##` section titles are the strongest query-relevant signal in ERG chunks
- ERG tables (hazard class definitions, UN number lookups) need their pipe structure to stay readable
- MiniLM was trained on Markdown-heavy corpora and handles the syntax well

---

## Output stats (ERG 2024, 516 chunks)

| | chars | ~tokens |
|---|---|---|
| p25 | ~885 | ~221 |
| median | ~1,057 | ~264 |
| p75 | ~1,171 | ~292 |
| max | ~1,474 | ~368 |

Moss recommendation: 200–500 tokens with 10–20% overlap at boundaries.
