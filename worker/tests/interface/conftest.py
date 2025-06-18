# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import tempfile
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from unittest.mock import patch

import pytest
from charms.tempo_coordinator_k8s.v0.charm_tracing import charm_tracing_disabled
from interface_tester import InterfaceTester
from ops import ActiveStatus
from scenario import Container, Mount, State, Exec
from unittest.mock import MagicMock
from coordinated_workers.worker import CONFIG_FILE

from charm import TempoWorkerK8SOperatorCharm

k8s_resource_patch_ready = MagicMock(return_value=True)


@contextmanager
def _urlopen_patch(url: str, resp, tls: bool = False):
    if url == f"{'https' if tls else 'http'}://localhost:3200/ready":
        mm = MagicMock()
        mm.read = MagicMock(return_value=resp.encode("utf-8"))
        yield mm
    else:
        raise RuntimeError("unknown path")


# Interface tests are centrally hosted at https://github.com/canonical/charm-relation-interfaces.
# this fixture is used by the test runner of charm-relation-interfaces to test tempo's compliance
# with the interface specifications.
# DO NOT MOVE OR RENAME THIS FIXTURE! If you need to, you'll need to open a PR on
# https://github.com/canonical/charm-relation-interfaces and change tempo's test configuration
# to include the new identifier/location.
@pytest.fixture
def interface_tester(interface_tester: InterfaceTester):
    td = tempfile.TemporaryDirectory()
    filename = "config.yaml"
    conf_file = Path(td.name).joinpath(filename)
    conf_file.write_text("foo: bar")

    with patch.multiple(
        "coordinated_workers.worker.KubernetesComputeResourcesPatch",
        _namespace="test-namespace",
        _patch=lambda _: None,
        get_status=lambda _: ActiveStatus(""),
        is_ready=k8s_resource_patch_ready,
    ):
        with patch("urllib.request.urlopen", new=partial(_urlopen_patch, resp="ready")):
            with patch("lightkube.core.client.GenericSyncClient"):
                with patch("subprocess.run"):
                    with charm_tracing_disabled():
                        interface_tester.configure(
                            charm_type=TempoWorkerK8SOperatorCharm,
                            state_template=State(
                                leader=True,
                                containers=[
                                    Container(
                                        name="tempo",
                                        can_connect=True,
                                        mounts={
                                            "worker-config": Mount(
                                                location=CONFIG_FILE, source=conf_file
                                            )
                                        },
                                        execs={
                                            Exec(("update-ca-certificates", "--fresh")),
                                        },
                                    )
                                ],
                            ),
                        )
                        yield interface_tester
