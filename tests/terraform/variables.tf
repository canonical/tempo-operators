# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

variable "model_uuid" {
  description = "Model name to deploy the charm to"
  type        = string
}

variable "channel" {
  description = "Charm channel to deploy"
  type        = string
}

variable "endpoint" {
  description = "s3 endpoint to use"
  type        = string
}
