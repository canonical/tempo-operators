import scenario

from tempo_cluster import TempoClusterProviderAppData


def get_tempo_config(state: scenario.State):
    cluster_relation = state.get_relations("tempo-cluster")[0]  # there's only one
    return TempoClusterProviderAppData.load(cluster_relation.local_app_data).tempo_config
