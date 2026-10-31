# Benchmark results

## Measured runs

- [`20261031T025753Z-null-backend-ryzen9800x3d-memory`](20261031T025753Z-null-backend-ryzen9800x3d-memory/report.md): gateway cost against a null backend with the in-memory limiter, 82,500 gateway requests.
- [`20261031T030824Z-null-backend-ryzen9800x3d-redis`](20261031T030824Z-null-backend-ryzen9800x3d-redis/report.md): the same run with the Redis limiter.
- [`20260803T212059Z-rtx5070ti-qwen25-3b-vllm026`](20260803T212059Z-rtx5070ti-qwen25-3b-vllm026/report.md): RTX 5070 Ti, Qwen2.5-3B-Instruct, 108 conditions, 3,000 measured requests, and zero failures. See its [`VALIDATION.md`](20260803T212059Z-rtx5070ti-qwen25-3b-vllm026/VALIDATION.md) for the conservative headline and runtime-drift limitation.

## Result layout

Each real run creates a timestamped directory containing:

- `environment.json`: hardware, software, Git, model, vLLM, and Redis metadata;
- `config.resolved.json`: the non-secret configuration used for the run;
- `raw_samples.jsonl`: one payload-free record per measured request;
- `conditions.jsonl`: official aggregate results plus resource and limiter data;
- `resource_samples.jsonl`: timestamped CPU, RAM, GPU, and optional process samples;
- `upstream/`: vLLM detailed result JSON with generated text removed;
- `summary.json`, `report.md`, and engineering SVG plots.

Do not add unmeasured or simulated performance numbers here. Test fixtures under
`tests/` validate the analysis code but are not benchmark evidence.
