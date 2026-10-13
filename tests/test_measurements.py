import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import httpx2

from analysis.plot_results import (
    _find_latest_summary, _plot_large_data_size_fit, _plot_latency_metrics,
    _plot_node_scaling_sweep, generate_plots,
)
from benchmarks.encryption_benchmark import encryption_samples, PLAINTEXT_CHUNK_BYTES
from benchmarks.execution import execute_benchmark, _derive_runtime_metrics
from benchmarks.measurements import experiment_statistics, sample_statistics
from benchmarks.runner import _run_one_with_retries
from benchmarks.utils import call_with_retries, HTTP_RETRY_EVENTS

ROOT = Path(__file__).resolve().parents[1]


class MeasurementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "raw_output").mkdir(exist_ok=True)
    def test_size_fit_uses_measured_intervals_not_aggregate_throughput(self):
        fig, ax = plt.subplots()
        metrics = {"sizes_bytes": [0, 100, 200], "round_interval_seconds": [10, 20],
                   "effective_application_throughput_mbps_samples": [0.0004, 0.0004]}
        _plot_large_data_size_fit(ax, metrics)
        self.assertEqual(ax.collections[0].get_offsets()[:, 1].tolist(), [10, 20])
        plt.close(fig)

    def test_latency_not_halved(self):
        fig, ax = plt.subplots()
        _plot_latency_metrics(ax, {"effective_communication_latency_max_samples_seconds": [8, 10]}, {}, [])
        self.assertEqual(ax.lines[4].get_ydata().tolist(), [9, 9])
        self.assertIn("Round-trip", ax.get_ylabel())
        plt.close(fig)

    def test_retry_and_independent_runs_are_distinct(self):
        rows = []
        for repeat, attempt, final, status, seconds in [
            (1, 1, False, "Failed", 2), (1, 2, True, "Success", 10),
            (2, 1, True, "Success", 20), (3, 1, True, "Failed", 5),
        ]:
            rows.append({"benchmark": "latency_overhead", "node_count": 6,
                         "repetition": repeat, "attempt": attempt, "final_attempt": final,
                         "status": status, "timings": {"total_duration_seconds": seconds},
                         "benchmark_metrics": {}})
        result = experiment_statistics(rows)[0]
        self.assertEqual(result["independent_runs"], 3)
        self.assertEqual(result["failed_attempts"], 2)
        self.assertEqual(result["failed_runs"], 1)
        self.assertEqual(result["total_duration_seconds"]["count"], 2)
        self.assertEqual(result["total_duration_seconds"]["median"], 15)

    def test_partial_phase_durations_are_unknown_and_steady_state_separate(self):
        timestamps = {"build_started_at": 0, "build_executed_at": 2,
                      "distribution_started_at": 2, "distribution_executed_at": 5,
                      "execution_started_at": 5, "execution_executed_at": 15}
        timings, _ = _derive_runtime_metrics(
            {"round_interval_seconds": [3, 3], "steady_state_critical_compute_seconds": 2,
             "total_critical_compute_time_seconds": 3}, timestamps, 20)
        self.assertEqual(timings["steady_state_seconds"], 6)
        self.assertEqual(timings["startup_and_completion_seconds"], 4)
        self.assertEqual(timings["steady_state_overhead_seconds"], 4)
        timestamps["execution_executed_at"] = None
        timings, overhead = _derive_runtime_metrics({}, timestamps, 20)
        self.assertIsNone(timings["execution_phase_seconds"])
        self.assertIsNone(overhead)

    def test_failed_attempt_preserves_remote_identity_and_phase(self):
        def fail(*args, progress, **kwargs):
            progress.update({"analysis_id": "existing-id", "phase": "distribute",
                             "phase_timestamps": dict.fromkeys([
                                 "build_started_at", "build_executed_at", "distribution_started_at",
                                 "distribution_executed_at", "execution_started_at", "execution_executed_at"
                             ])})
            raise httpx2.ReadTimeout("poll timeout")
        with patch("benchmarks.runner.execute_benchmark", side_effect=fail):
            rows = _run_one_with_retries(None, None, "project", ["n"], {"n": "node"}, ["n"], "00_test_connection.py", ".")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["analysis_id"], "existing-id")
        self.assertEqual(rows[0]["failed_phase"], "distribute")
        self.assertGreater(rows[0]["timings"]["total_duration_seconds"], 0)

    def test_programmer_invariants_are_not_silently_retried(self):
        with patch("benchmarks.runner.execute_benchmark", side_effect=AssertionError("bad metric")):
            with self.assertRaises(AssertionError):
                _run_one_with_retries(None, None, "p", [], {}, [], "01_latency_overhead.py", ".")

    def test_sdk_transport_retries_are_counted(self):
        operation = Mock(side_effect=[httpx2.ReadTimeout("temporary"), 3])
        operation.__qualname__ = "poll"
        events = []
        token = HTTP_RETRY_EVENTS.set(events)
        try:
            self.assertEqual(call_with_retries(operation, max_attempts=2, backoff_seconds=0), 3)
            self.assertEqual(len(events), 1)
        finally:
            HTTP_RETRY_EVENTS.reset(token)

    def test_crypto_chunk_boundary_and_integrity(self):
        result = encryption_samples(PLAINTEXT_CHUNK_BYTES + 1, 2)
        self.assertEqual(result["chunks"], 2)
        self.assertEqual(result["encrypted_bytes"], result["size_bytes"] + 56)
        self.assertEqual(len(result["encrypt_seconds"]), 2)

    def test_statistics_sidecar_is_not_loaded_as_raw_summary(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "raw_output") as folder:
            raw = Path(folder) / "summary_1.json"
            raw.write_text("[]")
            (Path(folder) / "summary_1.statistics.json").write_text("[]")
            self.assertEqual(_find_latest_summary(folder), str(raw))

    def test_failed_only_runs_still_generate_reliability_plots(self):
        row = {"benchmark": "latency_overhead", "node_count": 6, "repetition": 1,
               "attempt": 1, "final_attempt": True, "status": "Failed",
               "timings": {"total_duration_seconds": 12}, "benchmark_metrics": {}}
        with tempfile.TemporaryDirectory(dir=ROOT / "raw_output") as folder:
            summary = Path(folder) / "summary_test.json"
            summary.write_text(json.dumps([row]))
            with patch("analysis.plot_results._save_figure", side_effect=lambda fig, *args: (plt.close(fig), args[-1])[1]):
                plots = generate_plots(str(summary))
            self.assertIn("reliability", plots)

    def test_compute_reference_is_deterministic(self):
        spec = importlib.util.spec_from_file_location("compute", ROOT / "benchmarks/07_compute_baseline.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.ITERATIONS = 10
        module.REPEATS = 2
        first, second = module.run_workload(), module.run_workload()
        self.assertEqual(first["digest"], second["digest"])
        self.assertEqual(len(first["compute_samples_seconds"]), 2)

    def test_mocked_lifecycle_records_phases_and_checks_participants(self):
        core = Mock()
        core.create_analysis.return_value = SimpleNamespace(id="test-analysis")
        def state(build, distribution, execution):
            return SimpleNamespace(id="test-analysis", build_status=build, distribution_status=distribution,
                                   execution_status=execution, build_hash="image-hash")
        core.get_analysis.side_effect = [state(None, None, None), state("executed", None, None),
                                        state("executed", "executed", "executing"),
                                        state("executed", "executed", "executed")]
        with tempfile.TemporaryDirectory(dir=ROOT / "raw_output") as folder:
            with patch("benchmarks.execution.resolve_master_image_id", return_value="python-base"), \
                 patch("benchmarks.execution.upload_benchmark_code"), \
                 patch("benchmarks.execution.download_result_tar", return_value="result.tar"), \
                 patch("benchmarks.execution.extract_result_payload", return_value={
                     "overall_success": True, "node_results": {"analyzer": "ok"}}), \
                 patch("benchmarks.execution.time.sleep"):
                result = execute_benchmark(core, Mock(), "project", ["aggregator", "analyzer"],
                                           {}, ["analyzer"], "00_test_connection.py", folder)
            self.assertEqual(result["status"], "Success")
            self.assertEqual(result["execution_record"]["image_build_hash"], "image-hash")
            self.assertEqual(result["execution_record"]["phase"], "finished")
            self.assertIsNotNone(result["timings"]["execution_phase_seconds"])
            self.assertIsNone(result["timings"]["internal_compute_seconds"])
            self.assertTrue((Path(folder) / "progress_test-analysis.json").exists())

    def test_compute_aggregator_waits_for_all_nodes_and_acknowledges(self):
        spec = importlib.util.spec_from_file_location("compute", ROOT / "benchmarks/07_compute_baseline.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sdk = Mock()
        sdk.get_role.return_value = "aggregator"
        sdk.get_participant_ids.return_value = ["a", "b"]
        sdk.await_intermediate_data.return_value = {
            "a": {"digest": "same", "compute_total_seconds": 2},
            "b": {"digest": "same", "compute_total_seconds": 3},
        }
        with patch.object(module.importlib, "import_module", return_value=SimpleNamespace(FlameCoreSDK=lambda: sdk)):
            module.main()
        payload = json.loads(sdk.submit_final_result.call_args.args[0])
        self.assertEqual(payload["total_critical_compute_time_seconds"], 3)
        sdk.send_intermediate_data.assert_called_once_with(["a", "b"], {"done": True})

    def test_concurrent_batch_samples_are_not_pseudo_replicates(self):
        rows = []
        for repetition in (1, 2):
            for duration in (10, 20, 30):
                rows.append({"benchmark": "concurrent_launch", "node_count": 6,
                             "sweep_parameter": 3, "repetition": repetition, "final_attempt": True,
                             "status": "Success", "timings": {"total_duration_seconds": duration},
                             "benchmark_metrics": {}})
        stats = experiment_statistics(rows)[0]
        self.assertEqual(stats["analysis_runs"], 6)
        self.assertEqual(stats["independent_runs"], 2)
        self.assertEqual(stats["total_duration_seconds"]["count"], 2)
        self.assertEqual(stats["total_duration_seconds"]["median"], 20)

    def test_scaling_retains_full_intervals_and_aggregates_run_medians(self):
        results = [{"combined_node_ids": ["a"], "benchmark_metrics": {"round_interval_seconds": [8, 10]}},
                   {"combined_node_ids": ["a"], "benchmark_metrics": {"round_interval_seconds": [10, 12]}}]
        def capture(fig, *args):
            self.assertEqual(fig.axes[0].lines[0].get_ydata().tolist(), [10])
            plt.close(fig)
            return "scaling"
        with patch("analysis.plot_results._save_figure", side_effect=capture):
            _plot_node_scaling_sweep(results, ".")


if __name__ == "__main__":
    unittest.main()
