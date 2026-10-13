import argparse
import os
import json

from analysis.plot_results import generate_plots
from benchmarks.concurrent_launch import run_concurrent_launch
from benchmarks.runner import run_benchmarks, run_scaling_sweep
from benchmarks.storage_benchmark import run_storage_benchmark
from benchmarks.encryption_benchmark import run_encryption_benchmark


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run FLAME Benchmarks")
    parser.add_argument(
        "-b",
        "--benchmarks",
        nargs="+",
        default=[0, 1, 2, 3, 4, 5, 6, 7],
        help="Select benchmarks to run (e.g., -b 0 3 or -b * for all)",
    )
    parser.add_argument(
        "--plot-only",
        action="store_true",
        help="Skip benchmark execution and generate plots from --summary.",
    )
    parser.add_argument(
        "--scaling",
        action="store_true",
        help="Run the node-count scaling sweep (benchmarks/05_node_scaling.py) instead of -b selection.",
    )
    parser.add_argument(
        "--storage-bench",
        action="store_true",
        help="Run the MinIO storage micro-benchmark instead of -b selection.",
    )
    parser.add_argument(
        "--concurrent",
        action="store_true",
        help="Run the concurrent-launch (build/distribution queuing) benchmark instead of -b selection.",
    )
    parser.add_argument(
        "--summary",
        default="",
        help="Path to summary JSON for plotting (required with --plot-only).",
    )
    parser.add_argument("--repeats", type=int, default=3, help="Independent full runs per benchmark (default: 3).")
    parser.add_argument("--analysis-retries", type=int, default=0,
                        help="Fresh analyses after failures; all attempts are retained (default: 0).")
    parser.add_argument("--environment", default="", help="Cluster/environment label recorded with results.")
    parser.add_argument("--condition", choices=["unspecified", "cold", "warm"], default="unspecified",
                        help="Operator-provided condition label; does not clear caches.")
    parser.add_argument("--node-info", default="", help="JSON file keyed by node names containing CPU/RAM/placement/version details.")
    parser.add_argument("--seed", type=int, default=0, help="Benchmark ordering randomization seed.")
    parser.add_argument("--encryption-bench", action="store_true", help="Local ECDH/HKDF/AES-GCM primitive measurements.")
    return parser


def _run_plotting(summary_path: str) -> None:
    absolute_summary_path = os.path.abspath(summary_path)
    assert os.path.exists(absolute_summary_path), f"Summary file not found: {absolute_summary_path}"
    print("\n[*] Generating performance plots...")
    generate_plots(summary_path=absolute_summary_path)


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()
    assert args.repeats >= 1 and args.analysis_retries >= 0
    assert sum([args.scaling, args.storage_bench, args.concurrent, args.encryption_bench, args.plot_only]) <= 1, "Run one mode at a time."
    node_info = {}
    if args.node_info:
        with open(args.node_info, encoding="utf-8") as handle:
            node_info = json.load(handle)
        assert isinstance(node_info, dict)
    options = {"repeats": args.repeats, "analysis_retries": args.analysis_retries,
               "environment": args.environment, "condition": args.condition,
               "node_info": node_info, "seed": args.seed}

    if args.plot_only:
        assert args.summary != "", "--summary is required when --plot-only is set."
        _run_plotting(summary_path=args.summary)
        return

    if args.scaling:
        generated_summary_path = run_scaling_sweep(**options)
    elif args.storage_bench:
        generated_summary_path = run_storage_benchmark(**options)
    elif args.concurrent:
        generated_summary_path = run_concurrent_launch(**options)
    elif args.encryption_bench:
        generated_summary_path = run_encryption_benchmark(**options)
    else:
        generated_summary_path = run_benchmarks(benchmark_args=args.benchmarks, **options)
    _run_plotting(summary_path=generated_summary_path)
    with open(generated_summary_path, encoding="utf-8") as handle:
        results = json.load(handle)
    if any(row["status"] == "Failed" and ("final_attempt" not in row or row["final_attempt"]) for row in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
