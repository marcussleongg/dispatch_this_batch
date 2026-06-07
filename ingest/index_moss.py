"""Phase 1 (offline): index ERG chunks into Moss so the orchestrator can query
"isolation distance for UN 1005" inline mid-call.

Mirrors the moss-hacker-starter's src/create_index.py:
    from moss import DocumentInfo, MossClient
    client = MossClient(MOSS_PROJECT_ID, MOSS_PROJECT_KEY)
    await client.create_index(MOSS_INDEX_NAME, docs, MOSS_MODEL_ID)
Docs = ERG chunks from parse_erg.py as [{id, text, metadata}] (metadata values
must be strings). TODO(Phase 1). Run: uv run --extra ingest python ingest/index_moss.py
"""
