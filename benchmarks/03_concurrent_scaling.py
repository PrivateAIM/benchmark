import time
import json
import os
from flame.star import StarModel, StarAnalyzer, StarAggregator


# Keeping payload very small to isolate scheduler/contention overhead rather than network bounds
PAYLOAD_BYTES = 1024  # 1KB
TOTAL_ROUNDS = 100   # Rapid rounds to test broker/aggregator over time


class ConcurrentScalingAnalyzer(StarAnalyzer):
    def analysis_method(self, data, aggregator_results):
        if not hasattr(self, "cached_payload"):
            self.cached_payload = os.urandom(PAYLOAD_BYTES // 2).hex()
        payload = self.cached_payload

        return {
            "success": True,
            "node_id": self.flame.get_id(),
            "payload": payload,
        }


class ConcurrentScalingAggregator(StarAggregator):
    def aggregation_method(self, analysis_results):
        if not hasattr(self, "current_round"):
            self.current_round = 0
            self.last_mark = time.perf_counter()
            self.round_interval_seconds = []
            self.moving_average_intervals = []

        now = time.perf_counter()

        if self.current_round > 0:
            interval = now - self.last_mark
            assert interval > 0.0, "Round interval must be positive."
            self.round_interval_seconds.append(interval)
            
            # Keep a window of 10 to see if it degrades (increases) over time
            window_size = 10
            recent_intervals = self.round_interval_seconds[-window_size:]
            self.moving_average_intervals.append(sum(recent_intervals) / len(recent_intervals))

        self.current_round += 1
        self.last_mark = now

        return json.dumps(
            {
                "overall_success": True,
                "round": self.current_round,
                "total_rounds": TOTAL_ROUNDS,
                "payload_bytes": PAYLOAD_BYTES,
                "round_interval_seconds": self.round_interval_seconds,
                "moving_average_intervals": self.moving_average_intervals,
                "nodes_responded": len(analysis_results),
                "timestamp": time.time(),
            }
        )

    def has_converged(self, result, last_result, num_iterations=None):
        if not hasattr(self, "current_round"):
            return False
        return self.current_round >= TOTAL_ROUNDS


def main():
    StarModel(
        analyzer=ConcurrentScalingAnalyzer,
        aggregator=ConcurrentScalingAggregator,
        data_type="none",
        output_type="str",
        simple_analysis=False,
    )


if __name__ == "__main__":
    main()
