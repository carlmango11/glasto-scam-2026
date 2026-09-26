variable "name" { type = string }
variable "region" { type = string }
variable "admin_cidr" { type = string }
variable "instances" { type = number }
variable "instance_type" { type = string }
variable "disk_gb" { type = number }
variable "ssh_public_key" { type = string }
variable "ami_id" {
  type    = string
  default = ""
}
