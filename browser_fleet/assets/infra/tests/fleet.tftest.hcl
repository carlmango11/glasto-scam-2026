mock_provider "aws" {
  mock_data "aws_ami" {
    defaults = { id = "ami-0123456789abcdef0" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::test-browser-bundles", id = "test-browser-bundles" }
  }
}

variables {
  name           = "test-fleet"
  region         = "eu-west-2"
  admin_cidr     = "203.0.113.7/32"
  instances      = 2
  instance_type  = "m7i.2xlarge"
  disk_gb        = 100
  ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINotARealKeyForOfflineTesting"
}

run "two_private_desktop_hosts" {
  command = plan
  assert {
    condition     = length(aws_instance.worker) == 2 && length(aws_iam_role.worker) == 2
    error_message = "Every worker needs a separate host and scoped IAM role."
  }
  assert {
    condition     = length(aws_security_group.workers.ingress) == 1 && one(aws_security_group.workers.ingress).from_port == 22 && one(aws_security_group.workers.ingress).to_port == 22
    error_message = "Only SSH may be exposed; desktop ports must remain private."
  }
  assert {
    condition     = aws_instance.worker[0].root_block_device[0].encrypted && aws_instance.worker[0].metadata_options[0].http_tokens == "required"
    error_message = "Workers require encrypted disks and IMDSv2."
  }
  assert {
    condition     = !aws_instance.worker[0].user_data_replace_on_change
    error_message = "Routine bootstrap updates must not replace persistent desktops."
  }
}
