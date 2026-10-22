output "public_ip" {
  value = oci_core_instance.app.public_ip
}

# Domínio gratuito que já resolve para o IP da VM; use-o se não tiver um domínio próprio.
output "sslip_domain" {
  value = "${replace(oci_core_instance.app.public_ip, ".", "-")}.sslip.io"
}

output "ssh_command" {
  value = "ssh ubuntu@${oci_core_instance.app.public_ip}"
}
