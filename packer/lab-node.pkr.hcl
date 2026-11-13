# Pre-installed lab server image (ACTION=BUILD_IMAGE). New servers built from it skip most of the
# package downloads in install.yml, prepare_zfs.yml, loadgen.yml and configure_ssm_clients.yml;
# those playbooks still run and simply find everything already installed.

packer {
  required_plugins {
    amazon = {
      version = ">= 1.3.0"
      source  = "github.com/hashicorp/amazon"
    }
  }
}

variable "mariadb_version" {
  type    = string
  default = "11.4"
}

source "amazon-ebs" "lab_node" {
  region                      = "eu-west-2"
  subnet_id                   = "subnet-0bb1a97785b8ba5a5"
  associate_public_ip_address = true
  instance_type               = "t3.small"
  ssh_username                = "ubuntu"
  # Packer's temporary security group only admits this Jenkins host's public IP.
  temporary_security_group_source_public_ip = true

  ami_name        = "mariadb-lab-node-${var.mariadb_version}-{{timestamp}}"
  ami_description = "MariaDB Jenkins lab node: MariaDB ${var.mariadb_version}, ZFS tools, sysbench, SSM client"
  encrypt_boot    = true

  source_ami_filter {
    owners      = ["099720109477"]
    most_recent = true
    filters = {
      name                = "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"
      architecture        = "x86_64"
      virtualization-type = "hvm"
    }
  }

  launch_block_device_mappings {
    device_name           = "/dev/sda1"
    volume_size           = 20
    volume_type           = "gp3"
    delete_on_termination = true
  }

  tags = {
    Project        = "mariadb-jenkins-lab"
    ManagedBy      = "Packer"
    MariaDBVersion = var.mariadb_version
  }
}

build {
  sources = ["source.amazon-ebs.lab_node"]

  provisioner "shell" {
    script           = "${path.root}/provision.sh"
    environment_vars = ["MARIADB_VERSION=${var.mariadb_version}"]
    execute_command  = "chmod +x {{ .Path }}; sudo -E bash '{{ .Path }}'"
  }

  post-processor "manifest" {
    output = "packer-manifest.json"
  }
}
