import scenario
from dataclasses import replace

_valid_roles = [
    "all",
    "querier",
    "query-frontend",
    "ingester",
    "distributor",
    "compactor",
    "metrics-generator",
]


def set_role(state: scenario.State, role: str):
    """Modify a state to activate a tempo role."""
    cfg = {}
    for role_ in _valid_roles:
        cfg[f"role-{role_}"] = False
    cfg[f"role-{role}"] = True
    return replace(state, config=cfg)
