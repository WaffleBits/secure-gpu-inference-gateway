# vLLM gateway overhead benchmark

This report contains measured results from the official `vllm bench serve` client. Direct and gateway paths used paired seeds, token lengths, request counts, concurrency, warmups, and model configuration.

## Predeclared headline condition

At 8 concurrent medium requests, the full gateway completed 60 measured requests per path with zero errors and observed 103.83% of direct-vLLM request throughput (paired-run range 101.37% to 108.94%). Paired p50 latency differences ranged from -1963.70 ms to -219.31 ms, so no positive gateway-added latency or speedup claim is made.

### Paired repetitions

| Rep | Direct req/s | Gateway req/s | Throughput retained | Direct p50 | Gateway p50 | p50 difference (G-D) |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.25 | 0.25 | 101.37% | 28079.33 ms | 26115.63 ms | -1963.70 ms |
| 2 | 0.37 | 0.40 | 108.94% | 18394.34 ms | 16758.53 ms | -1635.81 ms |
| 3 | 0.50 | 0.52 | 102.32% | 13157.63 ms | 12938.32 ms | -219.31 ms |

## Direct versus full gateway

| Workload | C | Direct req/s | Gateway req/s | Req/s change | Direct output tok/s | Gateway output tok/s | pooled p50 diff (G-D) | pooled p95 diff (G-D) | pooled p99 diff (G-D) | TTFT p50 diff (G-D) | Errors direct/gateway | Fair |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| short | 1 | 1.34 | 1.34 | +0.14% | 21.42 | 21.45 | +18.72 ms | +8.39 ms | -78.37 ms | +0.84 ms | 0/0 | yes |
| short | 4 | 1.75 | 1.66 | -4.97% | 27.97 | 26.58 | +163.29 ms | +313.35 ms | +265.43 ms | -30.70 ms | 0/0 | yes |
| short | 8 | 2.52 | 2.65 | +5.02% | 40.39 | 42.42 | -82.48 ms | -242.42 ms | -239.16 ms | -122.40 ms | 0/0 | yes |
| short | 16 | 3.34 | 3.90 | +16.91% | 53.37 | 62.39 | -1075.82 ms | -1203.53 ms | -1203.16 ms | -798.47 ms | 0/0 | yes |
| short | 32 | 10.43 | 9.07 | -12.98% | 166.82 | 145.18 | +148.81 ms | +649.83 ms | +651.67 ms | -75.46 ms | 0/0 | yes |
| short | 64 | 11.06 | 9.98 | -9.76% | 176.89 | 159.62 | -17.80 ms | +965.98 ms | +968.12 ms | -54.08 ms | 0/0 | yes |
| medium | 1 | 0.17 | 0.17 | -0.91% | 21.52 | 21.33 | -23.89 ms | +99.17 ms | -45.47 ms | -32.15 ms | 0/0 | yes |
| medium | 4 | 0.23 | 0.23 | +0.92% | 29.18 | 29.45 | -1252.42 ms | +568.66 ms | +669.73 ms | -114.51 ms | 0/0 | yes |
| medium | 8 | 0.35 | 0.36 | +3.83% | 44.18 | 45.87 | -1635.81 ms | -1264.81 ms | -1289.70 ms | -730.79 ms | 0/0 | yes |
| medium | 16 | 0.49 | 0.49 | -0.41% | 63.32 | 63.06 | +74.63 ms | +2165.23 ms | +2165.69 ms | +1053.32 ms | 0/0 | yes |
| medium | 32 | 1.31 | 1.29 | -1.06% | 167.12 | 165.36 | +217.05 ms | +1233.92 ms | +1104.81 ms | -6.56 ms | 0/0 | yes |
| medium | 64 | 1.46 | 1.53 | +4.73% | 187.00 | 195.85 | -4444.95 ms | -1391.58 ms | -1430.62 ms | -1341.67 ms | 0/0 | yes |
| long | 1 | 0.11 | 0.12 | +2.59% | 21.96 | 22.53 | -170.96 ms | +143.84 ms | +115.63 ms | -70.25 ms | 0/0 | yes |
| long | 4 | 0.16 | 0.15 | -4.18% | 30.50 | 29.23 | +1377.33 ms | +1006.33 ms | +1023.83 ms | +322.24 ms | 0/0 | yes |
| long | 8 | 0.22 | 0.23 | +4.05% | 43.01 | 44.75 | +1123.21 ms | -3604.77 ms | -3605.52 ms | -613.77 ms | 0/0 | yes |
| long | 16 | 0.48 | 0.48 | +0.78% | 92.15 | 92.86 | -3138.11 ms | +1967.05 ms | +1977.23 ms | -632.74 ms | 0/0 | yes |
| long | 32 | 0.81 | 0.80 | -2.23% | 156.45 | 152.95 | -317.65 ms | +2939.46 ms | +2928.94 ms | -64.22 ms | 0/0 | yes |
| long | 64 | 0.88 | 0.91 | +3.24% | 168.73 | 174.18 | -6220.16 ms | +1988.72 ms | +1907.47 ms | -1295.81 ms | 0/0 | yes |

