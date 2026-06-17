import argparse
import glob
import json
import os
from collections.abc import Callable
from typing import Any

import matplotlib.pyplot as plt

CANONICAL_SCRIPT_BY_BENCHMARK = {
    "test_connection": "00_test_connection.py",
    "latency_overhead": "01_latency_overhead.py",
    "large_data_handling": "02_large_data_handling.py",
    "concurrent_scaling": "03_concurrent_scaling.py",
    "straggler_sensitivity": "04_straggler_sensitivity.py",
}


def _find_latest_summary(raw_output_dir: str) -> str:
    summary_files = glob.glob(os.path.join(raw_output_dir, "summary_*.json"))
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

    for node_id in ordered_node_ids:
        converted = [transform(float(value)) for value in samples_by_node[node_id]]
        if len(converted) == 0:
            continue
        datasets.append(converted)
        labels.append(node_labels.get(node_id, node_id))

    combined_sources = [node_id for node_id in combined_node_ids if node_id in samples_by_node]

    combined_values: list[float] = []
    for node_id in combined_sources:
        combined_values.extend(transform(float(value)) for value in samples_by_node[node_id])

    assert len(combined_values) > 0, "Combined distribution is empty."
    datasets.append(combined_values)
    labels.append("Combined")

    assert len(datasets) > 0, "No datasets available for plotting."

    return datasets, labels


def _plot_connection_metrics(ax, metrics: dict, node_labels: dict[str, str]) -> None:
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


def _plot_latency_metrics(
    ax, metrics: dict, node_labels: dict[str, str], combined_node_ids: list[str]
) -> None:
    per_node_samples = metrics.get("per_node_latency_samples_seconds", {})

    if per_node_samples:
        datasets, labels = _build_node_and_combined_datasets(
            samples_by_node={
                str(node_id): [float(value) for value in values]
                for node_id, values in per_node_samples.items()
            },
            node_labels=node_labels,
            combined_node_ids=combined_node_ids,
            transform=lambda value: value / 2.0,
        )

        box = ax.boxplot(datasets, patch_artist=True, tick_labels=labels)
        colors = ["#3498db", "#2980b9", "#1abc9c", "#16a085", "#2ecc71"]
        for index, patch in enumerate(box["boxes"]):
            patch.set_facecolor(colors[index % len(colors)])
    else:
        samples = metrics.get("effective_communication_latency_max_samples_seconds", [])
        if not samples:
            samples = metrics.get("effective_communication_latency_samples_seconds", [])

        sec_samples = [float(s) / 2.0 for s in samples]

        box = ax.boxplot([sec_samples], patch_artist=True, tick_labels=["Per-Communication"])
        colors = ["#3498db"]
        for index, patch in enumerate(box["boxes"]):
            patch.set_facecolor(colors[index % len(colors)])

    ax.set_ylabel("Seconds")
    ax.tick_params(axis="x", rotation=20)


def _plot_large_data_metrics(
    ax, metrics: dict, node_labels: dict[str, str], combined_node_ids: list[str]
) -> None:
    per_node_samples = metrics.get("per_node_throughput_mbps_samples", {})
    if per_node_samples:
        datasets, labels = _build_node_and_combined_datasets(
            samples_by_node={
                str(node_id): [float(value) for value in values]
                for node_id, values in per_node_samples.items()
            },
            node_labels=node_labels,
            combined_node_ids=combined_node_ids,
            transform=lambda value: value / 8.0,
        )
        box = ax.boxplot(datasets, patch_artist=True, tick_labels=labels)
        colors = ["#c0392b", "#e74c3c", "#d35400", "#e67e22", "#f39c12"]
        for index, patch in enumerate(box["boxes"]):
            patch.set_facecolor(colors[index % len(colors)])
    else:
        samples = metrics.get("effective_application_throughput_mbps_samples", [])
        if not samples:
            samples = metrics.get("throughput_mbps_samples", [])
        mbps = [float(s) / 8.0 for s in samples]

        box = ax.boxplot([mbps], patch_artist=True, tick_labels=["Combined"])
        colors = ["#c0392b"]
        for index, patch in enumerate(box["boxes"]):
            patch.set_facecolor(colors[index % len(colors)])

    ax.set_ylabel("MB/s")
    ax.tick_params(axis="x", rotation=20)


