locals {
  shape = "VM.Standard.A1.Flex"
  # Porta 443/udp: HTTP/3 servido pelo Caddy.
  public_ports = {
    ssh   = { protocol = "6", port = 22, source = var.ssh_allowed_cidr }
    http  = { protocol = "6", port = 80, source = "0.0.0.0/0" }
    https = { protocol = "6", port = 443, source = "0.0.0.0/0" }
    http3 = { protocol = "17", port = 443, source = "0.0.0.0/0" }
  }
}

data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

# Ubuntu 24.04 (não a "Minimal") mais recente para ARM.
data "oci_core_images" "ubuntu" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = local.shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

# --- Rede --------------------------------------------------------------------
resource "oci_core_vcn" "main" {
  compartment_id = var.compartment_ocid
  display_name   = "lingualab-vcn"
  cidr_blocks    = ["10.0.0.0/16"]
  dns_label      = "lingualab"
}

resource "oci_core_internet_gateway" "main" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "lingualab-igw"
  enabled        = true
}

resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "lingualab-public-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.main.id
  }
}

resource "oci_core_security_list" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.main.id
  display_name   = "lingualab-public-sl"

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }

  dynamic "ingress_security_rules" {
    for_each = { for name, rule in local.public_ports : name => rule if rule.protocol == "6" }
    content {
      source   = ingress_security_rules.value.source
      protocol = "6"
      tcp_options {
        min = ingress_security_rules.value.port
        max = ingress_security_rules.value.port
      }
    }
  }

  dynamic "ingress_security_rules" {
    for_each = { for name, rule in local.public_ports : name => rule if rule.protocol == "17" }
    content {
      source   = ingress_security_rules.value.source
      protocol = "17"
      udp_options {
        min = ingress_security_rules.value.port
        max = ingress_security_rules.value.port
      }
    }
  }

  # Path MTU discovery (a regra que a Security List padrão da Oracle já traz).
  ingress_security_rules {
    source   = "0.0.0.0/0"
    protocol = "1"
    icmp_options {
      type = 3
      code = 4
    }
  }
}

resource "oci_core_subnet" "public" {
  compartment_id    = var.compartment_ocid
  vcn_id            = oci_core_vcn.main.id
  display_name      = "lingualab-public-subnet"
  cidr_block        = "10.0.1.0/24"
  dns_label         = "app"
  route_table_id    = oci_core_route_table.public.id
  security_list_ids = [oci_core_security_list.public.id]
}

# --- VM ----------------------------------------------------------------------
resource "oci_core_instance" "app" {
  compartment_id      = var.compartment_ocid
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[var.availability_domain_index].name
  display_name        = "lingualab"
  shape               = local.shape

  shape_config {
    ocpus         = var.ocpus
    memory_in_gbs = var.memory_in_gbs
  }

  source_details {
    source_type             = "image"
    source_id               = data.oci_core_images.ubuntu.images[0].id
    boot_volume_size_in_gbs = var.boot_volume_size_in_gbs
  }

  # IP público efêmero: continua o mesmo ao parar/iniciar a VM e só muda se ela for recriada.
  create_vnic_details {
    subnet_id        = oci_core_subnet.public.id
    assign_public_ip = true
  }

  metadata = {
    ssh_authorized_keys = join("\n", var.ssh_public_keys)
    user_data           = base64encode(file("${path.module}/cloud-init.yaml"))
  }

  lifecycle {
    # Uma imagem nova do Ubuntu não deve recriar a VM (o que apagaria o banco).
    ignore_changes = [source_details[0].source_id]
  }
}
