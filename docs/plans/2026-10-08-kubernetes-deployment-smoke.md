# Plan: verify the hardened Kubernetes deployment at runtime

Date: 2026-10-08 (America/New_York)
Repository: `WaffleBits/secure-gpu-inference-gateway`
Branch: `feat/kubernetes-deployment-smoke-20261008`

## Current role signal from official listings

The following company-hosted Ashby listings were active (HTTP 200) and read on 2026-10-08. Dates are the pages' `datePosted` fields. Compensation is quoted as listed; equity is not valued. The listings do not expose closing dates.

| Role | Listing / date posted | Published compensation | Location / constraint | Repeated requirements relevant here |
|---|---|---:|---|---|
| Cognition, Software Engineer, Infrastructure | https://jobs.ashbyhq.com/cognition/13fdacf7-b4dc-4b9a-ac43-addc87de79ec / 2026-08-06 | `$260,000 - $300,000` base + significant equity | San Francisco or New York City | Kubernetes, cloud, Terraform, Python, containerized agent execution, reliability, observability, CI/CD, security and isolation |
| Replit, Staff Infrastructure Engineer | https://jobs.ashbyhq.com/replit/6481ec1e-527c-4c1f-a041-2fb5021e7bd5 / 2026-05-29 | `$220,000 - $325,000` annual base + equity | Foster City, CA; listing also states in-office Monday/Wednesday/Friday despite its structured `TELECOMMUTE` field | Kubernetes, Docker, GCP, Terraform/Pulumi, Python/Go, automation, distributed systems, observability, reliability and high availability; 8-10 years requested |
| Edison Scientific, Member of Technical Staff, Infrastructure Engineer | https://jobs.ashbyhq.com/edison%20scientific/45741c0b-a749-4be5-b561-17964c7d49ca / 2026-08-25 | `$175,000 - $240,000` USD/year | San Francisco, New York, or Boston | Kubernetes, cloud, IaC, Python/Go/Java/TypeScript, deployment automation, monitoring, storage/networking/security and incident response |
| Roboflow, Infrastructure Engineer | https://jobs.ashbyhq.com/roboflow/13df0a39-1845-4634-846d-d01f2a573b54 / 2026-09-02 | `USD $180,000 base - $230,000 base` | United States; description supports working from a hub or remotely | Kubernetes, Terraform/Helm, AWS/GCP, Python/Node.js, CI/CD, reliability, observability, security and cost management |

The common, portfolio-relevant signal is not merely Kubernetes syntax: infrastructure teams expect deployable container workloads with security and reliability controls, plus repeatable validation. Role seniority and actual cloud/production ownership remain separate qualifications.

## Public evidence inventory and map

The authenticated `WaffleBits` GitHub account exposes 21 public repositories. The Astro profile is `https://github.com/WaffleBits/WaffleBits`; Pages is `https://wafflebits.github.io/WaffleBits/`. Strong related evidence includes `triton-inference-benchmark` (Python serving/lifecycle/recovery fixtures), `deterministic-inference-scheduler` (Rust replay/release gates), `triton-kernel-lab` (GPU correctness/performance artifacts), and this repository (gateway policy, audit, telemetry, deployment posture, and supply-chain checks).

**Already demonstrated:** the gateway has a Docker image, Kubernetes Namespace/ConfigMap/Deployment/Service, non-root UID, seccomp, dropped capabilities, read-only root filesystem, probes, resource requests/limits, and static manifest assertions. CI runs Python tests, dependency audit, SBOM generation, and a high/critical image scan.

**Present but buried:** the Kubernetes controls are only statically inspected by substring tests; CI does not apply the checked manifest to a cluster or verify the resulting Pods/Service and reachable application endpoints.

**Missing and not claimed:** cloud-provider provisioning, managed-cluster operations, real customer traffic, multi-region operation, production SLOs/on-call, and production capacity.

## Exactly one selected gap

Add an ephemeral Kind integration check for the existing Kubernetes deployment. Build and load the real gateway image, apply the repository manifest, verify live Pod/Service security and resource settings, then exercise health, metrics, and unauthenticated inference denial through the Kubernetes Service. Add unit tests for the runtime-object contract and run the integration in GitHub Actions.

This extends the existing gateway; it does not add a new project, cloud account, production claim, fabricated metric, or public profile claim before the source implementation and CI are verified.

## Acceptance checks

- Unit tests reject unsafe live Kubernetes objects and accept the expected hardened fixture.
- A real disposable Kind cluster applies `deploy/kubernetes/gateway.yaml`, reaches rollout readiness, and passes the Service-path smoke checks.
- Existing Python tests, import check, dependency audit, container SBOM/vulnerability gate, and `git diff --check` pass.
- The pull-request and post-merge source workflows pass before the profile is updated.
