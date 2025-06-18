# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from interface_tester import InterfaceTester


def test_tempo_cluster_v0_interface(cluster_tester: InterfaceTester):
    cluster_tester.configure(
        interface_name="tempo_cluster",
        interface_version=0,
    )
    cluster_tester.run()
