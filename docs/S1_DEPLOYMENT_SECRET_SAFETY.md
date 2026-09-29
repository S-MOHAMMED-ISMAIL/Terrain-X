# S1 Deployment JWT Secret Safety

## VERIFIED: Current configuration

- `JWT_SECRET_KEY` is the signing and verification key for HS256 access tokens.
- Settings are constructed when the backend imports `app.main`, before the API serves a
  request. The worker also resolves the same settings during startup.
- Docker Compose supplies backend and worker settings from the repository's ignored `.env`
  file. It does not embed a JWT secret in the Compose file or image.
- `.env.example` contains a documented local-development example, not a deployment secret.
- The JWT secret is not written to application logs.

## VERIFIED: Security contract

- `JWT_SECRET_KEY` remains required in every environment. A missing value fails settings
  construction.
- An empty or whitespace-only value fails settings construction in every environment.
- Exact `ENVIRONMENT=development` may use the documented example so the existing local Docker
  workflow continues to start.
- Every non-development environment, including production and staging/shared deployments,
  rejects the documented example during settings construction.
- A non-empty custom secret continues to work. S1 does not impose a new length or format rule
  that could invalidate an existing deployment secret.
- Configuration stores the value as Pydantic `SecretStr`. Validation errors and settings
  representations redact it, and error messages never include either the configured value or
  the expected replacement.

The fail-fast error for an example value outside development is:

```text
JWT secret must be explicitly configured for non-development environments.
```

## VERIFIED: Docker and operator configuration

For local development, copy `.env.example` to the ignored `.env` file and retain
`ENVIRONMENT=development` if the documented example secret is acceptable for that isolated
machine.

For any shared, staging, or production deployment:

1. Set `ENVIRONMENT` to `production` or another non-development value.
2. Set `JWT_SECRET_KEY` through the deployment's environment or secret-management mechanism.
3. Use a unique, randomly generated value and keep it out of source control, images, logs, and
   support output.
4. Start the backend and worker. Either process refuses to initialize if the value is missing,
   empty, or still the documented example.

Docker Compose reads both values from `../.env` through `env_file`. The real `.env` remains
ignored by Git and is not copied into the Docker image.

## INFERENCE

- A random value with at least 256 bits of entropy is appropriate for HS256, but S1 deliberately
  avoids enforcing a length heuristic because existing valid deployment secrets must continue
  to work and length alone cannot prove entropy.
- Production orchestration may inject `JWT_SECRET_KEY` without a file; Pydantic settings accepts
  ordinary process environment variables with the same name.

## VERIFIED: Validation

- S1 configuration tests: 7 passed against the project's pinned Pydantic 2.10.4 and
  pydantic-settings 2.7.1 versions.
- JWT custom-secret signing, verification, claim preservation, and wrong-key rejection: 1
  passed against the project's pinned PyJWT 2.10.1 and Argon2 23.1.0 versions.
- S1 Python Ruff scope: clean under the available local Ruff 0.16.8 executable.
- TypeScript: clean.
- Vitest: 204 passed.
- ESLint: 0 errors and 5 existing Fast Refresh warnings.
- Vite production build: passed, with the existing large-chunk advisory.
- Docker Desktop's engine was unavailable during final validation. The database-backed auth
  integration test, broader backend regression, and browser authentication regression could
  not be rerun against the live stack.

## UNRESOLVED

- Environment classification is operator-controlled. A shared deployment incorrectly labelled
  `development` retains development behavior, because the application has no independent way to
  infer whether an instance is shared.
- S1 does not add secret rotation, key identifiers, asymmetric JWT signing, or an external secret
  manager. Rotating the HS256 secret invalidates tokens signed with the previous value.
- Process and container administrators can inspect environment variables by design; deployment
  access controls remain responsible for limiting that privilege.
- Live-stack validation remains pending until Docker Desktop's engine is available.
