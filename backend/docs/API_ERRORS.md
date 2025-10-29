# API Errors

Error model

- Errors follow FastAPI conventions, typically returning JSON shaped as `{"detail": "<code or message>"}`.
- Validation failures return `{"detail": [...errors...]}` arrays describing field issues.

Common status codes

- 400 Bad Request – payload failed domain checks (e.g., duplicate upload IDs, missing fields).
- 401 Unauthorized – login failures, missing or invalid bearer tokens.
- 403 Forbidden – user record is suspended/deleted when attempting auth refresh.
- 404 Not Found – missing resources (users, feedback, uploads, ingest jobs).
- 409 Conflict – duplicate user emails or conflicting ingestion jobs.
- 413 Payload Too Large – uploaded file exceeded configured size limit.
- 415 Unsupported Media Type – uploaded file MIME type not allowed.
- 422 Unprocessable Entity – Pydantic validation failures (email format, profile name).
- 500 Internal Server Error – unexpected backend failure.
- 502 Bad Gateway – SharePoint sync orchestration or upstream service errors.

Project-specific detail values

- Auth: `unauthorized`, `forbidden`, `missing_token`, `invalid_token`, `user_not_found`.
- Users: `email_already_exists`, `user_not_found`, `local_auth_disabled`, `invalid_current_password`.
- Feedback: `feedback_not_found`.
- Documents & Embeddings:
  - Upload storage: `"No file provided"`, `"Uploaded file is empty"`, `"Upload exceeds maximum size of <bytes> bytes"`, `"Unsupported MIME type: <type>"`, `"Upload failed"`.
  - Job creation: `"upload_ids must be unique"`, `"upload_ids must not be empty"`, `"Upload not found: <ids>"`, `"Conflicting job active job already references one of the uploads"`, `"Unknown profile: <name>"`, `"Unable to create job"`.
  - Job lookup: `"Job not found"`.
  - SharePoint sync: `"SharePoint sync failed"`, `"SharePoint sync service error <status>"`, `"Invalid response from SharePoint sync service"`.

Examples

- 401 missing bearer token
```json
{ "detail": "missing_token" }
```
- 404 user lookup
```json
{ "detail": "user_not_found" }
```
- 409 duplicate email
```json
{ "detail": "email_already_exists" }
```
- 413 oversized upload
```json
{ "detail": "Upload exceeds maximum size of 10485760 bytes" }
```
- 422 unknown profile
```json
{ "detail": "Unknown profile: legacy_profile" }
```
- 502 SharePoint sync failure
```json
{ "detail": "SharePoint sync failed" }
```
- 422 validation payload
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
