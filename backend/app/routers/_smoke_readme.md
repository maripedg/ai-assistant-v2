Smoke examples (manual)

- Switch to JSON storage in `backend/config/app.yaml`:
  storage:
    users: { mode: json, json_path: data/users.json }
    feedback: { mode: json, json_path: data/feedback.json }

- Create a user (JSON mode). By default (require_signup_approval=false), the new user is created with status=active:
  curl -X POST http://localhost:8080/api/v1/users/ \
    -H "Content-Type: application/json" \
    -d '{"email":"u1@example.com","name":"User One","password":"secret"}'
  -> backend/data/users.json updated (relative to repo root)

- Switch to DB mode and run Alembic:
  # Ensure DB env: DB_USER, DB_PASSWORD, DB_DSN (host:port/SERVICE)
  alembic upgrade head
  curl -X POST http://localhost:8080/api/v1/users/ \
    -H "Content-Type: application/json" \
    -d '{"email":"u2@example.com","name":"User Two","password":"secret"}'

- Create feedback (sanitized comment):
  curl -X POST http://localhost:8080/api/v1/feedback/ \
    -H "Content-Type: application/json" \
    -d '{"user_id":1, "comment":"Visa 4111-1111-1111-1111 test", "category":"like"}'
