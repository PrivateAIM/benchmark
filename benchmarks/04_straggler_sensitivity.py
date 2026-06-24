import hashlib
import json
import time

from flame.star import StarAggregator, StarAnalyzer, StarModel

TOTAL_ROUNDS = 50
MAX_SLEEP_SECONDS = 0.25


def _deterministic_sleep_seconds(node_id: str, round_index: int) -> float:
    seed = f"{node_id}:{round_index}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 1000
    return (bucket / 1000.0) * MAX_SLEEP_SECONDS


class StragglerAnalyzer(StarAnalyzer):
    def analysis_method(self, data, aggregator_results):
        if not hasattr(self, "current_round"):
            self.current_round = 0

        assert self.current_round < TOTAL_ROUNDS, "Analyzer round exceeded configured total rounds."
        node_id = self.flame.get_id()
        planned_sleep_seconds = _deterministic_sleep_seconds(
            node_id=node_id, round_index=self.current_round
        )

        start = time.perf_counter()
        time.sleep(planned_sleep_seconds)
        end = time.perf_counter()

        self.current_round += 1

        return {
            "success": True,
            "node_id": node_id,
            "planned_sleep_seconds": planned_sleep_seconds,
            "compute_time_seconds": end - start,
        }


class StragglerAggregator(StarAggregator):
    def aggregation_method(self, analysis_results):
        if not hasattr(self, "current_round"):
            self.current_round = 0
            self.last_mark = time.perf_counter()
            self.round_interval_seconds = []
            self.compute_spread_seconds = []
            self.max_compute_seconds = []
            self.min_compute_seconds = []
            self.compute_time_samples = []
            self.per_node_compute_samples_seconds = {}

        now = time.perf_counter()
        if self.current_round > 0:
            round_interval_seconds = now - self.last_mark
            assert round_interval_seconds > 0.0, "Round interval must be positive."
            self.round_interval_seconds.append(round_interval_seconds)

        assert len(analysis_results) > 0, "No straggler samples received from nodes."
        compute_times = [float(result["compute_time_seconds"]) for result in analysis_results]
        planned_sleep_times = [
            float(result["planned_sleep_seconds"]) for result in analysis_results
        ]

        self.compute_time_samples.extend(compute_times)
        for result in analysis_results:
            node_id = str(result["node_id"])
            node_compute_time = float(result["compute_time_seconds"])
            if node_id not in self.per_node_compute_samples_seconds:
                self.per_node_compute_samples_seconds[node_id] = []
            self.per_node_compute_samples_seconds[node_id].append(node_compute_time)

        round_max = max(compute_times)
        round_min = min(compute_times)
        spread = round_max - round_min

        self.max_compute_seconds.append(round_max)
        self.min_compute_seconds.append(round_min)
        self.compute_spread_seconds.append(spread)

        if not hasattr(self, "total_critical_compute_time_seconds"):
            self.total_critical_compute_time_seconds = 0.0
        self.total_critical_compute_time_seconds += round_max

        self.current_round += 1
        self.last_mark = now

        return json.dumps(
            {
                "overall_success": True,
                "round": self.current_round,
                "total_rounds": TOTAL_ROUNDS,
                "round_interval_seconds": self.round_interval_seconds,
                "compute_spread_seconds": self.compute_spread_seconds,
                "max_compute_seconds": self.max_compute_seconds,
                "min_compute_seconds": self.min_compute_seconds,
                "per_node_compute_samples_seconds": self.per_node_compute_samples_seconds,
                "planned_sleep_seconds": planned_sleep_times,
                "average_compute_time": sum(self.compute_time_samples)
                / len(self.compute_time_samples),
                "total_critical_compute_time_seconds": self.total_critical_compute_time_seconds,
                "timestamp": time.time(),
            }
        )

    def has_converged(self, result, last_result, num_iterations=None):
        if not hasattr(self, "current_round"):
            return False
        return self.current_round >= TOTAL_ROUNDS


def main():
    StarModel(
        analyzer=StragglerAnalyzer,
        aggregator=StragglerAggregator,
        data_type="none",
        output_type="str",
        simple_analysis=False,
    )


if __name__ == "__main__":
    main()
