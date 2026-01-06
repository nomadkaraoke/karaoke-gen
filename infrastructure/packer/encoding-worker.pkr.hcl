# Packer template for encoding worker GCE image
#
# This image pre-installs all dependencies to reduce VM startup time from
# ~10 minutes (building Python from source) to ~30 seconds (just start service).
#
# Usage:
#   cd infrastructure/packer
#   packer init encoding-worker.pkr.hcl
#   packer build encoding-worker.pkr.hcl

packer {
  required_plugins {
    googlecompute = {
      source  = "github.com/hashicorp/googlecompute"
      version = "~> 1.1"
    }
  }
}

variable "project_id" {
  type        = string
  default     = "nomadkaraoke"
  description = "GCP project ID"
}

variable "zone" {
  type        = string
  default     = "us-central1-a"
  description = "GCE zone for building the image"
}

variable "python_version" {
  type        = string
  default     = "3.13.1"
  description = "Python version to install from source"
}

source "googlecompute" "encoding-worker" {
  project_id             = var.project_id
  zone                   = var.zone
  source_image_family    = "debian-12"
  source_image_project_id = ["debian-cloud"]

  # Use n2 machine type for building (c4 requires hyperdisk which isn't supported by Packer)
  # The resulting image will work on c4-standard-8 in production
  machine_type = "n2-standard-8"
  disk_size    = 100
  disk_type    = "pd-ssd" # Use SSD for faster builds

  # Image naming - uses timestamp for versioning, family for latest
  image_name        = "encoding-worker-{{timestamp}}"
  image_family      = "encoding-worker"
  image_description = "Encoding worker with Python ${var.python_version}, FFmpeg 7.x, and fonts pre-installed"

  ssh_username = "packer"

  # Tags for firewall rules during build (if needed)
  tags = ["packer-build"]

  # Metadata for build tracking
  image_labels = {
    "managed-by" = "packer"
    "python"     = replace(var.python_version, ".", "-")
  }
}

build {
  sources = ["source.googlecompute.encoding-worker"]

  # Upload provisioning script
  provisioner "file" {
    source      = "scripts/provision.sh"
    destination = "/tmp/provision.sh"
  }

  # Run provisioning
  provisioner "shell" {
    inline = [
      "chmod +x /tmp/provision.sh",
      "sudo PYTHON_VERSION=${var.python_version} /tmp/provision.sh"
    ]
  }

  # Clean up for smaller image
  provisioner "shell" {
    inline = [
      "sudo apt-get clean",
      "sudo rm -rf /var/lib/apt/lists/*",
      "sudo rm -rf /tmp/*",
      "sudo rm -rf /var/tmp/*",
      "sudo journalctl --vacuum-time=1d"
    ]
  }
}
