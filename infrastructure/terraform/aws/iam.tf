data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execution role: pull images, write logs, inject secrets at task start.
resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = concat(
      [for s in aws_secretsmanager_secret.app : s.arn],
      [aws_db_instance.this.master_user_secret[0].secret_arn],
    )
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "read-app-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

# Runtime (task) role: no AWS permissions unless read-only customer access is enabled.
resource "aws_iam_role" "runtime" {
  name               = "${var.name}-runtime"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "assume_customer_readonly" {
  statement {
    actions   = ["sts:AssumeRole"]
    resources = ["arn:aws:iam::*:role/NimbrixReadOnly"]
  }
}

resource "aws_iam_role_policy" "assume_customer_readonly" {
  count  = var.aws_read_access_enabled ? 1 : 0
  name   = "assume-customer-readonly"
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.assume_customer_readonly.json
}
