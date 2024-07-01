# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Helper module for interacting with the Tempo configuration."""

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, field_validator, model_validator

S3_RELATION_NAME = "s3"
BUCKET_NAME = "tempo"

logger = logging.getLogger(__name__)


class InvalidConfigurationError(Exception):
    """Invalid configuration."""

    pass


class Kvstore(BaseModel):
    """Kvstore schema."""

    store: str = "memberlist"


class Ring(BaseModel):
    """Ring schema."""

    kvstore: Kvstore
    replication_factor: int


class Memberlist(BaseModel):
    """Memberlist schema."""

    abort_if_cluster_join_fails: bool
    bind_port: int
    join_members: List[str]
    tls_enabled: Optional[bool] = False
    tls_cert_path: Optional[str] = None
    tls_key_path: Optional[str] = None
    tls_ca_path: Optional[str] = None
    tls_server_name: Optional[str] = None


class ClientTLS(BaseModel):
    """Client tls config schema."""

    tls_enabled: bool
    tls_cert_path: str
    tls_key_path: str
    tls_ca_path: str
    tls_server_name: str


class Client(BaseModel):
    """Client schema."""

    grpc_client_config: ClientTLS


class Distributor(BaseModel):
    """Distributor schema."""

    ring: Optional[Ring] = None
    receivers: Dict[str, Any]


class Ingester(BaseModel):
    """Ingester schema."""

    trace_idle_period: str
    max_block_bytes: int
    max_block_duration: str
    lifecycler: Optional[Ring] = None


class FrontendWorker(BaseModel):
    """FrontendWorker schema."""

    frontend_address: str
    grpc_client_config: Optional[ClientTLS] = None


class Querier(BaseModel):
    """Querier schema."""

    frontend_worker: FrontendWorker


class TLS(BaseModel):
    """TLS configuration schema."""

    cert_file: str
    key_file: str
    client_ca_file: str
    client_auth_type: Optional[str] = "VerifyClientCertIfGiven"


class Server(BaseModel):
    """Server schema."""

    http_listen_port: int
    grpc_listen_port: int
    http_tls_config: Optional[TLS] = None
    grpc_tls_config: Optional[TLS] = None


class Compaction(BaseModel):
    """Compaction schema."""

    compaction_window: str
    max_compaction_objects: int
    block_retention: str
    compacted_block_retention: str
    v2_out_buffer_bytes: int


class Compactor(BaseModel):
    """Compactor schema."""

    ring: Optional[Ring] = None
    compaction: Compaction


class Pool(BaseModel):
    """Pool schema."""

    max_workers: int
    queue_depth: int


class Wal(BaseModel):
    """Wal schema."""

    path: str


class S3(BaseModel):
    """S3 config schema."""

    bucket: str
    access_key: str
    endpoint: str
    secret_key: str
    insecure: Optional[bool] = False

    @model_validator(mode="before")  # pyright: ignore
    @classmethod
    def set_insecure(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("endpoint", None):
            data["insecure"] = False if data["endpoint"].startswith("https://") else True
        return data

    @field_validator("endpoint")
    def remove_scheme(cls, v: str) -> str:
        """Remove the scheme from the s3 endpoint."""
        # remove scheme to avoid "Endpoint url cannot have fully qualified paths." on Tempo startup
        return re.sub(
            rf"^{urlparse(v).scheme}://",
            "",
            v,
        )


class Block(BaseModel):
    """Block schema."""

    version: Optional[str]


class TraceStorage(BaseModel):
    """Trace Storage schema."""

    wal: Wal
    pool: Pool
    backend: str
    s3: S3
    block: Block


class Storage(BaseModel):
    """Storage schema."""

    trace: TraceStorage


class Tempo(BaseModel):
    """Tempo config schema."""

    auth_enabled: bool
    server: Server
    distributor: Distributor
    ingester: Ingester
    memberlist: Memberlist
    compactor: Compactor
    querier: Querier
    storage: Storage
    ingester_client: Optional[Client] = None
    metrics_generator_client: Optional[Client] = None
