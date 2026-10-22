variable "home_jenkins_ipv4" {
  description = "Home public IPv4 used for SSH access and the Jenkins DNS record"
  type        = string
  default     = "89.127.224.199"

  validation {
    condition     = can(cidrnetmask("${var.home_jenkins_ipv4}/32"))
    error_message = "home_jenkins_ipv4 must be a single IPv4 address without a CIDR suffix."
  }
}
