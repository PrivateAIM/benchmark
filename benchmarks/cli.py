import argparse
import os

from analysis.plot_results import generate_plots
from benchmarks.concurrent_launch import run_concurrent_launch
from benchmarks.runner import run_benchmarks, run_scaling_sweep
from benchmarks.storage_benchmark import run_storage_benchmark


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run FLAME Benchmarks")
    parser.add_argument(
        "-b",
        "--benchmarks",
        nargs="+",
        default=[1, 2, 3, 4, 5, 6],
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
    return parser


def _run_plotting(summary_path: str) -> None:
    absolute_summary_path = os.path.abspath(summary_path)
    assert os.path.exists(absolute_summary_path), f"Summary file not found: {absolute_summary_path}"
    print("\n[*] Generating performance plots...")
    generate_plots(summary_path=absolute_summary_path)


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    if args.plot_only:
        assert args.summary != "", "--summary is required when --plot-only is set."
        _run_plotting(summary_path=args.summary)
        return

    if args.scaling:
        generated_summary_path = run_scaling_sweep()
        _run_plotting(summary_path=generated_summary_path)
        return

    if args.storage_bench:
        generated_summary_path = run_storage_benchmark()
        _run_plotting(summary_path=generated_summary_path)
        return

    if args.concurrent:
        generated_summary_path = run_concurrent_launch()
        _run_plotting(summary_path=generated_summary_path)
        return

    generated_summary_path = run_benchmarks(benchmark_args=args.benchmarks)
    _run_plotting(summary_path=generated_summary_path)


if __name__ == "__main__":
    main()
