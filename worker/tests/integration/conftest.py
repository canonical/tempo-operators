import os
from pathlib import Path

import logging
import pytest
import subprocess
from pytest_jubilant import pack_charm


logger = logging.getLogger(__name__)

@pytest.fixture(scope="session")
def tempo_worker_charm():
    return _tempo_worker_charm()

def _tempo_worker_charm() -> Path:
    """Tempo worker charm used for integration testing."""
    if charm_file := os.environ.get("CHARM_PATH"):
        return Path(charm_file)

    # Intermittent issue where charmcraft fails to build the charm for an unknown reason.
    # Retry building the charm
    for _ in range(3):
        logger.info("packing...")
        try:
            pth = pack_charm().charm.absolute()
        except subprocess.CalledProcessError:
            logger.warning("Failed to build tempo-worker. Trying again!")
            continue
        os.environ["CHARM_PATH"] = str(pth)
        return pth
    raise err  # noqa