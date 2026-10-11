locals {
  api_image = "${aws_ecr_repository.this["api"].repository_url}:${var.image_tag}"
  web_image = "${aws_ecr_repository.this["web"].repository_url}:${var.image_tag}"
  db_secret = aws_db_instance.this.master_user_secret[0].secret_arn

  api_environment = [
    { name = "NIMBRIX_ENVIRONMENT", value = "production" },
    { name = "NIMBRIX_LOG_LEVEL", value = "INFO" },
    { name = "NIMBRIX_DB_HOST", value = aws_db_instance.this.address },
    { name = "NIMBRIX_DB_PORT", value = tostring(aws_db_instance.this.port) },
    { name = "NIMBRIX_DB_NAME", value = aws_db_instance.this.db_name },
    { name = "NIMBRIX_DB_SSLMODE", value = "require" },
    { name = "NIMBRIX_DEMO_AUTH_ENABLED", value = tostring(var.allow_demo_auth_in_production) },
    { name = "NIMBRIX_ALLOW_DEMO_AUTH_IN_PRODUCTION", value = tostring(var.allow_demo_auth_in_production) },
    { name = "NIMBRIX_AWS_ENABLED", value = tostring(var.aws_read_access_enabled) },
    { name = "NIMBRIX_LIVE_ACTIONS_ENABLED", value = "false" },
    { name = "FORWARDED_ALLOW_IPS", value = "*" }, # reachable only from the web security group
  ]
  api_secrets = [
    { name = "NIMBRIX_DB_USER", valueFrom = "${local.db_secret}:username::" },
    { name = "NIMBRIX_DB_PASSWORD", valueFrom = "${local.db_secret}:password::" },
  ]

  log_config = { for k in ["api", "worker", "web", "migrate"] : k => {
    logDriver = "awslogs"
    options = {
      awslogs-group         = aws_cloudwatch_log_group.this[k].name
      awslogs-region        = var.region
      awslogs-stream-prefix = k
    }
  } }
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.runtime.arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([{
    name             = "api"
    image            = local.api_image
    essential        = true
    portMappings     = [{ name = "api", containerPort = local.api_port, protocol = "tcp" }]
    environment      = local.api_environment
    secrets          = local.api_secrets
    logConfiguration = local.log_config["api"]
  }])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.runtime.arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([{
    name             = "worker"
    image            = local.api_image
    essential        = true
    command          = ["nimbrix-worker"]
    environment      = local.api_environment
    secrets          = local.api_secrets
    logConfiguration = local.log_config["worker"]
  }])
}

# One-off: run explicitly before deploying a new API version (see docs/deployment.md).
resource "aws_ecs_task_definition" "migrate" {
  family                   = "${var.name}-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.runtime.arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([{
    name             = "migrate"
    image            = local.api_image
    essential        = true
    command          = ["alembic", "-c", "/app/alembic.ini", "upgrade", "head"]
    environment      = local.api_environment
    secrets          = local.api_secrets
    logConfiguration = local.log_config["migrate"]
  }])
}

resource "aws_ecs_task_definition" "web" {
  family                   = "${var.name}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([{
    name             = "web"
    image            = local.web_image
    essential        = true
    portMappings     = [{ name = "web", containerPort = local.web_port, protocol = "tcp" }]
    environment      = [{ name = "NIMBRIX_API_INTERNAL_URL", value = "http://api:${local.api_port}" }]
    logConfiguration = local.log_config["web"]
  }])
}

resource "aws_ecs_service" "api" {
  name            = "api"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = false
  }
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn
    service {
      port_name = "api"
      client_alias {
        dns_name = "api"
        port     = local.api_port
      }
    }
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = false
  }
}

resource "aws_ecs_service" "web" {
  name            = "web"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = var.web_desired_count
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.web.id]
    assign_public_ip = false
  }
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = local.web_port
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  depends_on = [aws_lb_listener.https]
}
