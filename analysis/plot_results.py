import argparse
import glob
import json
import os
from collections.abc import Callable
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

CANONICAL_SCRIPT_BY_BENCHMARK = {
    "test_connection": "00_test_connection.py",
    "latency_overhead": "01_latency_overhead.py",
    "large_data_handling": "02_large_data_handling.py",
    "concurrent_scaling": "03_concurrent_scaling.py",
    "straggler_sensitivity": "04_straggler_sensitivity.py",
    "node_scaling": "05_node_scaling.py",
    "no_star_baseline": "06_no_star_baseline.py",
    "storage_io": "storage_benchmark.py",
    "concurrent_launch": "concurrent_launch.py",
}

# Benchmarks whose per-round time series get a generic jitter/moving-average
# overlay plot, keyed by the metric key(s) in benchmark_metrics to plot.
ROUND_SERIES_METRIC_KEYS: dict[str, list[str]] = {
    "latency_overhead": ["round_interval_seconds"],
    "no_star_baseline": ["round_interval_seconds"],
    "large_data_handling": ["round_interval_seconds"],
    "straggler_sensitivity": ["compute_spread_seconds"],
}

BOX_COLORS = ["#3498db", "#2980b9", "#1abc9c", "#16a085", "#2ecc71", "#e67e22", "#9b59b6"]


# --------------------------------------------------------------------------- #
# Summary loading
# --------------------------------------------------------------------------- #
def _find_latest_summary(raw_output_dir: str) -> str:
    summary_files = glob.glob(os.path.join(raw_output_dir, "*summary_*.json"))
    assert len(summary_files) > 0, "No summary files found in raw_output directory."
    return max(summary_files, key=os.path.getctime)


def _load_summary(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as summary_file:
        payload = json.load(summary_file)
    assert isinstance(payload, list), "Summary file must contain a JSON list."
    return payload


def _canonical_script_name(result: dict) -> str:
    benchmark_key = str(result.get("benchmark", ""))
    assert (
        benchmark_key in CANONICAL_SCRIPT_BY_BENCHMARK
    ), f"Unsupported benchmark key for plotting: {benchmark_key}"
    return CANONICAL_SCRIPT_BY_BENCHMARK[benchmark_key]


# --------------------------------------------------------------------------- #
# Shared plot primitives
# --------------------------------------------------------------------------- #
def _colored_boxplot(ax, datasets: list[list[float]], labels: list[str]) -> None:
    box = ax.boxplot(datasets, patch_artist=True, tick_labels=labels)
    for index, patch in enumerate(box["boxes"]):
        patch.set_facecolor(BOX_COLORS[index % len(BOX_COLORS)])
    ax.tick_params(axis="x", rotation=20)


def _build_node_and_combined_datasets(
    samples_by_node: dict[str, list[float]],
    node_labels: dict[str, str],
    combined_node_ids: list[str],
    transform: Callable[[float], float],
) -> tuple[list[list[float]], list[str]]:
    assert (
        isinstance(samples_by_node, dict) and len(samples_by_node) > 0
    ), "Missing per-node samples."
    assert len(combined_node_ids) > 0, "Missing combined_node_ids for Combined boxplot."

    ordered_node_ids = [node_id for node_id in combined_node_ids if node_id in samples_by_node]
    assert len(ordered_node_ids) > 0, "No combined nodes found in per-node sample map."

    datasets: list[list[float]] = []
    labels: list[str] = []
    combined_values: list[float] = []

    for node_id in ordered_node_ids:
        converted = [transform(float(value)) for value in samples_by_node[node_id]]
        if len(converted) == 0:
            continue
        datasets.append(converted)
        labels.append(node_labels.get(node_id, node_id))
        combined_values.extend(converted)

    assert len(combined_values) > 0, "Combined distribution is empty."
    datasets.append(combined_values)
    labels.append("Combined")

    return datasets, labels


def _per_node_boxplot(
    ax,
    samples_by_node: dict[str, Any],
    node_labels: dict[str, str],
    combined_node_ids: list[str],
    transform: Callable[[float], float],
    ylabel: str,
) -> None:
    datasets, labels = _build_node_and_combined_datasets(
        samples_by_node={
            str(node_id): [float(value) for value in values]
            for node_id, values in samples_by_node.items()
        },
        node_labels=node_labels,
        combined_node_ids=combined_node_ids,
        transform=transform,
    )
    _colored_boxplot(ax, datasets, labels)
    ax.set_ylabel(ylabel)


def _plot_round_series(ax, series: dict[str, list[float]], moving_average_window: int = 10) -> None:
    for index, (label, values) in enumerate(series.items()):
        if len(values) == 0:
            continue
        color = BOX_COLORS[index % len(BOX_COLORS)]
        ax.plot(values, marker="o", linestyle="None", alpha=0.4, color=color, label=f"{label} (raw)")

        window = min(moving_average_window, len(values))
        moving_average = [
            sum(values[max(0, i - window + 1) : i + 1]) / len(values[max(0, i - window + 1) : i + 1])
            for i in range(len(values))
        ]
        ax.plot(moving_average, linewidth=2.5, color=color, label=f"{label} ({window}-round avg)")

    ax.set_xlabel("Round Number")
    ax.legend()


# --------------------------------------------------------------------------- #
# Per-benchmark summary plotters: all share signature (ax, metrics, node_labels,
# combined_node_ids); plotters that don't need node info ignore the extra args.
# --------------------------------------------------------------------------- #
def _plot_connection_metrics(ax, metrics, node_labels, combined_node_ids) -> None:
    node_results = metrics["node_results"]
    ordered_node_ids = sorted(
        node_results.keys(), key=lambda node_id: node_labels.get(node_id, node_id)
    )
    nodes = [node_labels.get(node_id, node_id) for node_id in ordered_node_ids]
    values = [1.0 if node_results[node_id] == "ok" else 0.0 for node_id in ordered_node_ids]

    bars = ax.bar(nodes, values, color="#27ae60")
    ax.set_ylim(0.0, 1.2)
    ax.set_ylabel("Success")
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2.0, value + 0.03, f"{int(value)}", ha="center")


