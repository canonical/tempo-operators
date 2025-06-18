from contextlib import contextmanager
from pathlib import PosixPath
from unittest.mock import MagicMock
import socket
import pytest
from cosl.coordinated_workers.worker import ROOT_CA_CERT
from scenario import Context, Exec
from ops import ActiveStatus
from unittest.mock import patch

from charm import TempoWorkerK8SOperatorCharm


@contextmanager
def _urlopen_patch(url: str, resp, tls: bool = False):
    if url == f"{'https' if tls else 'http'}://{socket.getfqdn()}:3200/ready":
        mm = MagicMock()
        mm.read = MagicMock(return_value=resp.encode("utf-8"))
        yield mm
    else:
        raise RuntimeError("unknown path")


class NonWriteablePath(PosixPath):
    def write_text(self, data, encoding=None, errors=None, newline=None):
        pass


@pytest.fixture(autouse=True)
def patch_all():
    with patch.multiple(
        "cosl.coordinated_workers.worker.KubernetesComputeResourcesPatch",
        _namespace="test-namespace",
        _patch=lambda _: None,
        get_status=lambda _: ActiveStatus(""),
        is_ready=lambda _: True,
    ):
        with patch("lightkube.core.client.GenericSyncClient"):
            with patch("subprocess.run"):
                with patch(
                    "cosl.coordinated_workers.worker.ROOT_CA_CERT",
                    new=NonWriteablePath(ROOT_CA_CERT),
                ):
                    yield


@pytest.fixture
def ctx():
    return Context(charm_type=TempoWorkerK8SOperatorCharm)


TEMPO_VERSION_EXEC_OUTPUT = Exec(
    command_prefix=("/bin/tempo", "-version"), stdout="1.31"
)
UPDATE_CA_CERTS_EXEC_OUTPUT = Exec(command_prefix=("update-ca-certificates", "--fresh"))
