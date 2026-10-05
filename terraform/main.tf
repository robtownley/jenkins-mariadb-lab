variable "replica_count" {
  description = "Desired number of MariaDB replicas"
  type        = number
  default     = 2
  validation {
    condition     = var.replica_count >= 1 && var.replica_count <= 6 && floor(var.replica_count) == var.replica_count
    error_message = "replica_count must be a whole number between 1 and 6."
  }
}

locals {
  vpc_id        = "vpc-0e108fe4a8ffe3dee"
  subnet_id     = "subnet-0bb1a97785b8ba5a5"
  jenkins_sg_id = "sg-0243f521e59c4d971"

  replicas = {
    for n in range(1, var.replica_count + 1) : "replica${n}" => "mariadb-replica-${n}"
  }
  database_nodes = merge({ primary = "mariadb-primary" }, local.replicas)
  nodes          = merge(local.database_nodes, { maxscale = "mariadb-maxscale" })

  mysql_zfs_disks = merge([
    for node in keys(local.database_nodes) : {
      "${node}-a" = { node = node, device = "/dev/sdf" }
      "${node}-b" = { node = node, device = "/dev/sdg" }
    }
  ]...)
}

data "aws_subnet" "lab" {
  id = local.subnet_id
}

data "aws_security_group" "jenkins" {
  id = local.jenkins_sg_id
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
  filter {
    name   = "state"
    values = ["available"]
  }
}

resource "aws_key_pair" "lab" {
  key_name   = "jenkins-mariadb-lab"
  public_key = file("/var/lib/jenkins/.ssh/mariadb-lab.pub")
}

resource "aws_security_group" "maxscale" {
  name        = "jenkins-mariadb-lab-maxscale"
  description = "MaxScale lab access"
  vpc_id      = local.vpc_id
  ingress {
    description     = "SSH from Jenkins"
    from_port       = 22
    to_port         = 22
    protocol        = "tcp"
    security_groups = [local.jenkins_sg_id]
  }
  ingress {
    description     = "Database connections from Jenkins"
    from_port       = 3306
    to_port         = 3306
    protocol        = "tcp"
    security_groups = [local.jenkins_sg_id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "jenkins-mariadb-lab-maxscale" }
}

resource "aws_security_group" "database" {
  name        = "jenkins-mariadb-lab-database"
  description = "MariaDB replication and proxy access"
  vpc_id      = local.vpc_id
  ingress {
    description     = "SSH from Jenkins"
    from_port       = 22
    to_port         = 22
    protocol        = "tcp"
    security_groups = [local.jenkins_sg_id]
  }
  ingress {
    description = "Replication between database nodes"
    from_port   = 3306
    to_port     = 3306
    protocol    = "tcp"
    self        = true
  }
  ingress {
    description     = "Database connections from MaxScale"
    from_port       = 3306
    to_port         = 3306
    protocol        = "tcp"
    security_groups = [aws_security_group.maxscale.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "jenkins-mariadb-lab-database" }
}

resource "aws_security_group" "lab_ssh" {
  name        = "jenkins-mariadb-lab-internal-ssh"
  description = "SSH between lab nodes"
  vpc_id      = local.vpc_id
  ingress {
    description = "SSH from other lab nodes"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    self        = true
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "jenkins-mariadb-lab-internal-ssh" }
}

resource "aws_instance" "node" {
  for_each                    = local.nodes
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = "t3.small"
  subnet_id                   = local.subnet_id
  associate_public_ip_address = true
  key_name                    = aws_key_pair.lab.key_name
  vpc_security_group_ids = [
    each.key == "maxscale" ? aws_security_group.maxscale.id : aws_security_group.database.id,
    aws_security_group.lab_ssh.id
  ]
  root_block_device {
    volume_size           = 20
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
    tags = {
      Project = "mariadb-jenkins-lab"
      Name    = "${each.value}-root"
    }
  }
  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }
  credit_specification {
    cpu_credits = "standard"
  }
  tags = {
    Name = each.value
    Role = each.key
  }
  lifecycle {
    ignore_changes = [ami]
    precondition {
      condition = (
        data.aws_subnet.lab.vpc_id == local.vpc_id &&
        data.aws_security_group.jenkins.vpc_id == local.vpc_id
      )
      error_message = "The subnet and Jenkins security group must belong to the lab VPC."
    }
  }
}

resource "aws_ebs_volume" "mysql_zfs" {
  for_each          = local.mysql_zfs_disks
  availability_zone = data.aws_subnet.lab.availability_zone
  size              = 15
  type              = "gp3"
  encrypted         = true
  tags = {
    Project = "mariadb-jenkins-lab"
    Name    = "mariadb-zfs-${each.key}"
    Purpose = "mysql-zfs-mirror"
  }
}

resource "aws_volume_attachment" "mysql_zfs" {
  for_each     = local.mysql_zfs_disks
  device_name  = each.value.device
  volume_id    = aws_ebs_volume.mysql_zfs[each.key].id
  instance_id  = aws_instance.node[each.value.node].id
  force_detach = false
}

output "nodes" {
  value = {
    for role, instance in aws_instance.node : role => {
      name        = local.nodes[role]
      instance_id = instance.id
      private_ip  = instance.private_ip
      public_ip   = instance.public_ip
      zfs_volume_ids = role == "maxscale" ? [] : [
        aws_ebs_volume.mysql_zfs["${role}-a"].id,
        aws_ebs_volume.mysql_zfs["${role}-b"].id
      ]
    }
  }
  depends_on = [aws_volume_attachment.mysql_zfs]
}

output "maxscale_endpoint" {
  value = "${aws_instance.node["maxscale"].private_ip}:3306"
}

output "mysql_zfs_volumes" {
  value = {
    for node in keys(local.database_nodes) : node => {
      disk_a = aws_ebs_volume.mysql_zfs["${node}-a"].id
      disk_b = aws_ebs_volume.mysql_zfs["${node}-b"].id
    }
  }
}