## Environment

- Git commit: `55c0a0a98fcfc8ddd3553a609072e454c0002d5b` (dirty: `False`)
- OS: `Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-with-glibc2.39`
- CPU: `AMD Ryzen 7 9800X3D 8-Core Processor`
- RAM: `16267804672` bytes
- GPU: `NVIDIA GeForce RTX 5070 Ti (16303 MiB)`
- NVIDIA driver / CUDA: `595.97` / `13.2`
- PyTorch / CUDA build: `2.11.0+cu130` / `13.0`
- Python / vLLM: `3.12.3` / `0.26.0`
- Gateway Python / FastAPI / httpx: `3.12.3` / `0.139.2` / `0.28.1`
- Model / revision: `Qwen/Qwen2.5-3B-Instruct` / `aa8e72537993ba99e69dfaafa59ed015b17504d1`
- dtype / quantization: `bfloat16` / `None`
- Redis: `7.0.15`; configuration: `{"appendfsync": "everysec", "appendonly": "no", "maxmemory-policy": "noeviction", "save": ""}`
- Gateway controls: `{"audience": "secure-gpu-inference-gateway", "audit_enabled": true, "auth_mode": "HS256 JWT", "backend_max_connections": 256, "backend_max_keepalive_connections": 128, "demo_principals_enabled": false, "input_token_limit_per_minute": 100000000, "issuer": "https://benchmark.local", "rate_limit_backend": "redis", "rate_limit_window_seconds": 60, "redis_key_prefix": "sgig-benchmark-20260803", "request_limit_per_minute": 1000000, "trace_export_enabled": true, "workers": 1}`

## Plots

- [Tail latency versus concurrency](plots/latency-vs-concurrency.svg)
- [Request throughput versus concurrency](plots/throughput-vs-concurrency.svg)
- [TTFT versus concurrency](plots/ttft-vs-concurrency.svg)
- [Output-token throughput versus concurrency](plots/token-throughput-vs-concurrency.svg)
- [Gateway overhead versus concurrency](plots/gateway-overhead-vs-concurrency.svg)

## Interpretation constraints

- Every latency difference is gateway minus direct. Pooled differences subtract the two pooled path percentiles; paired-repetition differences subtract percentiles within each repetition. They are not one-to-one request subtractions while sharing a live GPU scheduler.
- A negative difference means the sampled gateway distribution was faster. It is not a negative internal processing cost or, by itself, evidence that the gateway accelerates inference.
- Throughput aggregates completed requests or output tokens over the sum of official client-measured condition durations.
- TTFT, TPOT, and ITL use vLLM's streaming client definitions. ITL is measured between non-empty streamed choice events.
- Redis limiter percentile values, when present, are conservative Prometheus histogram upper bounds; the mean comes from counter deltas.
- Authentication and transport costs are exactly those in the recorded gateway configuration; local HS256 and loopback HTTP do not measure TLS, JWKS retrieval, a load balancer, or cross-host network latency.
- These results characterize only the recorded host, model, server flags, and gateway configuration. They are not a production fleet claim.
