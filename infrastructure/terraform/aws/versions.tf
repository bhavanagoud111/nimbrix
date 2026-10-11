terraform {
  required_version = ">= 1.9.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  # Configure a remote backend (S3 + DynamoDB/S3 locking) in a backend.tf you keep out of git.
}

provider "aws" {
  region = var.region
  default_tags {
    tags = merge({ Application = "nimbrix", ManagedBy = "terraform" }, var.tags)
  }
}
