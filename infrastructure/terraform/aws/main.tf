locals {
  container_insights = "enabled"
  api_port           = 8000
  web_port           = 3000
  log_retention_days = 30
}

data "aws_caller_identity" "current" {}

# --- Container registries -----------------------------------------------------

resource "aws_ecr_repository" "this" {
  for_each             = toset(["api", "web"])
  name                 = "${var.name}-${each.key}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "KMS"
  }
}

# --- Networking ----------------------------------------------------------------

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "Public HTTPS entry point"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  for_each          = toset(var.allowed_ingress_cidrs)
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.value
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "alb_http_redirect" {
  for_each          = toset(var.allowed_ingress_cidrs)
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.value
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_web" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.web.id
  from_port                    = local.web_port
  to_port                      = local.web_port
  ip_protocol                  = "tcp"
}

resource "aws_security_group" "web" {
  name        = "${var.name}-web"
  description = "Next.js web tier"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "web_from_alb" {
  security_group_id            = aws_security_group.web.id
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = local.web_port
  to_port                      = local.web_port
  ip_protocol                  = "tcp"
}

resource "aws_security_group" "api" {
  name        = "${var.name}-api"
  description = "API reachable only from the web tier"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "api_from_web" {
  security_group_id            = aws_security_group.api.id
  referenced_security_group_id = aws_security_group.web.id
  from_port                    = local.api_port
  to_port                      = local.api_port
  ip_protocol                  = "tcp"
}

resource "aws_security_group" "worker" {
  name        = "${var.name}-worker"
  description = "Background worker and migration tasks (no inbound)"
  vpc_id      = var.vpc_id
}

resource "aws_security_group" "db" {
  name        = "${var.name}-db"
  description = "PostgreSQL reachable only from API and worker"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "db_from_app" {
  for_each = {
    api    = aws_security_group.api.id
    worker = aws_security_group.worker.id
  }
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = each.value
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

# Tasks need outbound HTTPS (ECR, CloudWatch Logs, Secrets Manager, optional AWS/LLM APIs).
resource "aws_vpc_security_group_egress_rule" "app_https" {
  for_each = {
    web    = aws_security_group.web.id
    api    = aws_security_group.api.id
    worker = aws_security_group.worker.id
  }
  security_group_id = each.value
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "web_to_api" {
  security_group_id            = aws_security_group.web.id
  referenced_security_group_id = aws_security_group.api.id
  from_port                    = local.api_port
  to_port                      = local.api_port
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "app_to_db" {
  for_each = {
    api    = aws_security_group.api.id
    worker = aws_security_group.worker.id
  }
  security_group_id            = each.value
  referenced_security_group_id = aws_security_group.db.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

# --- Database ------------------------------------------------------------------

resource "aws_db_subnet_group" "this" {
  name       = var.name
  subnet_ids = var.private_subnet_ids
}

resource "aws_db_parameter_group" "this" {
  name   = "${var.name}-pg16"
  family = "postgres16"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "this" {
  identifier                      = var.name
  engine                          = "postgres"
  engine_version                  = "16"
  instance_class                  = var.db_instance_class
  allocated_storage               = var.db_allocated_storage_gb
  max_allocated_storage           = var.db_allocated_storage_gb * 5
  storage_encrypted               = true
  db_name                         = "nimbrix"
  username                        = "nimbrix"
  manage_master_user_password     = true # password lives only in an RDS-managed secret
  db_subnet_group_name            = aws_db_subnet_group.this.name
  vpc_security_group_ids          = [aws_security_group.db.id]
  parameter_group_name            = aws_db_parameter_group.this.name
  publicly_accessible             = false
  multi_az                        = var.db_multi_az
  backup_retention_period         = 14
  copy_tags_to_snapshot           = true
  deletion_protection             = true
  skip_final_snapshot             = false
  final_snapshot_identifier       = "${var.name}-final"
  auto_minor_version_upgrade      = true
  enabled_cloudwatch_logs_exports = ["postgresql"]
  performance_insights_enabled    = true
}

# --- Secrets (values are set out of band, never in Terraform state) -------------------

resource "aws_secretsmanager_secret" "app" {
  for_each    = toset(["demo-password", "llm-api-key"])
  name        = "${var.name}/${each.key}"
  description = "Nimbrix ${each.key}; set the value manually (aws secretsmanager put-secret-value)."
}

# --- Logs, cluster, service discovery -----------------------------------------------

resource "aws_cloudwatch_log_group" "this" {
  for_each          = toset(["api", "worker", "web", "migrate"])
  name              = "/ecs/${var.name}/${each.key}"
  retention_in_days = local.log_retention_days
}

resource "aws_ecs_cluster" "this" {
  name = var.name
  setting {
    name  = "containerInsights"
    value = local.container_insights
  }
}

resource "aws_service_discovery_http_namespace" "this" {
  name = var.name
}
