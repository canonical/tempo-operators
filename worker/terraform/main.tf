resource "juju_application" "tempo_worker" {

  name = var.app_name
  # Coordinator and worker must be in the same model
  model  = var.model_name
  trust  = true
  units  = var.units
  config = var.config


  charm {
    name     = "tempo-worker-k8s"
    channel  = var.channel
    revision = var.revision
  }

}