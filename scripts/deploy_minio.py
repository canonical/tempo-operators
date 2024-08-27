#!/usr/bin/env python3
"""Very simple utility script to deploy and configure minio+s3 integrator for local testing."""

import json
import os
import shlex
import subprocess
from time import sleep

from minio import Minio


def get_status():
    cmd = "juju status --format json"
    raw = subprocess.getoutput(cmd)
    status = json.loads(raw)
    return status


def get_model_name(status):
    try:
        return status['model']['name']
    except KeyError:
        return None


def get_s3_status(status):
    try:
        return status['applications']['s3']['units']['s3/0']
    except KeyError:
        return None


def get_minio_status(status):
    try:
        return status['applications']['minio']['units']['minio/0']
    except KeyError:
        return None


def _run(cmd: str):
    return subprocess.check_call(shlex.split(cmd))


def _check_juju_status(status, name):
    return status and (status['juju-status']['current'] == name)


def _check_workload_status(status, name):
    return status and (status['workload-status']['current'] == name)


def deploy(s3_app_name: str,
           minio_app_name: str,
           user: str,
           password: str,
           bucket_name: str,
           model: str = None):
    """Deploy minio and s3 integrator."""

    if model:
        _run(f"juju switch {model}")

    print("deploying minio and s3 integrator...")

    _run(
        f"juju deploy minio --channel edge --trust --config access-key={user} --config secret-key={password} {minio_app_name}")
    _run(f"juju deploy s3-integrator --channel edge --trust {s3_app_name}")

    print(f"waiting for minio ({minio_app_name}) to become active...", end='')
    while True:
        status = get_status()
        minio = get_minio_status(status)
        try:
            if _check_juju_status(get_s3_status(status), 'idle') and _check_workload_status(minio, "active"):
                break
        except KeyError:
            pass

        print('.', end='')
        sleep(1)

    print("minio and s3 integrator ready.")

    minio_addr = minio['address']

    mc_client = Minio(
        f"{minio_addr}:9000",
        access_key=user,
        secret_key=password,
        secure=False,
    )

    found = mc_client.bucket_exists(bucket_name)
    if found:
        print(f"bucket {bucket_name} already exists!")
    else:
        print("making bucket...")
        mc_client.make_bucket(bucket_name)

    print("syncing s3 credentials...")
    # configure s3-integrator
    model_name = get_model_name(status)
    _run(
        f"juju config {s3_app_name} endpoint={minio_app_name}-0.minio-endpoints.{model_name}.svc.cluster.local:9000 bucket={bucket_name}")
    _run(f"juju run {s3_app_name}/leader sync-s3-credentials access-key={user} secret-key={password}")

    print("all done! have fun.")


if __name__ == '__main__':
    deploy(
        s3_app_name=os.getenv("MINIO_S3_APP", "s3"),
        minio_app_name=os.getenv("MINIO_APP", "minio"),
        user=os.getenv("MINIO_USER", "accesskey"),
        password=os.getenv("MINIO_PASSWORD", "secretkey"),
        bucket_name=os.getenv("MINIO_BUCKET", "tempo"),
        model=os.getenv("MINIO_MODEL", None),
    )
