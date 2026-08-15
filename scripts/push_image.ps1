# Lambda 用コンテナイメージを build して ECR へ push する。
# Phase 3 で GitHub Actions に移植する前提で、コマンドを素直に並べているだけの薄いスクリプト。
#
# 前提: infra/bootstrap を apply 済みで ECR リポジトリが存在すること。
#
# 使い方:
#   .\scripts\push_image.ps1 [-Region ap-northeast-1] [-RepoName llmops-rag-api]

param(
    [string]$Region = "ap-northeast-1",
    [string]$RepoName = "llmops-rag-api"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$AccountId = (aws sts get-caller-identity --query Account --output text)
if (-not $AccountId) { throw "AWS 認証情報を取得できませんでした (aws sts get-caller-identity 失敗)" }

$RegistryUri = "$AccountId.dkr.ecr.$Region.amazonaws.com"
$ImageUri = "$RegistryUri/$RepoName"
$GitSha = (git rev-parse --short HEAD)

Write-Host "== 1/4: requirements.txt を生成 (uv export) =="
uv export --no-dev --no-emit-project --frozen -o requirements.txt

Write-Host "== 2/4: ECR にログイン =="
aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $RegistryUri

Write-Host "== 3/4: docker build (linux/amd64) =="
docker build --platform linux/amd64 -t "${ImageUri}:$GitSha" -t "${ImageUri}:latest" .

Write-Host "== 4/4: push =="
docker push "${ImageUri}:$GitSha"
docker push "${ImageUri}:latest"

Write-Host ""
Write-Host "Image URI: ${ImageUri}:$GitSha"
Write-Host "この値を infra/envs/dev/terraform.tfvars の image_uri に設定してください。"
