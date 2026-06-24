import time
import json
from flamesdk import FlameCoreSDK

TOTAL_ROUNDS = 50


def _run_aggregator(flame: FlameCoreSDK) -> None:
    analyzer_ids = flame.get_participant_ids()
    ready_check_result = flame.ready_check(list(analyzer_ids))
    assert all(ready_check_result.values()), f"Not all analyzers reachable: {ready_check_result}"

    current_round = 0
    round_mark = time.perf_counter()
    round_interval_seconds: list[float] = []
    effective_communication_latency_max_seconds: list[float] = []
    per_node_latency_samples_seconds: dict[str, list[float]] = {}

    while current_round < TOTAL_ROUNDS:
        results = flame.await_intermediate_data(analyzer_ids)
        now = time.perf_counter()

        if current_round > 0:
            interval = now - round_mark
            round_interval_seconds.append(interval)
            effective_communication_latency_max_seconds.append(interval)

        for node_id, payload in results.items():
            assert payload["round_interval"] >= 0.0, "Negative round interval reported by analyzer."
            if payload["round_interval"] > 0:
                per_node_latency_samples_seconds.setdefault(str(node_id), []).append(
                    float(payload["round_interval"])
                )

        current_round += 1
        round_mark = now

        if current_round < TOTAL_ROUNDS:
            flame.send_intermediate_data(analyzer_ids, {"round": current_round})

    flame.submit_final_result(
        json.dumps(
            {
                "overall_success": True,
                "round": current_round,
                "total_rounds": TOTAL_ROUNDS,
                "round_interval_seconds": round_interval_seconds,
                "effective_communication_latency_max_samples_seconds": effective_communication_latency_max_seconds,
                "per_node_latency_samples_seconds": per_node_latency_samples_seconds,
                "timestamp": time.time(),
            }
        ),
        output_type="str",
    )
    flame.analysis_finished()


def _run_analyzer(flame: FlameCoreSDK) -> None:
    aggregator_id = flame.get_aggregator_id()
    ready_check_result = flame.ready_check([aggregator_id])
    assert ready_check_result[aggregator_id], "Could not contact aggregator"

    current_round = 0
    last_mark = time.perf_counter()

    while current_round < TOTAL_ROUNDS:
        now = time.perf_counter()
        round_interval = 0.0 if current_round == 0 else now - last_mark
        last_mark = now

        flame.send_intermediate_data(
            [aggregator_id],
            {"node_id": flame.get_id(), "round_interval": round_interval},
        )

        current_round += 1
        if current_round < TOTAL_ROUNDS and not flame.config.finished:
            flame.await_intermediate_data([aggregator_id])


def main() -> None:
    flame = FlameCoreSDK()
    flame.ready_check()

    if flame.get_role() == "aggregator":
        _run_aggregator(flame)
    elif flame.get_role() == "default":
        _run_analyzer(flame)
    else:
        raise ValueError(f"Fatal: unknown node role '{flame.get_role()}'.")


if __name__ == "__main__":
    main()
