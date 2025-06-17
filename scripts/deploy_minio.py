#!/usr/bin/env python3
"""Very simple utility script to deploy and configure minio+s3 integrator for local testing."""

import json
import os
import shlex
import subprocess
from time import sleep
from typing import Union

from minio import Minio
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from datetime import datetime, timezone, timedelta
import base64


def wait_for_minio_idle(minio_app_name, s3_app_name):
    # sleep before polling as it might take some time for the status to change
    sleep(10)
    while True:
        status = get_status()
        minio = get_minio_status(status, minio_app_name)
        try:
            if _check_juju_status(get_s3_status(status, s3_app_name), "idle") and \
                _check_workload_status(minio, "active") and \
                _check_juju_status(minio, "idle") and \
                minio.get("address"):
                break
        except KeyError:
            pass

        print(".", end="")
        sleep(1)

def configure_minio_certs(app_name, hostname):
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"Test CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_subject)
        .issuer_name(ca_subject)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key=ca_key, algorithm=hashes.SHA256())
    )

    minio_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    minio_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])

    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(minio_subject)
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName(hostname),
            ]),
            critical=False
        )
        .sign(minio_key, hashes.SHA256())
    )
    
    minio_cert = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(ca_cert.subject)
        .public_key(minio_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName(hostname),
            ]),
            critical=False
        )
        .sign(private_key=ca_key, algorithm=hashes.SHA256())
    )

    print("Configuring minio to use TLS")
    _run(f"juju config {app_name} ssl-cert={_b64pem(minio_cert)} ssl-key={_b64key(minio_key)} ssl-ca={_b64pem(ca_cert)}")

def get_status():
    cmd = "juju status --format json"
    raw = subprocess.getoutput(cmd)
    status = json.loads(raw)
    return status


def get_model_name(status):
    try:
        return status["model"]["name"]
    except KeyError:
        return None


def get_s3_status(status, s3_app_name):
    try:
        return status["applications"][s3_app_name]["units"][f"{s3_app_name}/0"]
    except KeyError:
        return None


def get_minio_status(status, minio_app_name):
    try:
        return status["applications"][minio_app_name]["units"][f"{minio_app_name}/0"]
    except KeyError:
        return None


def _run(cmd: str):
    return subprocess.check_call(shlex.split(cmd))


def _check_juju_status(status, name):
    return status and (status["juju-status"]["current"] == name)


def _check_workload_status(status, name):
    return status and (status["workload-status"]["current"] == name)

def _b64pem(obj: Union[x509.Certificate, x509.CertificateSigningRequest]) -> str:
    return base64.b64encode(
        obj.public_bytes(serialization.Encoding.PEM)
    ).decode()

def _b64key(key) -> str:
    return base64.b64encode(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        )
    ).decode()
    

def deploy(
    s3_app_name: str,
    minio_app_name: str,
    user: str,
    password: str,
    bucket_name: str,
    model: str = None,
    tls: bool = False,
):
    """Deploy minio and s3 integrator."""

    if model:
        _run(f"juju switch {model}")

    status = get_status()
    if not get_minio_status(status, minio_app_name):
        print(f"deploying minio as {minio_app_name!r}")
        _run(
            f"juju deploy minio --channel edge --trust --config access-key={user} --config secret-key={password} {minio_app_name}"
        )
    else:
        print(f"found existing minio deployment at {minio_app_name}")

    if not get_s3_status(status, s3_app_name):
        print(f"deploying s3 as {s3_app_name!r}")
        _run(f"juju deploy s3-integrator --channel edge --trust {s3_app_name}")
    else:
        print(f"found existing s3 deployment at {minio_app_name}")


    print(f"waiting for minio ({minio_app_name}) to report active...", end="")
    wait_for_minio_idle(minio_app_name, s3_app_name)
    print("minio and s3 integrator ready.")

    status = get_status()
    model_name = get_model_name(status)
    minio_hostname = f"{minio_app_name}-0.minio-endpoints.{model_name}.svc.cluster.local"

    if tls:
        configure_minio_certs(minio_app_name, minio_hostname)
        print(f"waiting for minio ({minio_app_name}) to become active...", end="")
        wait_for_minio_idle(minio_app_name, s3_app_name)
        print("minio and s3 integrator ready.")
        # get the updated status 
        status = get_status()

    minio = get_minio_status(status, minio_app_name)
    minio_addr = minio["address"]

    mc_client = Minio(
        f"{minio_addr}:9000",
        access_key=user,
        secret_key=password,
        secure=tls,
        # disable server cert validation to avoid the hassle of passing a CA cert
        cert_check=False,
    )

    found = mc_client.bucket_exists(bucket_name)
    if found:
        print(f"bucket {bucket_name} already exists!")
    else:
        print("making bucket...")
        mc_client.make_bucket(bucket_name)

    print("syncing s3 credentials...")
    # configure s3-integrator
    _run(
        f"juju config {s3_app_name} endpoint={minio_hostname}:9000 bucket={bucket_name}"
    )
    _run(
        f"juju run {s3_app_name}/leader sync-s3-credentials access-key={user} secret-key={password}"
    )

    print("all done! have fun.")


if __name__ == "__main__":
    deploy(
        s3_app_name=os.getenv("MINIO_S3_APP", "s3"),
        minio_app_name=os.getenv("MINIO_APP", "minio"),
        user=os.getenv("MINIO_USER", "accesskey"),
        password=os.getenv("MINIO_PASSWORD", "secretkey"),
        bucket_name=os.getenv("MINIO_BUCKET", "tempo"),
        model=os.getenv("MINIO_MODEL", None),
        tls=os.getenv("MINIO_TLS", False),
    )
