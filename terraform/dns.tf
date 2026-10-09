# DNS belongs to this lab's Terraform state; the hosted zone is never destroyed.
data "aws_route53_zone" "lab" {
  name         = "roblabb.com."
  private_zone = false
}

data "aws_instance" "jenkins_dns" {
  instance_id = "i-00f49e3237ca1fc0e"
}

locals {
  lab_dns_addresses = merge(
    { for role, node in aws_instance.node : role => node.public_ip },
    {
      ssm       = aws_instance.ssm.public_ip
      monitor   = aws_instance.monitor.public_ip
      dashboard = aws_instance.monitor.public_ip
      jenkins   = data.aws_instance.jenkins_dns.public_ip
    }
  )
}

resource "aws_route53_record" "lab" {
  for_each = local.lab_dns_addresses

  zone_id = data.aws_route53_zone.lab.zone_id
  name    = "${each.key}.roblabb.com"
  type    = "A"
  ttl     = 60
  records = [each.value]

  # Existing manually created records must be imported rather than overwritten.
  allow_overwrite = false

  lifecycle {
    precondition {
      condition     = each.value != ""
      error_message = "Each DNS target must have a public IPv4 address."
    }
  }
}

output "lab_dns" {
  value = {
    for name, record in aws_route53_record.lab : name => {
      hostname = record.fqdn
      address  = local.lab_dns_addresses[name]
    }
  }
}
