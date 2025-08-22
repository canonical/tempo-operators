from pathlib import Path

import jubilant
import logging
import yaml

from jubilant import Juju
from minio import Minio

ACCESS_KEY = "accesskey"
SECRET_KEY = "secretkey"
BUCKET_NAME = "tempo"
MINIO_APP = "minio"
S3_APP = "s3-integrator"
WORKER_APP = "tempo-worker"
TEMPO_APP = "tempo"
METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())

WORKER_RESOURCES = {
    image_name: image_meta["upstream-source"]
    for image_name, image_meta in METADATA["resources"].items()
}

logger = logging.getLogger(__name__)


def get_unit_ip_address(juju: Juju, app_name: str, unit_no: int):
    """Return a juju unit's IP address."""
    return juju.status().apps[app_name].units[f"{app_name}/{unit_no}"].address


def _deploy_and_configure_minio(juju: Juju):
    keys = {
        "access-key": ACCESS_KEY,
        "secret-key": SECRET_KEY,
    }
    juju.deploy(MINIO_APP, channel="edge", trust=True, config=keys)
    juju.wait(
        lambda status: status.apps[MINIO_APP].is_active,
        error=jubilant.any_error,
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
    juju.config(
        S3_APP,
        {
            "endpoint": f"minio-0.minio-endpoints.{juju.model}.svc.cluster.local:9000",
            "bucket": BUCKET_NAME,
        },
    )
    task = juju.run(S3_APP + "/0", "sync-s3-credentials", params=keys)
    assert task.status == "completed"


def deploy_minio_and_s3(juju: Juju):
    juju.deploy(S3_APP, channel="edge")

    _deploy_and_configure_minio(juju)

    juju.wait(
        lambda status: jubilant.all_active(status, S3_APP),
        timeout=2000,
    )
