# 値は init 時に -backend-config=backend.hcl で渡す (bootstrap 適用後の
# tfstate バケット名に依存するため、ここではハードコードしない)。
#   terraform -chdir=infra/envs/dev init -backend-config=backend.hcl
terraform {
  backend "s3" {}
}
