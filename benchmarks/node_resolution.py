import flame_hub

from benchmarks.config import (
    SCALING_AGGREGATOR_NODE_NAME,
    SCALING_NODE_NAME_PREFIX,
    TARGET_NODE_NAMES,
)


def is_aggregator_node_name(node_name: str) -> bool:
    return node_name.lower().startswith("aggregator")


def register_nodes_to_project(
    core_client: flame_hub.CoreClient, project_id: str, node_ids: list[str]
) -> None:
    existing_project_nodes = core_client.find_project_nodes(
        filter={"project_id": str(project_id)}
    )
    existing_node_ids = [str(project_node.node_id) for project_node in existing_project_nodes]

    for node_id in node_ids:
        if str(node_id) not in existing_node_ids:
            core_client.create_project_node(project_id=project_id, node_id=node_id)
            print(f"[+] Added node {node_id} to project")


def _node_ids_and_labels(
    selected_nodes: list,
) -> tuple[list[str], dict[str, str], list[str]]:
    node_ids = [str(node.id) for node in selected_nodes]
    node_labels = {str(node.id): str(node.name) for node in selected_nodes}
    combined_node_ids = [
        str(node.id) for node in selected_nodes if not is_aggregator_node_name(str(node.name))
    ]
    return node_ids, node_labels, combined_node_ids


def resolve_target_nodes(
    core_client: flame_hub.CoreClient, project_id: str
) -> tuple[list[str], dict[str, str], list[str]]:
    nodes = core_client.get_nodes()
    selected_nodes = [node for node in nodes if node.name in TARGET_NODE_NAMES]
    assert len(selected_nodes) > 0, f"No nodes matching {TARGET_NODE_NAMES} found."

    node_ids, node_labels, combined_node_ids = _node_ids_and_labels(selected_nodes)
    if len(combined_node_ids) == 0:
        combined_node_ids = node_ids.copy()

    print(f"[+] Using {len(node_ids)} nodes: {[node.name for node in selected_nodes]}")

    register_nodes_to_project(core_client, project_id=project_id, node_ids=node_ids)

    return node_ids, node_labels, combined_node_ids


def resolve_scaling_nodes(
    core_client: flame_hub.CoreClient, project_id: str, node_count: int
) -> tuple[list[str], dict[str, str], list[str]]:
    expected_names = [SCALING_AGGREGATOR_NODE_NAME] + [
        f"{SCALING_NODE_NAME_PREFIX}{index}" for index in range(1, node_count + 1)
    ]

    nodes = core_client.get_nodes()
    nodes_by_name = {str(node.name): node for node in nodes}

    missing_names = [name for name in expected_names if name not in nodes_by_name]
    assert len(missing_names) == 0, (
        f"Scaling sweep requested {node_count} node(s) but the following are not registered "
        f"on the hub: {missing_names}. Reduce SCALING_NODE_COUNTS or register these nodes."
    )

    selected_nodes = [nodes_by_name[name] for name in expected_names]
    node_ids, node_labels, combined_node_ids = _node_ids_and_labels(selected_nodes)

    print(f"[+] Scaling sweep N={node_count}: using nodes {[node.name for node in selected_nodes]}")

    register_nodes_to_project(core_client, project_id=project_id, node_ids=node_ids)

    return node_ids, node_labels, combined_node_ids