def _plot_latency_metrics(ax, metrics, node_labels, combined_node_ids) -> None:
    per_node_samples = metrics.get("per_node_latency_samples_seconds", {})
    if per_node_samples:
        _per_node_boxplot(
            ax, per_node_samples, node_labels, combined_node_ids,
            transform=lambda value: value / 2.0, ylabel="Seconds",
        )
    else:
        samples = metrics.get("effective_communication_latency_max_samples_seconds", [])
        _colored_boxplot(ax, [[float(s) / 2.0 for s in samples]], ["Per-Communication"])
        ax.set_ylabel("Seconds")


def _plot_large_data_metrics(ax, metrics, node_labels, combined_node_ids) -> None:
    per_node_samples = metrics.get("per_node_throughput_mbps_samples", {})
    if per_node_samples:
        _per_node_boxplot(
            ax, per_node_samples, node_labels, combined_node_ids,
            transform=lambda value: value / 8.0, ylabel="MB/s",
        )
    else:
        samples = metrics.get("effective_application_throughput_mbps_samples", [])
        _colored_boxplot(ax, [[float(s) / 8.0 for s in samples]], ["Combined"])
        ax.set_ylabel("MB/s")


def _plot_large_data_size_fit(ax, metrics: dict) -> None:
    sizes_bytes = metrics.get("sizes_bytes", [])
    throughput_samples = metrics.get("effective_application_throughput_mbps_samples", [])

    # Round 0 (size 0) is the warm-up with no throughput sample, so the recorded
    # samples align with sizes_bytes[1:].
    fit_sizes = [float(size) for size in sizes_bytes[1 : 1 + len(throughput_samples)]]
    assert len(fit_sizes) == len(throughput_samples), "Size/throughput sample length mismatch."

    points = [(size, (size * 8) / (mbps * 1_000_000)) for size, mbps in zip(fit_sizes, throughput_samples) if mbps > 0]
    assert len(points) > 0, "No non-zero throughput samples to fit."
    sizes_nonzero = [size for size, _ in points]
    round_seconds = [seconds for _, seconds in points]

    ax.scatter(sizes_nonzero, round_seconds, color="#2980b9", label="Observed round time")
    if len(sizes_nonzero) >= 2:
        slope, intercept = np.polyfit(sizes_nonzero, round_seconds, 1)
        fit_x = np.linspace(min(sizes_nonzero), max(sizes_nonzero), 100)
        ax.plot(
            fit_x, slope * fit_x + intercept, color="#e74c3c", linewidth=2.0,
            label=f"Fit: {intercept:.3f}s + {slope * 1_000_000:.4f}s/MB",
        )

    ax.set_xlabel("Payload Size (Bytes)")
    ax.set_ylabel("Round Time (Seconds)")
    ax.legend()


