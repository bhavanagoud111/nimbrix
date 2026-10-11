output "url" {
  value = "https://${aws_lb.this.dns_name}"
}

output "ecr_repositories" {
  value = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "db_endpoint" {
  value = aws_db_instance.this.address
}

output "secrets_to_populate" {
  description = "Set these values manually; Terraform never stores them."
  value       = { for k, s in aws_secretsmanager_secret.app : k => s.arn }
}

output "migrate_task_definition" {
  value = aws_ecs_task_definition.migrate.arn
}

output "account_id" {
  value = data.aws_caller_identity.current.account_id
}
