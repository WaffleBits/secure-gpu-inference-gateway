# Gateway overhead against a null backend

Commit `bb6ed30` on AMD Ryzen 7 9800X3D 8-Core Processor, kernel 6.6.87.2-microsoft-standard-WSL2, Python 3.12.3.

Redis limiter, HS256 JWT, 1 uvicorn worker, no TLS, loopback. 5 paired repetitions of 5000 requests, each streaming 16 tokens.

Added latency is gateway minus direct within a repetition. The table shows the median across repetitions and the range in brackets, in microseconds.

| Concurrency | Direct p50 | Gateway p50 | Added p50 | Added p95 | Added p99 | Gateway req/s |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 211 | 5701 | 5506 [5427, 5559] | 6305 [6151, 6340] | 6580 [6444, 6721] | 173 |
| 8 | 393 | 54189 | 53836 [53521, 55053] | 61704 [61355, 62885] | 66907 [65913, 67631] | 146 |
| 32 | 1677 | 268244 | 266546 [258783, 269316] | 478775 [466394, 483218] | 636385 [625683, 662019] | 111 |

All 82500 gateway requests produced an audit event and a trace span, so the control path ran for every request.

Above concurrency 1 the added latency includes queueing on one Python worker, so those rows show saturation, not per-request cost. This is not a production capacity figure.
