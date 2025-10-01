# Manifest Specification

Each ingest manifest is stored in **JSON Lines (JSONL)** format. Every line represents one document to ingest, with the following structure:

`
{
  "path": "<abs-or-rel-filepath>",
  "doc_id": "<optional>",
  "profile": "<optional overrides>",
  "tags": ["optional"],
  "lang": "optional",
  "priority": 0-10
}
`

**Fields**

- path *(required)*: Absolute or relative file path to the document to ingest.
- doc_id *(optional)*: Override the document identifier; defaults to a generated ID if omitted.
- profile *(optional)*: Name of the embeddings profile to use for this document. If omitted, the active profile from config/app.yaml is used.
- 	ags *(optional)*: Array of tags (strings) to attach to the document for filtering/metadata.
- lang *(optional)*: Language code or descriptor (e.g., en, es, r).
- priority *(optional)*: Integer from 0 (lowest) to 10 (highest) that the ingest job can leverage for scheduling.

**Processing Notes**

- The ingest job applies the active profile declared in config/app.yaml when profile is not provided.
- Per-document overrides allow mixing profiles or tweaking behaviour for specific documents.
- Additional fields may be ignored by the current pipeline but should follow valid JSON syntax.
