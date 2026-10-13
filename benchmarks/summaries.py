import json
import os
import time
import uuid
from typing import Any
from benchmarks.measurements import experiment_statistics


def zero_timings(total_duration_seconds: float = 0.0) -> dict[str, float]:
    return {
        "total_duration_seconds": total_duration_seconds,
        "build_phase_seconds": 0.0,
        "distribution_phase_seconds": 0.0,
        "execution_phase_seconds": 0.0,
        "platform_runtime_seconds": 0.0,
        "internal_compute_seconds": 0.0,
        "orchestration_overhead_seconds": 0.0,
        "end_to_end_overhead_seconds": total_duration_seconds,
    }


def runner_side_result(
    benchmark: str,
    script: str,
    metrics: dict[str, Any],
    total_duration_seconds: float,
) -> dict[str, Any]:
    """Result envelope for benchmarks that run from the orchestrator rather than
    on the nodes (no analysis lifecycle, hence zeroed phase timings)."""
    return {
        "script": script,
        "benchmark": benchmark,
        "status": "Success",
        "result_file": "",
        "analysis_id": "",
        "node_count": 0,
        "node_labels": {},
        "combined_node_ids": [],
        "timings": zero_timings(total_duration_seconds),
        "federation_tax": 0.0,
        "benchmark_metrics": metrics,
    }


def write_summary(results: list[dict[str, Any]], raw_output_dir: str, prefix: str = "summary") -> str:
    os.makedirs(raw_output_dir, exist_ok=True)
    summary_file = os.path.join(raw_output_dir, f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:8]}.json")
    with open(summary_file, "w", encoding="utf-8") as summary_handle:
        json.dump(results, summary_handle, indent=2)
    if results and all("final_attempt" in result for result in results):
        with open(summary_file.replace(".json", ".statistics.json"), "w", encoding="utf-8") as handle:
            json.dump(experiment_statistics(results), handle, indent=2)
    print(f"\n[+] Summary saved to {summary_file}")
    return summary_file
