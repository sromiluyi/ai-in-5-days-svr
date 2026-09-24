# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

locals {
  # Enabled first, through the api_bootstrap provider: the provider needs both
  # to manage anything else in the project.
  bootstrap_services = [
    "serviceusage.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ]

  services = [
    "secretmanager.googleapis.com",
    "aiplatform.googleapis.com",
    "logging.googleapis.com",
    "iam.googleapis.com",
  ]
}

# Enabled through google.api_bootstrap so the Service Usage call is billed to
# the caller, not to the target project whose Service Usage API is still off.
resource "google_project_service" "bootstrap" {
  provider = google.api_bootstrap
  for_each = toset(local.bootstrap_services)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# Application services required for Secret Manager and Gemini execution
resource "google_project_service" "services" {
  for_each = toset(local.services)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false

  depends_on = [google_project_service.bootstrap]
}
