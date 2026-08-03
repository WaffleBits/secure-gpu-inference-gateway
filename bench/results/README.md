# Benchmark results

Each real run creates a timestamped directory containing:

- `environment.json`: hardware, software, Git, model, vLLM, and Redis metadata;
- `config.resolved.json`: the non-secret configuration used for the run;
- `raw_samples.jsonl`: one payload-free record per measured request;
- `conditions.jsonl`: official aggregate results plus resource and limiter data;
- `resource_samples.jsonl`: timestamped CPU, RAM, GPU, and optional process samples;
- `upstream/`: vLLM detailed result JSON with generated text removed;
- `summary.json`, `report.md`, and engineering SVG plots.

Do not add hand-written or simulated performance numbers here. Test fixtures under
`tests/` validate the analysis code but are not benchmark evidence.
