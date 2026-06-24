import os
import time
import traceback
from typing import Any

from benchmarks.config import MAX_RETRIES, RAW_OUTPUT_DIR_NAME, SCALING_NODE_COUNTS
from benchmarks.execution import execute_benchmark
from benchmarks.hub_io import ensure_project
from benchmarks.node_resolution import resolve_scaling_nodes, resolve_target_nodes
from benchmarks.summaries import write_summary, zero_timings
from benchmarks.utils import authenticate_clients, benchmark_slug

BENCHMARKS_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(BENCHMARKS_DIR)


def _raw_output_dir() -> str:
    raw_output_dir = os.path.join(PROJECT_ROOT, RAW_OUTPUT_DIR_NAME)
    os.makedirs(raw_output_dir, exist_ok=True)
    return raw_output_dir


def _discover_benchmarks() -> list[str]:
    benchmark_files = [
        filename
        for filename in os.listdir(BENCHMARKS_DIR)
        if filename.endswith(".py") and filename[0].isdigit()
    ]
    benchmark_files.sort()
    return benchmark_files


def _select_benchmarks(all_benchmarks: list[str], benchmark_args: list[str]) -> list[str]:
    if "*" in benchmark_args:
        return all_benchmarks

    selected = []
    for benchmark_id in benchmark_args:
        prefix = f"{int(benchmark_id):02d}_"
        selected.extend([benchmark for benchmark in all_benchmarks if benchmark.startswith(prefix)])

    assert len(selected) > 0, "No benchmarks matched the selection."
    return selected


def _print_run_table(results: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 60)
    print(
        f"{'BENCHMARK':<30} | {'STATUS':<9} | {'PLATFORM':>9} | {'BUILD':>7} | {'COMPUTE':>8} | {'OVERHEAD':>8}"
    )
    print("-" * 60)
    for result in results:
        if result["status"] == "Success":
            platform_seconds = result["timings"]["platform_runtime_seconds"]
            build_seconds = result["timings"]["build_phase_seconds"]
            compute_seconds = result["timings"]["internal_compute_seconds"]
            overhead_seconds = result["timings"]["orchestration_overhead_seconds"]
            print(
                f"{result['script']:<30} | {result['status']:<9} | {platform_seconds:>8.2f}s | {build_seconds:>6.2f}s | {compute_seconds:>7.2f}s | {overhead_seconds:>7.2f}s"
            )
            print(f"  -> Results: {os.path.basename(result['result_file'])}")
        else:
            print(
                f"{result['script']:<30} | {result['status']:<9} | {'-':>9} | {'-':>7} | {'-':>8} | {'-':>8}"
            )
            print(f"  -> Error: {result['error_message']}")


def _failed_result(
    script_name: str,
    node_count: int,
    exception: Exception,
    node_labels: dict[str, str],
    combined_node_ids: list[str],
) -> dict[str, Any]:
    return {
        "script": script_name,
        "benchmark": benchmark_slug(script_name),
        "status": "Failed",
        "error_type": type(exception).__name__,
        "error_message": str(exception),
        "result_file": "",
        "analysis_id": "",
        "node_count": node_count,
        "node_labels": node_labels,
        "combined_node_ids": combined_node_ids,
        "timings": zero_timings(),
        "federation_tax": 0.0,
        "benchmark_metrics": {},
    }


def _run_one_with_retries(
    core_client: Any,
    storage_client: Any,
    project_id: str,
    node_ids: list[str],
    node_labels: dict[str, str],
    combined_node_ids: list[str],
    script_name: str,
    raw_output_dir: str,
) -> dict[str, Any]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return execute_benchmark(
                core_client=core_client,
                storage_client=storage_client,
                project_id=project_id,
                node_ids=node_ids,
                node_labels=node_labels,
                combined_node_ids=combined_node_ids,
                script_name=script_name,
                raw_output_dir=raw_output_dir,
            )
        except Exception as exception:
            print(
                f"    [!] Benchmark {script_name} failed with {type(exception).__name__}: "
                f"{exception} (Attempt {attempt}/{MAX_RETRIES})"
            )
            traceback.print_exc()
            if attempt >= MAX_RETRIES:
                return _failed_result(
                    script_name=script_name,
                    node_count=len(node_ids),
                    exception=exception,
                    node_labels=node_labels,
                    combined_node_ids=combined_node_ids,
                )
            print("    [*] Retrying benchmark...")
            time.sleep(5)


def run_benchmarks(benchmark_args: list[str]) -> str:
    raw_output_dir = _raw_output_dir()
    core_client, storage_client = authenticate_clients()
    project = ensure_project(core_client)
    node_ids, node_labels, combined_node_ids = resolve_target_nodes(
        core_client, project_id=str(project.id)
    )

    selected_benchmarks = _select_benchmarks(
        all_benchmarks=_discover_benchmarks(), benchmark_args=benchmark_args
    )
    print(f"[*] Selected benchmarks to run: {selected_benchmarks}")

    results = [
        _run_one_with_retries(
            core_client=core_client,
            storage_client=storage_client,
            project_id=str(project.id),
            node_ids=node_ids,
            node_labels=node_labels,
            combined_node_ids=combined_node_ids,
            script_name=benchmark_script,
            raw_output_dir=raw_output_dir,
        )
        for benchmark_script in selected_benchmarks
    ]

    _print_run_table(results)
    return write_summary(results, raw_output_dir)


def run_scaling_sweep() -> str:
    raw_output_dir = _raw_output_dir()
    core_client, storage_client = authenticate_clients()
    project = ensure_project(core_client)

    results: list[dict[str, Any]] = []
    for node_count in SCALING_NODE_COUNTS:
        node_ids, node_labels, combined_node_ids = resolve_scaling_nodes(
            core_client, project_id=str(project.id), node_count=node_count
        )
        results.append(
            _run_one_with_retries(
                core_client=core_client,
                storage_client=storage_client,
                project_id=str(project.id),
                node_ids=node_ids,
                node_labels=node_labels,
                combined_node_ids=combined_node_ids,
                script_name="05_node_scaling.py",
                raw_output_dir=raw_output_dir,
            )
        )

    _print_run_table(results)
    return write_summary(results, raw_output_dir, prefix="scaling_summary")
