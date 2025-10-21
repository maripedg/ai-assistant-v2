# API Errors

Error model

- Errors use standard FastAPI error responses, typically:
```json
{ "detail": "error_code_or_message" }
```

Common status codes

- 400 Bad Request — malformed input/body.
- 401 Unauthorized — when auth is enabled and missing/invalid.
- 403 Forbidden — when user lacks permissions.
- 404 Not Found — entity or route not found.
- 409 Conflict — e.g., `email_already_exists` on user creation.
- 422 Unprocessable Entity — validation errors.
- 500 Internal Server Error — unexpected failure.

Examples

- 404 user not found
```json
{ "detail": "user_not_found" }
```
- 409 email exists
```json
{ "detail": "email_already_exists" }
```
- 422 validation
```json
{
  "detail": [
    {
      "loc": ["body", "email"],
      "msg": "value is not a valid email address",
      "type": "value_error"
    }
  ]
}
```
