import time
import json
import os
from flame.star import StarModel, StarAnalyzer, StarAggregator

# Round 0 (size 0) is a warm-up with no throughput sample. Every other size is
# repeated REPEATS_PER_SIZE times in a row so each point in the size/throughput
# fit is an average over multiple samples instead of a single noisy reading.
BASE_SIZES_BYTES = [
    0,
    1_024,
    10_000,
    100_000,
    500_000,
    1_000_000,
    5_000_000,
    10_000_000,
    50_000_000,
    100_000_000,
]
REPEATS_PER_SIZE = 3
SIZES_BYTES = [BASE_SIZES_BYTES[0]] + [
    size for size in BASE_SIZES_BYTES[1:] for _ in range(REPEATS_PER_SIZE)
]
TOTAL_ROUNDS = len(SIZES_BYTES)


class LargeDataAnalyzer(StarAnalyzer):
    def analysis_method(self, data, aggregator_results):
        if not hasattr(self, "current_round"):
            self.current_round = 0

        payload_bytes = SIZES_BYTES[self.current_round]
        payload = os.urandom(payload_bytes // 2).hex()

        self.current_round += 1

        return {
            "success": True,
            "node_id": self.flame.get_id(),
            "payload": payload,
            "payload_bytes": payload_bytes,
        }


class LargeDataAggregator(StarAggregator):
    def aggregation_method(self, analysis_results):
        if not hasattr(self, "current_round"):
            self.current_round = 0
            self.last_mark = time.perf_counter()
            self.round_interval_seconds = []
            self.effective_application_throughput_mbps_samples = []
            self.per_node_throughput_mbps_samples = {}

        now = time.perf_counter()
        node_payload_sizes = {res["node_id"]: int(res["payload_bytes"]) for res in analysis_results}
        assert len(node_payload_sizes) > 0, "No large-data results received from nodes."
        total_size_bytes = sum(node_payload_sizes.values())

        if self.current_round > 0:
            round_interval_seconds = now - self.last_mark
            assert round_interval_seconds > 0.0, "Round interval must be positive."
            self.round_interval_seconds.append(round_interval_seconds)

            round_throughput_mbps = (total_size_bytes * 8) / (round_interval_seconds * 1_000_000)
            self.effective_application_throughput_mbps_samples.append(round_throughput_mbps)

            for node_id, size in node_payload_sizes.items():
                node_throughput = (size * 8) / (round_interval_seconds * 1_000_000)
                self.per_node_throughput_mbps_samples.setdefault(node_id, []).append(
                    node_throughput
                )

        self.current_round += 1
        self.last_mark = now

        return json.dumps(
            {
                "overall_success": True,
                "round": self.current_round,
                "total_rounds": TOTAL_ROUNDS,
                "sizes_bytes": SIZES_BYTES,
                "node_payload_sizes": node_payload_sizes,
                "total_size_bytes": total_size_bytes,
                "round_interval_seconds": self.round_interval_seconds,
                "effective_application_throughput_mbps_samples": self.effective_application_throughput_mbps_samples,
                "per_node_throughput_mbps_samples": self.per_node_throughput_mbps_samples,
                "timestamp": time.time(),
            }
        )

    def has_converged(self, result, last_result, num_iterations=None):
        if not hasattr(self, "current_round"):
            return False
        return self.current_round >= TOTAL_ROUNDS


def main():
    StarModel(
        analyzer=LargeDataAnalyzer,
        aggregator=LargeDataAggregator,
        data_type="none",
        output_type="str",
        simple_analysis=False,
    )


if __name__ == "__main__":
    main()
