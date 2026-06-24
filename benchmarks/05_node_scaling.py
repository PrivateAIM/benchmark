import time
import json
from flame.star import StarModel, StarAnalyzer, StarAggregator

TOTAL_ROUNDS = 50


class NodeScalingAnalyzer(StarAnalyzer):
    def analysis_method(self, data, aggregator_results):
        if not hasattr(self, "current_round"):
            self.current_round = 0
            self.last_mark = time.perf_counter()

        now = time.perf_counter()
        round_interval = 0.0
        if self.current_round > 0:
            round_interval = now - self.last_mark
        self.last_mark = now

        assert self.current_round < TOTAL_ROUNDS, "Analyzer round exceeded configured total rounds."

        self.current_round += 1

        return {
            "success": True,
            "node_id": self.flame.get_id(),
            "round_interval": round_interval,
        }


class NodeScalingAggregator(StarAggregator):
    def aggregation_method(self, analysis_results):
        if not hasattr(self, "current_round"):
            self.current_round = 0
            self.round_mark = time.perf_counter()
            self.round_interval_seconds = []
            self.effective_communication_latency_max_seconds = []
            self.per_node_latency_samples_seconds = {}

        now = time.perf_counter()
        round_interval = 0.0
        if self.current_round > 0:
            round_interval = now - self.round_mark
            self.round_interval_seconds.append(round_interval)
            self.effective_communication_latency_max_seconds.append(round_interval)

        for res in analysis_results:
            node_id = str(res["node_id"])
            if "round_interval" in res and res["round_interval"] > 0:
                if node_id not in self.per_node_latency_samples_seconds:
                    self.per_node_latency_samples_seconds[node_id] = []
                self.per_node_latency_samples_seconds[node_id].append(float(res["round_interval"]))

        self.current_round += 1
        self.round_mark = now

        return json.dumps(
            {
                "overall_success": True,
                "round": self.current_round,
                "total_rounds": TOTAL_ROUNDS,
                "node_count": len(analysis_results),
                "round_interval_seconds": self.round_interval_seconds,
                "effective_communication_latency_max_samples_seconds": self.effective_communication_latency_max_seconds,
                "per_node_latency_samples_seconds": self.per_node_latency_samples_seconds,
                "timestamp": time.time(),
            }
        )

    def has_converged(self, result, last_result, num_iterations=None):
        if not hasattr(self, "current_round"):
            return False
        return self.current_round >= TOTAL_ROUNDS


def main():
    StarModel(
        analyzer=NodeScalingAnalyzer,
        aggregator=NodeScalingAggregator,
        data_type="none",  # pyright: ignore[reportArgumentType]
        output_type="str",
        simple_analysis=False,
    )


if __name__ == "__main__":
    main()
