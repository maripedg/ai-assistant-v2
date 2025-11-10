# API Errors
Last updated: 2025-11-07

The backend relies on FastAPI’s default JSON error payloads (`{"detail": ...}` or Pydantic validation arrays). This page summarises the most common codes and their meaning.

## Status Catalogue
| Code | Surface | Example `detail` | Notes |
| --- | --- | --- | --- |
| 400 | Uploads / ingest jobs | `"Uploaded file is empty"` / `"upload_ids must not be empty"` | Payload failed domain validation. |
| 401 | Auth | `"unauthorized"`, `"missing_token"`, `"invalid_token"` | Login failure or bearer token missing/malformed. |
| 403 | Auth | `"forbidden"` | User suspended/deleted when logging in or refreshing. |
| 404 | Users / feedback / uploads / jobs | `"user_not_found"`, `"feedback_not_found"`, `"Upload not found: upl-42"`, `"Job not found"` | Resource missing or expired from staging cache. |
| 409 | Users / ingest jobs | `"email_already_exists"`, `"Conflicting job ..." ` | Duplicate email or conflicting upload set. |
| 413 | Uploads | `"Upload exceeds maximum size of 104857600 bytes"` | File larger than `MAX_UPLOAD_MB`. |
| 415 | Uploads | `"Unsupported MIME type: application/x-msdownload"` | MIME not in `ALLOW_MIME`. |
| 422 | Validation | Standard FastAPI validation array, `"Unknown profile: beta_profile"` | Schema or domain validation failure. |
| 500 | Uploads / jobs / chat | `"Upload failed"`, `"Unable to create job"`, `"Internal Server Error"` | Unexpected exception; check server logs. |
| 502 | SharePoint proxy | `"SharePoint sync failed"` | Upstream SharePoint service error. |

## Validation Payload Example
```json
{
  "detail": [
    {
      "loc": ["body", "email"],
      "msg": "value is not a valid email address",
      "type": "value_error.email"
    }
  ]
}
```

## Tips
- `/api/v1/feedback/` sanitizes `comment` server-side. Sanitization failures do not emit HTTP errors; they simply replace text with placeholders and optionally log counters.
- When dual-write is enabled for storage, secondary write failures are swallowed (primary response stays `200`). Inspect server logs if mismatched states are suspected.
- SharePoint proxy errors bubble through untouched; the frontend should surface the status message and instruct operators to check the sync service logs.
