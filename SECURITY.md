# Security

Do not commit secrets, real model endpoints, production logs, customer prompts, API keys, classified material, export-controlled data, or proprietary datasets.

The current gateway includes local demo principals and an in-memory rate limiter. Demo-principal authentication is disabled by default and must be explicitly enabled for local review. Treat this as a security architecture artifact, not production-ready infrastructure.

