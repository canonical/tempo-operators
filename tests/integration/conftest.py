# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import os
import subprocess
from contextlib import contextmanager, nullcontext
from functools import partial
from pathlib import Path
from typing import Literal, Sequence

import jubilant
from jubilant import Juju
from minio import Minio
from pytest import fixture
from pytest_jubilant import pack_charm, get_resources

from tests.integration.helpers import get_unit_ip_address

ACCESS_KEY = "accesskey"
SECRET_KEY = "secretkey"
BUCKET_NAME = "tempo"
MINIO_APP = "minio"
SSC_APP = "ssc"
S3_APP = "s3-integrator"
WORKER_APP = "tempo-worker"
TEMPO_APP = "tempo"
TRAEFIK_APP = "trfk"
# we don't import this from the coordinator module because that'd mean we need to
# bring in the whole charm's dependencies just to run the integration tests
ALL_ROLES = [
    "querier",
    "query_frontend",
    "ingester",
    "distributor",
    "compactor",
    "metrics_generator",
]
ALL_WORKERS = [f"{WORKER_APP}-" + role for role in ALL_ROLES]

logger = logging.getLogger(__name__)


def pytest_addoption(parser):
    group = parser.getgroup("test-config")
    group.addoption(
        "--tls",
        dest="tls",
        action="store",
        default="0",
        help="Run tests with tls enabled.",
    )
    group.addoption(
        "--distributed",
        dest="distributed",
        action="store",
        default="0",
        help="Run tests with distributed deployment mode enabled.",
    )


@fixture(scope="session")
def coordinator_charm():
    """Pyroscope coordinator used for integration testing."""
    return charm_and_channel_and_resources("coordinator", "COORDINATOR_CHARM_PATH", "COORDINATOR_CHARM_CHANNEL")


@fixture(scope="session")
def worker_charm():
    """Pyroscope worker used for integration testing."""
    return charm_and_channel_and_resources("worker", "WORKER_CHARM_PATH", "WORKER_CHARM_CHANNEL")


@fixture(scope="session")
def tls(pytestconfig):
    """Run with or without tls."""
    return pytestconfig.getoption("tls") == "1"


@fixture(scope="session")
def distributed(pytestconfig):
    """Run in monolithic or fully distributed mode."""
    return pytestconfig.getoption("distributed") == "1"


def charm_and_channel_and_resources(role: Literal["coordinator", "worker"], charm_path_key: str,
                                    charm_channel_key: str):
    """Pyrosocope coordinator or worker charm used for integration testing.

    Build once per session and reuse it in all integration tests to save some minutes/hours.
    """
    # deploy charm from charmhub
    if channel_from_env := os.getenv(charm_channel_key):
        charm = f"pyroscope-{role}-k8s"
        logger.info(f"Using published {charm} charm from {channel_from_env}")
        return charm, channel_from_env, None
    # else deploy from a charm packed locally
    elif path_from_env := os.getenv(charm_path_key):
        charm_path = Path(path_from_env).absolute()
        logger.info("Using local {role} charm: %s", charm_path)
        return (
            charm_path,
            None,
            get_resources(charm_path.parent),
        )
    # else try to pack the charm
    for _ in range(3):
        logger.info(f"packing Tempo {role} charm...")
        try:
            pth = pack_charm(Path() / role).charm.absolute()
        except subprocess.CalledProcessError:
            logger.warning(f"Failed to build Tempo {role}. Trying again!")
            continue
        os.environ[charm_path_key] = str(pth)
        return pth, None, get_resources(pth.parent / role)
    raise subprocess.CalledProcessError


def _deploy_monolithic_cluster(juju: Juju, coordinator_deployed_as=None):
    """Deploy a pyroscope-monolithic cluster."""
    worker_charm_url, channel, resources = charm_and_channel_and_resources("worker", "WORKER_CHARM_PATH",
                                                                           "WORKER_CHARM_CHANNEL")

    juju.deploy(
        worker_charm_url,
        app=WORKER_APP,
        channel=channel,
        trust=True,
        resources=resources,
    )
    _deploy_cluster(juju, [WORKER_APP], coordinator_deployed_as=coordinator_deployed_as)


