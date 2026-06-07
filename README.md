# Multi-agent AI dispatch system for 911 call centers

One inbound 911-style call is answered by an AI call-taker (the **single voice** to the caller); a **supervisor** then spins up a **swarm of parallel action agents** mid-call — live web research + simulated agency calls — while a **command-center dashboard** streams every agent and transcript.

This project simulates specifically a call regarding a gas leak, with the document ingested being a guideline/protocol on how to handle it. This can be generalized to other emergency calls.

What I think is unique:
Live indexing of information obtained during call and web search (e.g. weather, wind direction), both locally and on the cloud, to allow for subagents (separate OS env) to query and retrieve the freshest information from other **ongoing calls**. This simulates how a real call enter operates.

Stack used:

- **Unsiloed** to parse [Department of Transportation 2024 Emergency Response Guidebook](https://www.phmsa.dot.gov/sites/phmsa.dot.gov/files/2024-04/ERG2024-Eng-Web-a.pdf)
- 2 voice agent environments set up with **Livekit**, and phone number for system to run through inbound phone call
- **Moss** to index document chunks and for live querying during calls
- LLM routed through **TrueFoundry**, using **Qwen-Plus** and **Minimax-M3**
- Web search tool using Tavily

---

## Chunking Steps

### 1. Send 392-page PDF to Unsiloed

Used parse capability of Unsiloed to get structured Markdown output.

### 2. Whitespace normalization

Strip trailing spaces from every line and collapse runs of 3+ blank lines into 2. Applied before any filtering so length checks aren't skewed by whitespace.

No information loss — Markdown structure (headers, bullets, tables) is preserved intentionally (see rationale below).

### 3. Noise filtering

Drop a chunk if any condition is true:

- **Length < 50 chars** — catches page markers ("Page 4"), stray headings, and other PDF artefacts
- **OCR corruption heuristic** — if a single word appears in > 15% of all words in the chunk, the text is a repeated/garbled OCR output; drop it

### 4. Split on `##` headers

Unsiloed sometimes returns multiple `##` sections stitched into one chunk. Split on `\n## ` so each section becomes its own document. Sub-chunk IDs are suffixed `-{j}` (e.g. `erg-13-0`, `erg-13-1`).

### 5. Sliding-window split (overflow safety)

Any chunk still > 1,500 chars after step 3 is broken into 1,200-char windows with 200-char overlap (~17%). Sub-chunk IDs are suffixed `-w{j}`. This mainly catches the long VLM image descriptions Unsiloed generates for placard diagrams.

### 6. Merge short adjacent chunks

Greedily merge consecutive chunks up to 1,200 chars to bring most chunks into the 200–500 token range Moss recommends (~800–2,000 chars). The first chunk's `id` and `metadata` are kept for the merged result.

**Overlap at merge boundaries:** when a buffer is flushed and the next chunk does _not_ start with a `#` header (i.e. it's a mid-flow continuation, not a new section), the last 150 chars of the previous buffer are prepended as context. If the next chunk opens with `#`, it's a distinct section and no overlap is added.

---

## Multi-agent system:

- We load in Moss index in the beginning for later retrieval, namely in `isolation distance for UN 1005`
- Tools available to orchestrator are web research and supervisor dispatching subagents. Tool available to subagents conducting parallel outbound calls is Moss cloud query.
- Web research runs to search for relevant information based on incident facts
- Supervisor dispatches subagents with to make outbound calls for cross-agency communication in parallel
- We use **live indexing**, **both locally and on the cloud**, to update information obtained from the main call and web research. Locally for agent within the same Livekit agent to retrieve information at very low latency. On the cloud for other Livekit agents (subagents spun up as simulating outbound calls) to retrieve information. Incident-specific index is deleted after the incident

## Example system log:

2026-06-07 10:29:33,333 [live_conditions] INFO live: MossClient ready for index=live-cd027582
2026-06-07 10:29:33,395 [llm ] DEBUG build_llm role=call_taker provider=truefoundry model=dispatch_llm/dispatch
2026-06-07 10:29:35,022 [llm ] DEBUG build_llm role=supervisor provider=truefoundry model=dispatch_llm/dispatch
2026-06-07 10:29:35,028 [guidebook ] INFO Moss index 'guidebook' loaded
2026-06-07 10:29:35,029 [supervisor ] INFO Supervisor started for incident cd027582
2026-06-07 10:29:58,165 [call_taker ] INFO record_fact: un_number='one zero zero five'
2026-06-07 10:29:58,166 [supervisor ] DEBUG supervisor FACT: un_number='one zero zero five' facts={'un_number': 'one zero zero five'}
2026-06-07 10:29:58,172 [supervisor ] INFO dashboard published ok: type=fact_update bytes=127
2026-06-07 10:29:58,467 [supervisor ] INFO \_reason: calling LLM facts=['un_number'] dispatched=[] hint=''
2026-06-07 10:29:59,062 [supervisor ] INFO dashboard published ok: type=snapshot bytes=303
2026-06-07 10:29:59,446 [supervisor ] INFO \_reason: LLM returned tool_calls=1 text=''
2026-06-07 10:29:59,446 [supervisor ] INFO \_reason: tool_call name='\_supervisor_dispatch' args='{"actions": ["web_research", "agency_fire_hazmat"]}'
2026-06-07 10:29:59,446 [supervisor ] INFO supervisor LLM decided: ['web_research', 'agency_fire_hazmat'] hint=''
2026-06-07 10:29:59,446 [supervisor ] INFO dispatching web_research
2026-06-07 10:29:59,448 [supervisor ] INFO dispatching liaison for fire_hazmat
2026-06-07 10:29:59,449 [web_research ] INFO web_research query: 'one zero zero five hazardous material emergency spill current news evacuation conditions'
2026-06-07 10:30:00,388 [connect ] INFO dispatched liaison for fire_hazmat → room=cd027582-fire_hazmat dispatch_id=AD_hP4t3VB648QE
2026-06-07 10:30:01,067 [web_research ] INFO web_research finding (As of today, all evacuation orders related to the Garden Grove hazardous materia)
2026-06-07 10:30:01,067 [supervisor ] INFO relaying finding from web_research: As of today, all evacuation orders related to the Garden Grove hazardous materia
2026-06-07 10:30:13,359 [call_taker ] INFO record_fact: location='Third Street at the main entrance of Oracle Park'
2026-06-07 10:30:13,360 [supervisor ] DEBUG supervisor FACT: location='Third Street at the main entrance of Oracle Park' facts={'un_number': 'one zero zero five', 'location': 'Third Street at the main entrance of Oracle Park'}
2026-06-07 10:30:13,663 [supervisor ] INFO \_reason: calling LLM facts=['un_number', 'location'] dispatched=['agency_fire_hazmat', 'web_research'] hint=''
2026-06-07 10:30:14,827 [supervisor ] INFO \_reason: LLM returned tool_calls=1 text=''
2026-06-07 10:30:14,827 [supervisor ] INFO \_reason: tool_call name='\_supervisor_dispatch' args='{"actions": ["agency_public_works"]}'
2026-06-07 10:30:14,827 [supervisor ] INFO supervisor LLM decided: ['agency_public_works'] hint=''
2026-06-07 10:30:14,827 [supervisor ] INFO dispatching liaison for public_works
2026-06-07 10:30:15,829 [connect ] INFO dispatched liaison for public_works → room=cd027582-public_works dispatch_id=AD_SsJV5KKQFEdi
2026-06-07 10:30:35,632 [guidebook ] INFO Moss query 'isolation distance for UN 1005' -> 3 hits, top_score=0.931, 1ms
2026-06-07 10:30:35,632 [guidebook ] DEBUG hit erg-un1005-ammonia score=0.930555522441864: UN 1005, Ammonia, anhydrous. ERG Guide 125 (gases - corrosive). Initial isolation: isolate the spill or leak area for at
2026-06-07 10:30:35,632 [guidebook ] DEBUG hit erg-un1017-chlorine score=0.9149305820465088: UN 1017, Chlorine. ERG Guide 124 (gases - toxic and/or corrosive - oxidizing). Initial isolation: isolate the spill or l
2026-06-07 10:30:35,632 [guidebook ] DEBUG hit erg-1385 score=0.5: ## HOW TO CHOOSE THE APPROPRIATE ISOLATION AND PROTECTIVE ACTION
DISTANCES
ERG2024 lists isolation or evacuation distanc
2026-06-07 10:30:35,766 [call_taker ] INFO trigger_research: reason='caller uncertain about wind direction'
2026-06-07 10:30:35,767 [supervisor ] INFO supervisor RESEARCH_TRIGGER: reason='caller uncertain about wind direction'
2026-06-07 10:30:36,074 [supervisor ] INFO \_reason: calling LLM facts=['un_number', 'location'] dispatched=['agency_fire_hazmat', 'agency_public_works', 'web_research'] hint='caller uncertain about wind direction'
2026-06-07 10:30:37,523 [supervisor ] INFO \_reason: LLM returned tool_calls=1 text=''
2026-06-07 10:30:37,524 [supervisor ] INFO \_reason: tool_call name='\_supervisor_dispatch' args='{"actions": []}'
2026-06-07 10:30:37,524 [supervisor ] INFO supervisor LLM decided: [] hint='caller uncertain about wind direction'

---

## Voice system

- **Livekit** agents with **phone number** allowing for **inbound calls**. One environment for main call, one environment for outbound subagents

## LLM routing

- Used **TrueFoundry priority routing virtual model** with **Qwen-Plus** and **Minimax-M3**

## What I would have added with more time

- Human takeover. In high-stakes calls and situations in public safety, there should always be the ability for a human to takeover. I envision a button where human operators can simply takeover, and "context" for human operators are obtained from the transcripts
- Subagents' live querying of the cloud has not been fully implemented, but it should be implemented with a hook/trigger where querying is done when the cloud index is updated
- More specialized subagents: medical, fire incident, etc. where more protocol documents are ingested and parsed over to provide even more use cases
- And so generalization of orchestrator agent that calls these specialized agents

---