def _plot_straggler_metrics(ax, metrics, node_labels, combined_node_ids) -> None:
    _per_node_boxplot(
        ax, metrics["per_node_compute_samples_seconds"], node_labels, combined_node_ids,
        transform=lambda value: value * 1000.0, ylabel="Milliseconds",
    )


def _plot_concurrent_scaling_metrics(ax, metrics, node_labels, combined_node_ids) -> None:
    series = {}
    if metrics.get("round_interval_seconds"):
        series["Round Interval"] = metrics["round_interval_seconds"]
    _plot_round_series(ax, series)
    ax.set_ylabel("Round Interval (Seconds)")


def _plot_storage_io_metrics(ax, metrics, node_labels, combined_node_ids) -> None:
    samples_by_size = metrics["write_seconds_by_size"]
    sizes = sorted(samples_by_size.keys(), key=lambda size: int(size))
    datasets = [samples_by_size[size] for size in sizes]
    labels = [f"{int(size) / 1_000_000:.3f} MB" for size in sizes]
    _colored_boxplot(ax, datasets, labels)
    ax.set_ylabel("Write Seconds")


def _plot_concurrent_launch_metrics(ax, metrics, node_labels, combined_node_ids) -> None:
    build_by_level = metrics["build_seconds_by_level"]
    dist_by_level = metrics["distribution_seconds_by_level"]
    levels = sorted(int(level) for level in build_by_level.keys())

    for label, samples_by_level, color in (
        ("Build", build_by_level, "#2980b9"),
        ("Distribution", dist_by_level, "#e67e22"),
    ):
        # Each launched analysis is one point at its concurrency level; the spread
        # within a level shows how queuing affects individual analyses.
        scatter_x = [level for level in levels for _ in samples_by_level[str(level)]]
        scatter_y = [value for level in levels for value in samples_by_level[str(level)]]
        ax.scatter(scatter_x, scatter_y, color=color, alpha=0.5, label=f"{label} (per analysis)")

        means = [sum(samples_by_level[str(level)]) / len(samples_by_level[str(level)]) for level in levels]
        ax.plot(levels, means, color=color, linewidth=2.0, marker="o", label=f"{label} (mean)")

    ax.set_xlabel("Concurrent Analyses Launched")
    ax.set_ylabel("Phase Duration (Seconds)")
    ax.legend()


SUMMARY_PLOTTERS: dict[str, Callable] = {
    "test_connection": _plot_connection_metrics,
    "latency_overhead": _plot_latency_metrics,
    "no_star_baseline": _plot_latency_metrics,
    "node_scaling": _plot_latency_metrics,
    "large_data_handling": _plot_large_data_metrics,
    "straggler_sensitivity": _plot_straggler_metrics,
    "concurrent_scaling": _plot_concurrent_scaling_metrics,
    "storage_io": _plot_storage_io_metrics,
    "concurrent_launch": _plot_concurrent_launch_metrics,
}


# --------------------------------------------------------------------------- #
# Figure dispatch / saving
# --------------------------------------------------------------------------- #
def _save_figure(figure, plots_dir: str, base_name: str, plot_name: str) -> str:
    benchmark_dir = os.path.join(plots_dir, base_name)
    os.makedirs(benchmark_dir, exist_ok=True)
    svg_path = os.path.join(benchmark_dir, f"{plot_name}.svg")
    figure.savefig(svg_path, dpi=150, format="svg")
    figure.savefig(os.path.join(benchmark_dir, f"{plot_name}.png"), dpi=150, format="png")
    plt.close(figure)
    return svg_path


def _plot_figure(plots_dir: str, base_name: str, plot_name: str, draw: Callable[[Any], None]) -> str:
    figure, ax = plt.subplots(1, 1, figsize=(8, 5))
    draw(ax)
    figure.tight_layout()
    return _save_figure(figure, plots_dir, base_name, plot_name)


