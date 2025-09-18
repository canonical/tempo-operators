output "app_name" {
  value = juju_application.tempo_worker.name
}

output "endpoints" {
  value = {
    # Requires
    tempo_cluster = "tempo-cluster"
    # Provides
  }
}