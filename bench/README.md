# End-to-end vLLM gateway benchmark

This benchmark answers one question: what latency and throughput cost does the
full secure gateway add in front of the same vLLM server under the same
workload?

It compares:

```text
official vLLM benchmark client -> vLLM -> GPU
official vLLM benchmark client -> gateway -> vLLM -> GPU
```

The gateway path keeps authentication, role policy, request limiting,
input-token budgeting, audit logging, Prometheus metrics, trace export, and
streaming proxying enabled. The default measured matrix does not include a
"proxy-only" mode because this application has no production-safe switch that
independently removes its security controls. Direct vLLM is the baseline and
the full gateway is the treatment.

The load generator is the upstream [`vllm bench serve`](https://docs.vllm.ai/en/stable/cli/bench/serve/)
command with detailed result saving enabled. The repository orchestrator adds
balanced ordering, environment capture, resource sampling, Redis-limiter
telemetry, workload-equivalence checks, analysis, and plots. It does not replace
vLLM's timing implementation with a custom approximation.

## What is measured

For every workload, concurrency, path, and repetition the run records:

- completed and failed requests;
- completed requests per second;
- output and total token throughput;
- end-to-end latency mean, standard deviation, min, max, p50, p95, and p99;
- time to first token (TTFT), time per output token (TPOT), and inter-token
  latency (ITL);
- gateway-added p50, p95, and p99 end-to-end latency;
- gateway TTFT overhead and relative throughput change;
- system CPU and RAM, GPU utilization and memory, and optional per-process CPU
  and RSS;
- request-limit and token-budget decision latency. Redis mode reports the mean
  from Prometheus counter deltas and conservative percentile bucket upper bounds.

Warmups run before every measured condition. Repetition order alternates between
direct and gateway paths to reduce systematic cache or thermal bias.

The example vLLM server disables automatic prefix caching. Paired paths use
the exact same seeded prompts, so leaving the cache enabled would let whichever
path runs second reuse the first path's KV entries. If a deployment must retain
prefix caching, reset that cache between every condition and record the reset
method; alternating order alone is not a cache flush.

## Requirements

- Linux or WSL2 with a CUDA-capable NVIDIA GPU supported by the selected vLLM
  build;
- an installed `vllm` CLI that exposes `vllm bench serve`;
- Python with the gateway and benchmark dependencies;
- Redis for the full distributed-budget path;
- enough storage for the model and vLLM build;
- optional Docker Engine plus NVIDIA Container Toolkit for the Compose path.

Install the repository dependencies in the benchmark environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-redis.txt -r bench/requirements.txt
```

Install vLLM using its current hardware-specific installation guidance. Keep
the exact vLLM, PyTorch, CUDA, and driver versions fixed for a comparison; the
runner captures them in `environment.json`.

## Select and start a real model

The model is configurable. The example uses a 3B model as a conservative
starting point for a 16 GB GPU; it is not assumed to fit every host.

```bash
vllm serve Qwen/Qwen2.5-3B-Instruct \
  --host 127.0.0.1 \
  --port 8001 \
  --served-model-name benchmark-echo \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.90 \
  --no-enable-prefix-caching
```

`benchmark-echo` is the API alias protected by the repository's benchmark
policy. The benchmark config keeps the tokenizer/model identifier separate
from this served name so the direct and gateway payloads both request the same
alias.

Before continuing, verify the backend itself:

```bash
curl http://127.0.0.1:8001/v1/models
curl -N http://127.0.0.1:8001/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"benchmark-echo","prompt":"hello","max_tokens":8,"stream":true}'
```

Record any non-default engine flags in `model.vllm_server_args` in the benchmark
config. Record any `VLLM_*` compatibility switches in
`model.vllm_environment`. Set `model.revision`, `dtype`, and `quantization` to
the actual values.

On WSL, a CUDA runtime can be available even when unified virtual addressing
or the CUDA compiler is not. If the vLLM V2 runner fails with `UVA is not
available`, `VLLM_USE_V2_MODEL_RUNNER=0` selects the V1 runner. If FlashInfer's
sampler then tries to JIT compile without `nvcc`,
`VLLM_USE_FLASHINFER_SAMPLER=0` selects vLLM's native sampling path. Apply the
same settings for the entire run, record both in the config, and retain the
startup log; these switches change the measured serving runtime.

## Start Redis and the full gateway

Start a dedicated Redis instance, then launch the gateway with a high but still
enforced benchmark budget. The high limits prevent the measurement matrix from
being rejected while every request still executes both atomic Lua decisions.

```bash
redis-server bench/redis-benchmark.conf
```

In a second shell:

```bash
export INFERENCE_BACKEND_COMPLETIONS_URL=http://127.0.0.1:8001/v1
export INFERENCE_BACKEND_TIMEOUT_MS=7200000
export INFERENCE_BACKEND_MAX_CONNECTIONS=256
export INFERENCE_BACKEND_MAX_KEEPALIVE_CONNECTIONS=128
export RATE_LIMIT_BACKEND=redis
export REDIS_URL=redis://127.0.0.1:6379/0
export REDIS_KEY_PREFIX=sgig-benchmark
export BENCHMARK_REQUESTS_PER_MINUTE=1000000
export BENCHMARK_INPUT_TOKENS_PER_MINUTE=100000000
export AUDIT_LOG_PATH=/tmp/sgig-benchmark-audit.jsonl
export TRACE_EXPORT_PATH=/tmp/sgig-benchmark-traces.jsonl
export ALLOW_DEMO_PRINCIPALS=true
python -m uvicorn gateway.app:app --host 127.0.0.1 --port 8000 --workers 1
```

The example config authenticates with `X-Principal-Id: analyst-1`, the existing
local-review identity path. For a full JWT run, remove that header from the
config, set the gateway OIDC variables, disable demo principals, and put the
token value (without the `Bearer` prefix) in the environment variable named by
`paths.gateway.api_key_env`. The runner maps it to `OPENAI_API_KEY` only inside
the gateway benchmark client process and does not store its value. Record the
non-secret auth mode, issuer, audience, worker count, limiter mode, budgets,
pool bounds, and enabled audit/trace sinks in `gateway_configuration`.

Verify the complete streaming path before loading it:

```bash
curl -N http://127.0.0.1:8000/v1/completions \
  -H 'Content-Type: application/json' \
  -H 'X-Principal-Id: analyst-1' \
  -d '{"model":"benchmark-echo","prompt":"hello","max_tokens":8,"stream":true,"stream_options":{"include_usage":true}}'
```

## Configure the workload

Copy `bench/config.example.json` and edit it rather than modifying the example
in place. The checked example defines:

- token shapes `32/16`, `256/128`, and `512/256` for short, medium, and longer
  generation workloads;
- concurrency `1, 4, 8, 16, 32, 64`;
- three repetitions;
- at least 32 requests per condition and at least two requests per concurrency
  slot;
- four warmup requests per condition;
- a fixed seed and zero workload-length variance;
- `medium` at concurrency `8` as the headline condition, declared before data
  collection.

Reduce concurrency or token lengths when the selected model/hardware would OOM.
Make the change in the config before running both paths; never change only the
baseline or gateway condition.

If the gateway and vLLM PIDs are known, add them to `monitored_pids`:

```json
{"gateway": 1234, "vllm": 5678, "redis": 9012}
```

Host-level CPU, RAM, GPU utilization, GPU memory, power, and temperature are
captured even when process PIDs are omitted.

If the gateway and benchmark client use separate Python environments, add the
gateway interpreter to `python_environments`. The runner will record that
interpreter's Python, FastAPI, httpx, Redis-client, and other relevant package
versions rather than implying that the client's dependency set is the
gateway's.

When a WSL benchmark runs from a Git for Windows worktree, the Linux `git`
binary cannot resolve the Windows absolute `gitdir` pointer. Set
`git_command` to the mounted Git for Windows executable (for example,
`["/mnt/c/Program Files/Git/cmd/git.exe"]`) so commit, branch, and dirty-state
provenance are still captured.

## Run

Inspect the full paired schedule and exact non-secret commands:

```bash
python -m bench.run --config bench/config.local.json --dry-run
```

Run a two-request end-to-end smoke condition first:

```bash
python -m bench.run --config bench/config.local.json --smoke
```

Then run the full matrix:

```bash
python -m bench.run --config bench/config.local.json
```

The runner refuses to begin if either vLLM `/v1/models` or gateway `/health` is
unreachable. A failed condition is written to `conditions.jsonl` and aborts the
run by default; failed requests are retained in raw data and error-rate tables.

To regenerate analysis without rerunning inference:

```bash
python -m bench.analyze bench/results/<run-directory>
```

## Result layout and raw schema

Every run is isolated under `bench/results/<UTC timestamp>-<run name>/`:

```text
environment.json
config.resolved.json
raw_samples.jsonl
conditions.jsonl
resource_samples.jsonl
upstream/*.json
logs/*.json
summary.json
report.md
plots/*.svg
```

`raw_samples.jsonl` contains one record per measured request, including path,
workload, concurrency, repetition, seed, actual prompt and completion token
counts, TTFT, end-to-end latency, TPOT, all ITL samples, and failure information.
It excludes prompts, generated text, credentials, and endpoint URLs. The saved
upstream detailed JSON also has `generated_texts` removed.

The analysis verifies that direct and gateway runs have matching seeds, request
indexes, actual prompt-token counts, and requested output lengths for every
repetition. A mismatch holds the predeclared headline.

## Statistical interpretation

The report pools request distributions across repetitions and also reports the
population standard deviation of per-run p50, p95, and throughput. Throughput is
computed from total completions or tokens divided by total official client
duration. Percentiles use linear interpolation over successful request samples.

Gateway overhead is:

```text
gateway-path percentile - direct-vLLM percentile
```

This is a difference between matched condition distributions, not a fabricated
per-request subtraction across two sequential GPU runs. The headline is emitted
only when the predeclared condition has at least two repetitions, at least 30
successful samples per path, zero failures, and equivalent workloads.

## Redis correctness outside the GPU benchmark

The unit suite always verifies that the configured limiter uses the checked Lua
script and hashed principal keys. Set `REDIS_TEST_URL` to run live integration
tests against one disposable Redis service; they drive concurrent decisions
through four client instances, verify exact atomic request/token budgets, caller
isolation, and expiration:

```bash
REDIS_TEST_URL=redis://127.0.0.1:6379/15 \
  python -m unittest tests.test_redis_integration -v
```

The Compose replica profile starts two independent gateway processes sharing one
Redis budget and using the deterministic mock backend, so no GPU is needed:

```bash
docker compose -f bench/docker-compose.yml --profile redis-correctness \
  up --build -d redis gateway-a gateway-b
python -m bench.redis_replica_check \
  --gateway-url http://127.0.0.1:8010 \
  --gateway-url http://127.0.0.1:8011 \
  --request-limit 10 \
  --window-seconds 2 \
  --total-requests 30 \
  --output bench/results/redis-replica-check.json
```

Use a fresh `REDIS_KEY_PREFIX` or restart the disposable Redis service before
repeating that check.

## Containerized GPU path

Copy `bench/.env.example` to a local `.env`, pin the vLLM image tag or digest,
and edit the model/engine values. Then:

```bash
docker compose --env-file bench/.env -f bench/docker-compose.yml \
  --profile gpu up --build redis vllm gateway
```

The Compose file requests one NVIDIA GPU and uses host IPC for vLLM. GPU
passthrough still requires a working Docker Engine and NVIDIA Container Toolkit;
Compose cannot install or validate host drivers.

## Known limitations

- The gateway's policy budget estimates input tokens as characters divided by
  four, while the report records the tokenizer counts returned by vLLM. High
  benchmark limits keep this estimator from changing the paired workload.
- `/v1/completions` intentionally accepts a bounded subset of completion fields,
  one prompt, and `n=1`; it is a benchmarkable secure proxy, not a claim of full
  OpenAI API compatibility.
- The official client timestamps end-to-end completion at the last streamed
  choice event. Audit/trace finalization still affects condition duration and
  throughput because the response stream does not close until finalization ends.
- Host CPU/GPU samples include other activity unless per-process PIDs are
  supplied and the benchmark host is otherwise isolated.
- Prometheus limiter percentiles are bucket bounds, not invented exact values.
- The recorded local full-gateway run uses HS256 signature verification and
  loopback HTTP. It does not include TLS termination, a JWKS network/cache path,
  load balancing, or cross-host network latency.
- WSL hosts without UVA or `nvcc` may require the documented V1/native-sampler
  switches. Results from that runtime must not be compared as if they used the
  default V2/FlashInfer path.
- `nvidia-smi` sampling is enabled for both paths and adds a small amount of
  host-side measurement activity.
- Results from a single workstation/model do not establish production or
  multi-GPU scalability.
