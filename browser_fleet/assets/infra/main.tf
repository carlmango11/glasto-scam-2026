terraform {
  required_version = ">= 1.7.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "browser-fleet", Fleet = var.name }
  }
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_vpc" "fleet" {
  cidr_block           = "10.80.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = var.name }
}
resource "aws_subnet" "workers" {
  vpc_id                  = aws_vpc.fleet.id
  cidr_block              = "10.80.1.0/24"
  map_public_ip_on_launch = true
}
resource "aws_internet_gateway" "fleet" {
  vpc_id = aws_vpc.fleet.id
}
resource "aws_route_table" "workers" {
  vpc_id = aws_vpc.fleet.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.fleet.id
  }
}
resource "aws_route_table_association" "workers" {
  subnet_id      = aws_subnet.workers.id
  route_table_id = aws_route_table.workers.id
}

resource "aws_security_group" "workers" {
  name_prefix = "${var.name}-"
  vpc_id      = aws_vpc.fleet.id
  ingress {
    description = "Operator SSH tunnels only"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.admin_cidr]
  }
  egress {
    description = "Host bootstrap and proxy gateway transport; browsers are isolated by nftables"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_s3_bucket" "bundles" {
  bucket_prefix = "${var.name}-bundles-"
  force_destroy = true
}
resource "aws_s3_bucket_public_access_block" "bundles" {
  bucket                  = aws_s3_bucket.bundles.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "bundles" {
  bucket = aws_s3_bucket.bundles.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
resource "aws_s3_bucket_policy" "bundles" {
  bucket = aws_s3_bucket.bundles.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "RequireTLS", Effect = "Deny", Principal = "*", Action = "s3:*"
      Resource  = [aws_s3_bucket.bundles.arn, "${aws_s3_bucket.bundles.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

resource "aws_key_pair" "operator" {
  key_name_prefix = "${var.name}-"
  public_key      = var.ssh_public_key
}
resource "aws_iam_role" "worker" {
  count       = var.instances
  name_prefix = "${var.name}-${count.index + 1}-"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}
resource "aws_iam_role_policy" "bundle" {
  count = var.instances
  role  = aws_iam_role.worker[count.index].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow", Action = ["s3:GetObject"]
      Resource = "${aws_s3_bucket.bundles.arn}/worker-${format("%03d", count.index + 1)}.tar.gz"
    }]
  })
}
resource "aws_iam_instance_profile" "worker" {
  count       = var.instances
  name_prefix = "${var.name}-${count.index + 1}-"
  role        = aws_iam_role.worker[count.index].name
}
resource "aws_instance" "worker" {
  count                       = var.instances
  ami                         = var.ami_id != "" ? var.ami_id : data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.workers.id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.workers.id]
  key_name                    = aws_key_pair.operator.key_name
  iam_instance_profile        = aws_iam_instance_profile.worker[count.index].name
  user_data_replace_on_change = false
  user_data = templatefile("${path.module}/bootstrap.sh.tftpl", {
    bucket = aws_s3_bucket.bundles.id
    key    = "worker-${format("%03d", count.index + 1)}.tar.gz"
    region = var.region
  })
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  root_block_device {
    encrypted   = true
    volume_type = "gp3"
    volume_size = var.disk_gb
  }
  tags       = { Name = "${var.name}-worker-${format("%03d", count.index + 1)}" }
  depends_on = [aws_route_table_association.workers, aws_iam_role_policy.bundle]
  lifecycle {
    # Routine tool/AMI updates must not replace disks containing saved profiles.
    # Use the CLI repair command to apply runtime updates explicitly.
    ignore_changes = [ami, user_data]
  }
}

output "bucket" { value = aws_s3_bucket.bundles.id }
output "workers" {
  value = [for index, worker in aws_instance.worker : {
    index = index + 1, id = worker.id, public_ip = worker.public_ip
  }]
}
