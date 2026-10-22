# --- Autenticação ------------------------------------------------------------
variable "tenancy_ocid" {
  type = string
}

variable "user_ocid" {
  type = string
}

variable "fingerprint" {
  type = string
}

variable "private_key_path" {
  description = "Caminho da chave privada da API da Oracle (arquivo .pem)."
  type        = string
}

variable "region" {
  description = "Home Region da conta: as VMs Always Free só existem nela."
  type        = string
  default     = "sa-saopaulo-1"
}

variable "compartment_ocid" {
  description = "Compartimento onde os recursos são criados (o OCID da tenancy usa o compartimento raiz)."
  type        = string
}

# --- VM ----------------------------------------------------------------------
variable "ssh_public_keys" {
  description = "Chaves públicas com acesso SSH: a sua e a do pipeline de deploy."
  type        = list(string)
}

variable "ocpus" {
  description = "OCPUs da VM Ampere A1 (Always Free: até 4 no total)."
  type        = number
  default     = 2
}

variable "memory_in_gbs" {
  description = "RAM da VM em GB (Always Free: até 24 no total)."
  type        = number
  default     = 12
}

variable "boot_volume_size_in_gbs" {
  type    = number
  default = 50
}

variable "availability_domain_index" {
  description = "Troque para 1 ou 2 se a criação falhar com \"Out of capacity\" (regiões com mais de um AD)."
  type        = number
  default     = 0
}

variable "ssh_allowed_cidr" {
  description = "Origem liberada na porta 22. Os runners do GitHub Actions não têm IP fixo, por isso o padrão é aberto."
  type        = string
  default     = "0.0.0.0/0"
}
