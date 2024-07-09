from scenario import State

from charm import TempoCoordinatorCharm
from tempo_cluster import TempoClusterRequirerUnitData


def test_memberlist_multiple_members(context, all_worker, s3):
    workers_no = 3
    all_worker = all_worker.replace(
        remote_units_data={
            worker_idx: TempoClusterRequirerUnitData(
                **{
                    "address": f"worker-{worker_idx}.test.svc.cluster.local:7946",
                    "juju_topology": {
                        "model": "test",
                        "unit": f"worker/{worker_idx}",
                        "model_uuid": "1",
                        "application": "worker",
                        "charm_name": "TempoWorker",
                    },
                }
            ).dump()
            for worker_idx in range(workers_no)
        },
    )
    state = State(leader=True, relations=[all_worker, s3])
    with context.manager(all_worker.changed_event, state) as mgr:
        charm: TempoCoordinatorCharm = mgr.charm
        assert charm.tempo_cluster.gather_addresses() == set(
            [
                "worker-0.test.svc.cluster.local:7946",
                "worker-1.test.svc.cluster.local:7946",
                "worker-2.test.svc.cluster.local:7946",
            ]
        )
