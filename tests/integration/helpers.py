# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import yaml
from pathlib import Path
import os
import subprocess
from minio import Minio

from typing import Literal, Sequence, Union
from pytest_jubilant import pack_charm
from jubilant import Juju
import jubilant

# Application names used uniformly across the tests
ACCESS_KEY = "accesskey"
SECRET_KEY = "secretkey"
BUCKET_NAME = "pyroscope"
MINIO_APP = "minio"
S3_APP = "s3-integrator"
WORKER_APP = "pyroscope-worker"
PYROSCOPE_APP = "pyroscope"
TRAEFIK_APP = "trfk"

logger = logging.getLogger(__name__)

def get_unit_ip_address(juju: Juju, app_name: str, unit_no: int):
    """Return a juju unit's IP address."""
    return juju.status().apps[app_name].units[f"{app_name}/{unit_no}"].address

def charm_and_channel_and_resources(role: Literal["coordinator", "worker"], charm_path_key: str, charm_channel_key: str):
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
            _get_resources(charm_path.parent),
        )
    # else try to pack the charm
    for _ in range(3):
        logger.info(f"packing Pyroscope {role} charm...")
        try:
            pth = pack_charm(Path() / role).charm.absolute()
        except subprocess.CalledProcessError:
            logger.warning(f"Failed to build Pyroscope {role}. Trying again!")
            continue
        os.environ[charm_path_key] = str(pth)
        return pth, None, _get_resources(pth.parent / role)
    raise subprocess.CalledProcessError


def deploy_monolithic_cluster(juju: Juju, coordinator_deployed_as=None):
    """Deploy a pyroscope-monolithic cluster."""
    worker_charm_url, channel, resources = charm_and_channel_and_resources("worker", "WORKER_CHARM_PATH", "WORKER_CHARM_CHANNEL")

    juju.deploy(
        worker_charm_url,
        app=WORKER_APP,
        channel=channel,
        trust=True,
        resources=resources,
    )
    _deploy_cluster(juju, [WORKER_APP], coordinator_deployed_as=coordinator_deployed_as)

def _deploy_cluster(juju: Juju, workers: Sequence[str], coordinator_deployed_as: str = None):
    if coordinator_deployed_as:
        coordinator_app = coordinator_deployed_as
    else:
        coordinator_charm_url, channel, resources = charm_and_channel_and_resources("coordinator", "COORDINATOR_CHARM_PATH", "COORDINATOR_CHARM_CHANNEL")
        juju.deploy(
            coordinator_charm_url, PYROSCOPE_APP, channel=channel, resources=resources, trust=True
        )
        coordinator_app = PYROSCOPE_APP

    juju.deploy(S3_APP, channel="edge")

    juju.integrate(coordinator_app + ":s3", S3_APP + ":s3-credentials")
    for worker in workers:
        juju.integrate(coordinator_app + ":pyroscope-cluster", worker + ":pyroscope-cluster")

    _deploy_and_configure_minio(juju)

    juju.wait(
        lambda status: jubilant.all_active(status, coordinator_app, *workers, S3_APP),
        timeout=2000,
        delay=5,
        successes=3,
    )

def _get_resources(path: Union[str, Path]):
    meta = yaml.safe_load((Path(path) / "charmcraft.yaml").read_text())
    resources_meta = meta.get("resources", {})
    return {res_name: res_meta["upstream-source"] for res_name, res_meta in resources_meta.items()}

def _deploy_and_configure_minio(juju: Juju):
    keys = {
        "access-key": ACCESS_KEY,
        "secret-key": SECRET_KEY,
    }
    juju.deploy(MINIO_APP, channel="edge", trust=True, config=keys)
    juju.wait(
        lambda status: status.apps[MINIO_APP].is_active,
        error=jubilant.any_error,
        delay=5,
        successes=3,
    )
    minio_addr = get_unit_ip_address(juju, MINIO_APP, 0)

    mc_client = Minio(
        f"{minio_addr}:9000",
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        secure=False,
    )

    # create pyroscope bucket
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

def get_ingress_proxied_hostname(juju: Juju):
    status = juju.status()
    status_msg = status.apps[TRAEFIK_APP].app_status.message

    # hacky way to get ingress hostname, but it's the safest one.
    if "Serving at" not in status_msg:
        raise RuntimeError(
            f"Ingressed hostname is not present in {TRAEFIK_APP} status message."
        )
    return status_msg.replace("Serving at", "").strip()