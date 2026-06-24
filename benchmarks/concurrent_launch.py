import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from benchmarks.config import (
    CONCURRENCY_LEVELS,
    CONCURRENT_LAUNCH_SCRIPT,
    RAW_OUTPUT_DIR_NAME,
)
from benchmarks.execution import execute_benchmark
from benchmarks.hub_io import ensure_project
from benchmarks.node_resolution import resolve_target_nodes
from benchmarks.summaries import runner_side_result, write_summary
from benchmarks.utils import authenticate_clients

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))


def _launch_one(
    project_id: str,
    node_ids: list[str],
    node_labels: dict[str, str],
    combined_node_ids: list[str],
    raw_output_dir: str,
) -> dict[str, float]:
    # Each concurrent launch gets its own authenticated clients: httpx clients are
    # not guaranteed safe to share across threads, and a shared auth token can race.
    core_client, storage_client = authenticate_clients()
    result = execute_benchmark(
        core_client=core_client,
        storage_client=storage_client,
        project_id=project_id,
        node_ids=node_ids,
        node_labels=node_labels,
        combined_node_ids=combined_node_ids,
        script_name=CONCURRENT_LAUNCH_SCRIPT,
        raw_output_dir=raw_output_dir,
    )
    timings = result["timings"]
    return {
        "build_seconds": timings["build_phase_seconds"],
        "distribution_seconds": timings["distribution_phase_seconds"],
        "total_seconds": timings["total_duration_seconds"],
    }


def _run_level(
    level: int,
    project_id: str,
    node_ids: list[str],
    node_labels: dict[str, str],
    combined_node_ids: list[str],
    raw_output_dir: str,
) -> list[dict[str, float]]:
    print(f"\n[>>>] CONCURRENCY LEVEL: launching {level} analyses at once")
    with ThreadPoolExecutor(max_workers=level) as executor:
        futures = [
            executor.submit(
                _launch_one,
                project_id,
                node_ids,
                node_labels,
                combined_node_ids,
                raw_output_dir,
            )
            for _ in range(level)
        ]
        # Surface any worker exception by accessing .result() (fail-fast).
        return [future.result() for future in futures]


def run_concurrent_launch() -> str:
    raw_output_dir = os.path.join(PROJECT_ROOT, RAW_OUTPUT_DIR_NAME)
    os.makedirs(raw_output_dir, exist_ok=True)

    core_client, _ = authenticate_clients()
    project = ensure_project(core_client)
    node_ids, node_labels, combined_node_ids = resolve_target_nodes(
        core_client, project_id=str(project.id)
    )

    build_seconds_by_level: dict[str, list[float]] = {}
    distribution_seconds_by_level: dict[str, list[float]] = {}
    total_seconds_by_level: dict[str, list[float]] = {}

    started_at = time.time()
    for level in CONCURRENCY_LEVELS:
        per_analysis = _run_level(
            level=level,
            project_id=str(project.id),
            node_ids=node_ids,
            node_labels=node_labels,
            combined_node_ids=combined_node_ids,
            raw_output_dir=raw_output_dir,
        )
        build_seconds_by_level[str(level)] = [item["build_seconds"] for item in per_analysis]
        distribution_seconds_by_level[str(level)] = [
            item["distribution_seconds"] for item in per_analysis
        ]
        total_seconds_by_level[str(level)] = [item["total_seconds"] for item in per_analysis]
        print(
            f"[+] Level {level}: build={build_seconds_by_level[str(level)]} "
            f"dist={distribution_seconds_by_level[str(level)]}"
        )

    total_duration_seconds = time.time() - started_at

    metrics: dict[str, Any] = {
        "overall_success": True,
        "concurrency_levels": CONCURRENCY_LEVELS,
        "launch_script": CONCURRENT_LAUNCH_SCRIPT,
        "build_seconds_by_level": build_seconds_by_level,
        "distribution_seconds_by_level": distribution_seconds_by_level,
        "total_seconds_by_level": total_seconds_by_level,
        "timestamp": time.time(),
    }
    result = runner_side_result(
        benchmark="concurrent_launch",
        script="concurrent_launch.py",
        metrics=metrics,
        total_duration_seconds=total_duration_seconds,
    )
    return write_summary([result], raw_output_dir, prefix="concurrent_summary")
