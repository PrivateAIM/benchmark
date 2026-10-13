import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import time

ITERATIONS = 200_000
REPEATS = 5


def run_workload() -> dict:
    # Fixed inputs and one untimed warm-up, identical locally and on every node.
    expected = hashlib.pbkdf2_hmac("sha256", b"FLAME benchmark", b"fixed salt", ITERATIONS).hex()
    samples = []
    for _ in range(REPEATS):
        start = time.perf_counter()
        digest = hashlib.pbkdf2_hmac("sha256", b"FLAME benchmark", b"fixed salt", ITERATIONS).hex()
        samples.append(time.perf_counter() - start)
        assert digest == expected
    packages = {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}
    return {
        "digest": expected, "iterations": ITERATIONS, "repeats": REPEATS,
        "compute_samples_seconds": samples, "compute_total_seconds": sum(samples),
        "runtime": {"python": platform.python_version(), "platform": platform.platform(),
                    "cpu_count": os.cpu_count(), "packages": packages},
    }


def main() -> None:
    flame = importlib.import_module("flamesdk").FlameCoreSDK()
    flame.ready_check()
    if flame.get_role() == "default":
        result = run_workload()
        flame.send_intermediate_data([flame.get_aggregator_id()], result)
        flame.await_intermediate_data([flame.get_aggregator_id()])
        flame.analysis_finished()
    elif flame.get_role() == "aggregator":
        ids = flame.get_participant_ids()
        results = flame.await_intermediate_data(ids)
        assert set(results) == set(ids), "Missing compute results."
        assert len({result["digest"] for result in results.values()}) == 1
        payload = {
            "overall_success": True, "per_node_compute": results,
            "total_critical_compute_time_seconds": max(
                result["compute_total_seconds"] for result in results.values()
            ),
        }
        flame.submit_final_result(json.dumps(payload), output_type="str")
        flame.send_intermediate_data(ids, {"done": True})
        flame.analysis_finished()
    else:
        raise ValueError(f"Unsupported role: {flame.get_role()}")


if __name__ == "__main__":
    main()
