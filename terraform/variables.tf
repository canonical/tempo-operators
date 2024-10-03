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