# Plan: disable demo-principal authentication by default

Date: 2026-10-07
Repository: `WaffleBits/secure-gpu-inference-gateway`

## Market signal

Official active listings read on 2026-10-07:

- LiveKit, Security Engineer: https://jobs.ashbyhq.com/livekit/efc06ff8-37c4-473e-8de8-bbba56cf0479; published 2026-10-06; `$135K – $300K • Offers Equity`; United States (Remote). The listing calls for hardening identity, secrets, and network boundaries and engineering-first automation.
- OpenAI, Software Engineer, Search Infrastructure: https://jobs.ashbyhq.com/openai/7caed1e8-c6f6-4569-9d45-2d7a7a56a025; published 2026-09-25; `$266K – $445K • Offers Equity`; San Francisco, hybrid (three office days weekly). The listing includes access controls, production reliability, and measurable systems operation.
- Anthropic, Performance Engineer: https://job-boards.greenhouse.io/anthropic/jobs/4020350008; updated 2026-08-21 12:49:34 -04:00; annual salary `$280,000 — $850,000 USD`; San Francisco, New York City, or Seattle; office at least 25% of the time. It emphasizes throughput, robustness, and fault-tolerant distributed systems.
- Cohere, Senior Software Engineer, GPU Infrastructure (HPC): https://jobs.ashbyhq.com/cohere/ef9b939d-da66-464c-a878-ef45616c0473; published 2026-09-03; `CA$285K – CA$340K • Offers Equity` for the Canada range; Canada, hybrid. It calls for reliability, observability, automation, Kubernetes/GPU infrastructure, and a 24x7 on-call rotation.

## Evidence and selected gap

The gateway already has JWT validation, role policy, threat-model notes, container/dependency scans, deployment posture tests, and explicit Kubernetes configuration with demo identities disabled. However, `AuthSettings` and `from_env()` currently default the demo-principal header to enabled when the deployment omits `ALLOW_DEMO_PRINCIPALS`. This is inconsistent with the explicit opt-in language in the threat model and the roadmap item to make local demo identities disabled by default.

Implement exactly this one trust-boundary change: default local demo identity authentication to disabled. Preserve intentional local review by requiring `ALLOW_DEMO_PRINCIPALS=true` explicitly. Update the quick-start documentation and tests so examples remain reproducible. Do not change JWT verification, authorization policy, or any production-readiness claims.

## Verification plan

1. Add tests first for unset-environment default deny, explicit opt-in allow, and explicit false deny; demonstrate failure against current code.
2. Change the dataclass and environment fallback defaults to false.
3. Run the focused tests and the repository's full canonical unittest suite, compile check, and app-import check in the CI Python 3.13 environment.
4. Exercise the actual HTTP service with demo auth unset (403) and explicitly enabled (200), using only the mock backend and synthetic request data; keep that fixture in CI.
5. Keep the high-severity Trivy gate intact. The existing image scan identifies build-time pip-bundled Python packages as high severity, so omit the unused package manager from the runtime container and rerun the scan.
6. Run `git diff --check`; review the complete diff and ensure no unrelated files or secrets are included.
7. Commit, push a feature branch, open a PR, and wait for both relevant GitHub Actions workflows to pass before merging or publishing profile claims.