def _plot_single_benchmark(result: dict, plots_dir: str) -> list[str]:
    benchmark_key = str(result["benchmark"])
    base_name = _canonical_script_name(result).replace(".py", "")
    metrics = result["benchmark_metrics"]
    node_labels = {str(key): str(value) for key, value in result.get("node_labels", {}).items()}
    combined_node_ids = [str(node_id) for node_id in result.get("combined_node_ids", [])]

    plotter = SUMMARY_PLOTTERS.get(benchmark_key)
    assert plotter is not None, f"Unsupported benchmark plot routing for key: {benchmark_key}"

    saved_paths = [
        _plot_figure(
            plots_dir, base_name, "summary",
            lambda ax: plotter(ax, metrics, node_labels, combined_node_ids),
        )
    ]

    if benchmark_key == "large_data_handling":
        saved_paths.append(
            _plot_figure(plots_dir, base_name, "size_fit", lambda ax: _plot_large_data_size_fit(ax, metrics))
        )

    if benchmark_key in ROUND_SERIES_METRIC_KEYS:
        series = {key: metrics.get(key, []) for key in ROUND_SERIES_METRIC_KEYS[benchmark_key]}
        saved_paths.append(
            _plot_figure(plots_dir, base_name, "timeseries", lambda ax: _plot_round_series(ax, series))
        )

    return saved_paths


def _plot_node_scaling_sweep(results: list[dict], plots_dir: str) -> str:
    # x-axis is the analyzer count (combined_node_ids excludes the aggregator),
    # not result["node_count"] which counts the aggregator too.
    sweep_points = sorted(
        (
            (
                len(result["combined_node_ids"]),
                [
                    float(s) / 2.0
                    for s in result["benchmark_metrics"]["effective_communication_latency_max_samples_seconds"]
                ],
            )
            for result in results
        ),
        key=lambda point: point[0],
    )
    node_counts = [count for count, _ in sweep_points]
    medians = [float(np.median(samples)) for _, samples in sweep_points]
    minimums = [min(samples) for _, samples in sweep_points]
    maximums = [max(samples) for _, samples in sweep_points]

    def draw(ax) -> None:
        ax.plot(node_counts, medians, marker="o", color="#2980b9", label="Median Round Latency")
        ax.fill_between(node_counts, minimums, maximums, color="#2980b9", alpha=0.2, label="Min-Max Range")
        ax.set_xlabel("Number of Analyzer Nodes")
        ax.set_ylabel("Round Latency (Seconds)")
        ax.legend()

    return _plot_figure(plots_dir, "05_node_scaling", "scaling_sweep", draw)


def generate_plots(summary_path: str = "") -> list[str]:
    project_root = os.path.dirname(os.path.dirname(__file__))
    raw_output_dir = os.path.join(project_root, "raw_output")
    resolved_summary_path = summary_path if summary_path else _find_latest_summary(raw_output_dir)

    print(f"[*] Plotting data from: {os.path.basename(resolved_summary_path)}")
    results = _load_summary(resolved_summary_path)

    successful_results = [result for result in results if result["status"] == "Success"]
    if len(successful_results) == 0:
        print("[!] No successful benchmark results to plot. Skipping plot generation.")
        return []

    plots_dir = os.path.join(project_root, "plots")
    os.makedirs(plots_dir, exist_ok=True)

    scaling_results = [r for r in successful_results if r.get("benchmark") == "node_scaling"]
    other_results = [r for r in successful_results if r.get("benchmark") != "node_scaling"]

    saved_paths: list[str] = []

    # A multi-point scaling sweep is one combined plot; a lone scaling result falls
    # back to the standard per-benchmark latency plot.
    if len(scaling_results) > 1:
        saved_paths.append(_plot_node_scaling_sweep(scaling_results, plots_dir))
    else:
        other_results += scaling_results

    for benchmark_result in other_results:
        saved_paths.extend(_plot_single_benchmark(benchmark_result, plots_dir))

    for plot_path in saved_paths:
        print(f"[+] Plot saved to {plot_path}")
    print(f"[+] Generated {len(saved_paths)} benchmark plots.")
    return saved_paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate FLAME benchmark plots")
    parser.add_argument("--summary", default="", help="Path to a summary JSON file")
    args = parser.parse_args()
    generate_plots(summary_path=args.summary)


if __name__ == "__main__":
    main()
