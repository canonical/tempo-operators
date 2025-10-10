import scenario
from coordinated_workers.interfaces.cluster import ClusterProviderAppData


def get_tempo_config(state: scenario.State):
    cluster_relation = state.get_relations("tempo-cluster")[0]  # there's only one
    return ClusterProviderAppData.load(cluster_relation.local_app_data).worker_config
