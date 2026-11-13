# Optional lab features. Every default keeps the lab exactly as it was before these options existed.

variable "db_instance_type" {
  description = "Instance type for every MariaDB server. Changing it stops and restarts each server (in place, disks kept)."
  type        = string
  default     = "t3.small"
  validation {
    condition     = contains(["t3.small", "t3.medium", "t3.large", "m6i.large"], var.db_instance_type)
    error_message = "db_instance_type must be t3.small, t3.medium, t3.large or m6i.large."
  }
}

variable "mariadb_version" {
  description = "MariaDB series; only used here to pick a matching pre-built lab image."
  type        = string
  default     = "11.4"
  validation {
    condition     = contains(["10.11", "11.4", "11.8"], var.mariadb_version)
    error_message = "mariadb_version must be 10.11, 11.4 or 11.8."
  }
}

variable "use_lab_image" {
  description = "Build NEW servers from the newest pre-built lab image (see packer/). Existing servers are never replaced."
  type        = bool
  default     = false
}

data "aws_ami_ids" "lab_image" {
  count  = var.use_lab_image ? 1 : 0
  owners = ["self"]
  filter {
    name   = "name"
    values = ["mariadb-lab-node-${var.mariadb_version}-*"]
  }
}

locals {
  lab_image_ids = var.use_lab_image ? data.aws_ami_ids.lab_image[0].ids : []

  # aws_ami_ids lists the newest image first.
  node_ami = length(local.lab_image_ids) > 0 ? local.lab_image_ids[0] : data.aws_ami.ubuntu.id
}

check "lab_image_found" {
  assert {
    condition     = !var.use_lab_image || length(local.lab_image_ids) > 0
    error_message = "USE_LAB_IMAGE is on but no mariadb-lab-node-${var.mariadb_version}-* image exists yet; run ACTION=BUILD_IMAGE first. Falling back to stock Ubuntu."
  }
}

variable "multi_source_enabled" {
  description = "Add orders-primary (an independent primary) and warehouse (replicates from both primaries)."
  type        = bool
  default     = false
}

variable "loadgen_count" {
  description = "Load generator servers running sysbench through MaxScale."
  type        = number
  default     = 0
  validation {
    condition     = contains([0, 1, 2], var.loadgen_count)
    error_message = "loadgen_count must be 0, 1 or 2."
  }
}

locals {
  multisource_nodes = var.multi_source_enabled ? {
    "orders-primary" = "mariadb-orders-primary"
    warehouse        = "mariadb-warehouse"
  } : {}
  loadgen_nodes = { for n in range(1, var.loadgen_count + 1) : "loadgen${n}" => "mariadb-loadgen-${n}" }
  feature_nodes = merge(local.multisource_nodes, local.loadgen_nodes)
}

# Always present (security groups are free), so MaxScale's rules never depend on loadgen_count.
resource "aws_security_group" "loadgen" {
  name        = "jenkins-mariadb-lab-loadgen"
  description = "Load generators: SSH in, everything out"
  vpc_id      = local.vpc_id
  ingress {
    description = "SSH from home Jenkins"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["${var.home_jenkins_ipv4}/32"]
  }
  ingress {
    description     = "SSH from Jenkins"
    from_port       = 22
    to_port         = 22
    protocol        = "tcp"
    security_groups = [local.jenkins_sg_id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "jenkins-mariadb-lab-loadgen" }
}
