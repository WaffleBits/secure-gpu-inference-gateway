from __future__ import annotations

import argparse

from gateway.trace_exporter import post_otlp_payload, write_otlp_payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build or send an OTLP/HTTP trace payload from sanitized trace JSONL."
    )
    parser.add_argument("--input", required=True, help="Sanitized trace JSONL input path.")
    parser.add_argument("--output", required=True, help="OTLP JSON payload output path.")
    parser.add_argument(
        "--endpoint",
        help="Optional OTLP/HTTP traces endpoint, such as http://localhost:4318/v1/traces.",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Post the generated payload to --endpoint after writing it.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=2.0,
        help="Collector POST timeout when --send is used.",
    )
    args = parser.parse_args()

    payload = write_otlp_payload(args.input, args.output)
    if args.send:
        if not args.endpoint:
            parser.error("--send requires --endpoint")
        post_otlp_payload(args.endpoint, payload, timeout_seconds=args.timeout_seconds)


if __name__ == "__main__":
    main()
