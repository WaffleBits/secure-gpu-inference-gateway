# Validation and interpretation

This is a measured run, not a fixture. Inference was measured at commit
`55c0a0a98fcfc8ddd3553a609072e454c0002d5b`; the variance-aware report was
regenerated from the unchanged raw samples at analysis commit
`b41b5fa9d6d31bd26743bb7c707e0a6d90b2605c`.

The matrix completed all 108 conditions and 3,000 measured requests with zero
failures. All 18 direct/gateway comparisons passed the seed, request-index,
prompt-token, and requested-output equivalence checks. The gateway handled
1,500 measured requests plus 216 warmups. Its metrics, audit sink, and trace sink
each recorded exactly 1,716 accepted requests, with matching unique trace IDs.

The predeclared medium workload at concurrency 8 completed 60 measured requests
per path with zero errors. Aggregate gateway throughput was 0.358393 requests/s
versus 0.345163 requests/s direct, or 103.832972% retained. The three paired-run
retention values were 101.36608%, 108.939525%, and 102.318922%.

This is an observed no-throughput-loss result, not a gateway speedup claim. The
paired p50 latency differences ranged from -1,963.70 ms to -219.31 ms, while the
paired p95 differences ranged from -1,906.16 ms to +154.17 ms. vLLM throughput
also increased materially over the two-hour run. The analyzer therefore holds a
positive gateway-added latency estimate and any acceleration claim while still
reporting every pooled and paired difference.

At the headline condition, the Redis request limiter averaged 1.632236 ms per
decision and the input-token limiter averaged 1.498167 ms. Their conservative
p95 histogram bounds were 10 ms and 5 ms, respectively. These controls were in
the critical path for every gateway request.

The host required vLLM's V1 runner and native sampler because WSL lacked UVA for
the V2 runner and `nvcc` for FlashInfer sampler JIT compilation. Prefix caching
was disabled. The dominant limitation was serving-runtime drift and decode time,
not a resolved positive gateway latency signal. Results apply only to the
recorded RTX 5070 Ti, Qwen2.5-3B, WSL, loopback HTTP, and local HS256 setup.

`validation.json` contains the machine-readable integrity counts. The result
tree was scanned for the local signing secret, JWT material, prompt payloads,
and generated text; none were present.
