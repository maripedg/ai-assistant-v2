# API Auth

Current modes

- `auth.mode`: `local | sso | hybrid` (see backend/config/app.yaml)
- In the current build, endpoints do not enforce authentication by default. Local mode controls password hashing behavior for user creation only.

Future/SSO placement

- When auth is enabled, send a bearer token header:
  - `Authorization: Bearer {{auth_token}}`
- Roles (planned):
  - `admin`: manage users, list all feedback
  - `user`: read own profile, submit feedback

Password hashing

- `auth.password_algo`: `bcrypt` (default) or `pbkdf2_sha256` fallback.
- Passwords are never returned by the API.

Route guards

- Users and Feedback routers are enabled via feature flags:
  - `features.users_api`
  - `features.feedback_api`
- In dev, these may be open; in production, protect via gateway or app-level auth middleware.
