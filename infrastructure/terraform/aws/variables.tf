variable "name" {
  description = "Resource name prefix."
  type        = string
  default     = "nimbrix"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "vpc_id" {
  description = "Existing VPC to deploy into."
  type        = string
}

variable "public_subnet_ids" {
  description = "Subnets for the internet-facing load balancer (at least two AZs)."
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "Subnets for ECS tasks and RDS (no public IPs; NAT or VPC endpoints for ECR/Logs/Secrets Manager)."
  type        = list(string)
}

variable "certificate_arn" {
  description = "ACM certificate for the HTTPS listener."
  type        = string
}

variable "allowed_ingress_cidrs" {
  description = "CIDRs allowed to reach the load balancer. Restrict for private demos."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "image_tag" {
  description = "Immutable image tag (e.g. git SHA) pushed to both ECR repositories."
  type        = string
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.small"
}

variable "db_multi_az" {
  type    = bool
  default = false
}

variable "db_allocated_storage_gb" {
  type    = number
  default = 20
}

variable "api_desired_count" {
  type    = number
  default = 2
}

variable "web_desired_count" {
  type    = number
  default = 2
}

variable "allow_demo_auth_in_production" {
  description = "Demo password login is not production identity. OIDC is not implemented, so a deployment without this flag has no way to sign in. Set true only for a private demo and say so."
  type        = bool
  default     = false
}

variable "aws_read_access_enabled" {
  description = "Allow the runtime role to assume customer read-only roles named NimbrixReadOnly."
  type        = bool
  default     = false
}

variable "tags" {
  type    = map(string)
  default = {}
}