def _plot_straggler_metrics(
    ax, metrics: dict, node_labels: dict[str, str], combined_node_ids: list[str]
) -> None:
    per_node_samples = metrics["per_node_compute_samples_seconds"]
    datasets, labels = _build_node_and_combined_datasets(
        samples_by_node={
            str(node_id): [float(value) for value in values]
            for node_id, values in per_node_samples.items()
        },
        node_labels=node_labels,
        combined_node_ids=combined_node_ids,
        transform=lambda value: value * 1000.0,
    )

    box = ax.boxplot(datasets, patch_artist=True, tick_labels=labels)
    colors = ["#16a085", "#e67e22", "#2980b9", "#d35400", "#2c3e50"]
    for index, patch in enumerate(box["boxes"]):
        patch.set_facecolor(colors[index % len(colors)])

    ax.set_ylabel("Milliseconds")
    ax.tick_params(axis="x", rotation=20)


def _plot_concurrent_scaling_metrics(ax, metrics: dict) -> None:
    round_intervals = metrics.get("round_interval_seconds", [])
    moving_averages = metrics.get("moving_average_intervals", [])

    ax.plot(
        round_intervals,
        marker="o",
        color="#ecf0f1",
        linestyle="None",
        alpha=0.5,
        label="Round Interval",
    )

    if moving_averages:
        # moving_average_intervals is the same length as round_intervals minus first few (actually wait, my 06_script appends to moving_averages only when current_round > 0, so length is len(round_intervals))
        # Let's just plot moving average directly since we appended to it every round > 0
        ax.plot(moving_averages, color="#e74c3c", linewidth=2.5, label="10-Round Moving Avg")

    ax.set_ylabel("Round Interval (Seconds)")
    ax.set_xlabel("Round Number")
    ax.legend()


def _plot_single_benchmark(result: dict, plots_dir: str) -> str:
    script_name = _canonical_script_name(result)
    benchmark_key = str(result.get("benchmark", ""))
    benchmark_metrics = result["benchmark_metrics"]
    node_labels = {str(key): str(value) for key, value in result.get("node_labels", {}).items()}
    combined_node_ids = [str(node_id) for node_id in result.get("combined_node_ids", [])]

    figure, ax = plt.subplots(1, 1, figsize=(8, 5))

    if benchmark_key == "test_connection" and "node_results" in benchmark_metrics:
        _plot_connection_metrics(ax, benchmark_metrics, node_labels=node_labels)
    elif benchmark_key == "latency_overhead":
        _plot_latency_metrics(
            ax,
            benchmark_metrics,
            node_labels=node_labels,
            combined_node_ids=combined_node_ids,
        )
    elif benchmark_key == "large_data_handling":
        _plot_large_data_metrics(
            ax,
            benchmark_metrics,
            node_labels=node_labels,
            combined_node_ids=combined_node_ids,
        )
    elif benchmark_key == "straggler_sensitivity":
        _plot_straggler_metrics(
            ax,
            benchmark_metrics,
            node_labels=node_labels,
            combined_node_ids=combined_node_ids,
        )
    elif benchmark_key == "concurrent_scaling":
        _plot_concurrent_scaling_metrics(ax, benchmark_metrics)
    else:
        assert False, f"Unsupported benchmark plot routing for key: {benchmark_key}"

    figure.tight_layout()
    base_name = script_name.replace(".py", "")
    output_path = os.path.join(plots_dir, f"{base_name}.svg")
    png_path = os.path.join(plots_dir, f"{base_name}.png")
    figure.savefig(output_path, dpi=150, format="svg")
    figure.savefig(png_path, dpi=150, format="png")
    plt.close(figure)
    return output_path


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

    saved_paths = []
    for benchmark_result in successful_results:
        plot_path = _plot_single_benchmark(benchmark_result, plots_dir)
        saved_paths.append(plot_path)
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
