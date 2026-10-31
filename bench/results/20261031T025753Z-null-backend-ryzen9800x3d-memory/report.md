# Gateway overhead against a null backend

Commit `bb6ed30` on AMD Ryzen 7 9800X3D 8-Core Processor, kernel 6.6.87.2-microsoft-standard-WSL2, Python 3.12.3.

Memory limiter, HS256 JWT, 1 uvicorn worker, no TLS, loopback. 5 paired repetitions of 5000 requests, each streaming 16 tokens.

Added latency is gateway minus direct within a repetition. The table shows the median across repetitions and the range in brackets, in microseconds.

| Concurrency | Direct p50 | Gateway p50 | Added p50 | Added p95 | Added p99 | Gateway req/s |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 181 | 4841 | 4663 [4621, 4703] | 5309 [5213, 5347] | 5513 [5422, 5638] | 205 |
| 8 | 378 | 45391 | 45017 [42645, 47068] | 53854 [49534, 55956] | 57184 [53164, 61927] | 172 |
| 32 | 1693 | 228817 | 227125 [215228, 237298] | 427427 [417617, 453489] | 566272 [561892, 621325] | 128 |

All 82500 gateway requests produced an audit event and a trace span, so the control path ran for every request.

Above concurrency 1 the added latency includes queueing on one Python worker, so those rows show saturation, not per-request cost. This is not a production capacity figure.
