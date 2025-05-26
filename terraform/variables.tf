variable "app_name" {
  description = "Application name"
  type        = string
  default     = "tempo"
}

#Defaulting to latest/edge instead of stable as its not yet released
variable "channel" {
  description = "Charm channel"
  type        = string
  default     = "latest/edge"
}


variable "config" {
  description = "Charm config options as in the ones we pass in juju config"
  type        = map(any)
  default     = {}
}

variable "model_name" {
  description = "Model name"
  type        = string
}

variable "revision" {
  description = "Charm revision"
  type        = number
  default     = null
}

variable "units" {
  description = "Number of units"
  type        = number
  default     = 1
}

# We use constraints to set AntiAffinity in K8s
# https://discourse.charmhub.io/t/pod-priority-and-affinity-in-juju-charms/4091/13?u=jose
variable "constraints" {
  description = "String listing constraints for this application"
  type        = string
  # FIXME: Passing an empty constraints value to the Juju Terraform provider currently
  # causes the operation to fail due to https://github.com/juju/terraform-provider-juju/issues/344
  default = "arch=amd64"
}