def _deploy_distributed_cluster(juju: Juju, roles: Sequence[str] = tuple(ALL_ROLES), coordinator_deployed_as=None):
    """Deploy a tempo distributed cluster."""
    worker_charm_url, channel, resources = charm_and_channel_and_resources("worker", "WORKER_CHARM_PATH",
                                                                           "WORKER_CHARM_CHANNEL")

    all_workers = []

    for role in roles or ALL_ROLES:
        worker_name = f"{WORKER_APP}-{role}"
        all_workers.append(worker_name)

        juju.deploy(
            worker_charm_url,
            app=worker_name,
            channel=channel,
            trust=True,
            config={"role-all": False, f"role-{role}": True},
            resources=resources,
        )

    _deploy_cluster(juju, all_workers, coordinator_deployed_as=coordinator_deployed_as)


def _deploy_s3(juju: Juju):
    """Deploys S3 integrator and s3 backend applications."""
    # todo replace with seaweedfs once it has TLS support
    juju.deploy(S3_APP, channel="edge")
    keys = {
        "access-key": ACCESS_KEY,
        "secret-key": SECRET_KEY,
    }
    juju.deploy(MINIO_APP, channel="edge", trust=True, config=keys)
    logger.info(f"waiting for {MINIO_APP} to become active...")
    juju.wait(
        lambda status: status.apps[MINIO_APP].is_active,
        error=jubilant.any_error,
        timeout=2000,
        delay=5
    )
    minio_addr = get_unit_ip_address(juju, MINIO_APP, 0)

    mc_client = Minio(
        f"{minio_addr}:9000",
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        secure=False,
    )

    # create tempo bucket
    found = mc_client.bucket_exists(BUCKET_NAME)
    if not found:
        mc_client.make_bucket(BUCKET_NAME)

    # configure s3-integrator
    juju.config(S3_APP, {
        "endpoint": f"minio-0.minio-endpoints.{juju.model}.svc.cluster.local:9000",
        "bucket": BUCKET_NAME,
    })
    task = juju.run(S3_APP + "/0", "sync-s3-credentials", params=keys)
    assert task.status == "completed"


def _deploy_cluster(juju: Juju, workers: Sequence[str], coordinator_deployed_as: str = None):
    if coordinator_deployed_as:
        coordinator_app = coordinator_deployed_as
    else:
        coordinator_charm_url, channel, resources = charm_and_channel_and_resources("coordinator",
                                                                                    "COORDINATOR_CHARM_PATH",
                                                                                    "COORDINATOR_CHARM_CHANNEL")
        juju.deploy(
            coordinator_charm_url, TEMPO_APP, channel=channel, resources=resources, trust=True
        )
        coordinator_app = TEMPO_APP

    logger.info(f"deploying S3")
    _deploy_s3(juju)

    juju.integrate(coordinator_app + ":s3", S3_APP + ":s3-credentials")
    for worker in workers:
        juju.integrate(coordinator_app, worker)

    logger.info("waiting for active...")
    juju.wait(
        lambda status: jubilant.all_active(status, coordinator_app, *workers, S3_APP),
        timeout=2000,
        delay=5,
        successes=3,
    )
    logger.info("S3 deployed.")


@contextmanager
def _tls_ctx(active:bool, juju: Juju, distributed: bool):
    """Context manager to set up tls integration for tempo and s3 integrator."""
    if not active:  # a bit ugly, but nicer than using a nullcontext
        yield
        return

    logger.info("adding TLS")
    juju.deploy("self-signed-certificates", SSC_APP)
    juju.integrate(SSC_APP, TEMPO_APP)
    juju.integrate(SSC_APP+":certificates", S3_APP+":certificates")

    logger.info("waiting for active...")
    juju.wait(
        lambda status: jubilant.all_active(status, TEMPO_APP, *(ALL_WORKERS if distributed else (WORKER_APP,)), S3_APP),
        timeout=2000,
        delay=5,
        successes=3,
    )
    logger.info("TLS ready")

    yield

    juju.remove_application(SSC_APP)


@fixture(scope="module")
def deployment(tls, distributed, juju, coordinator_charm, worker_charm, pytestconfig):
    if not pytestconfig.getoption("--no-setup"):
        if distributed:
            logger.info("deploying distributed cluster...")
            _deploy_distributed_cluster(juju)
        else:
            logger.info("deploying monolithic cluster...")
            _deploy_monolithic_cluster(juju)
        logger.info("cluster deployed.")

    with _tls_ctx(tls, juju=juju, distributed=distributed):
        yield juju

    if not pytestconfig.getoption("--no-teardown"):
        logger.info("tearing down all apps...")
        for app_to_remove in {TEMPO_APP, *(ALL_WORKERS if distributed else (WORKER_APP,))}:
            juju.remove_application(app_to_remove)