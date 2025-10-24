# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

module "tempo" {
  source        = "../../terraform"
  model         = var.model
  channel       = var.channel
  s3_endpoint   = var.endpoint
  s3_access_key = "placeholder"
  s3_secret_key = "placeholder"
}
