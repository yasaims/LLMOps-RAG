# eval CI (Phase 3, ADR 0007/0008) 用の IAM ロール。
#
# PR ごとに GitHub Actions が本番 S3 Vectors インデックスに対して評価用の retrieve/generate を
# 直接実行する (evals/run_eval.py)。権限は infra/modules/api が Lambda 実行ロールに与えている
# ものと完全に同じ ARN を参照する (var.bedrock_model_arns / var.vector_index_arn は
# infra/envs/dev/main.tf 経由で module.api / module.vector_store の出力をそのまま渡す)。
# これにより「eval CI は本番 Lambda が持たない権限では絶対に通らない」ことが構造的に保証される。
# PutVectors は含めない — 取り込みは Phase 2 の方針どおり常にローカル/バッチから行う。

locals {
  role_name = "${var.project}-${var.env}-eval-ci"
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      # eval は PR (通常経路) と main への直接 push (workflow_dispatch フォールバック) の
      # どちらからも実行できるようにする。terraform-plan/apply ロールと異なりインフラを
      # 変更する権限を持たないため、対象を広げても影響は Bedrock/S3Vectors の読み取りのみ。
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repo}:pull_request",
        "repo:${var.github_repo}:ref:refs/heads/main",
      ]
    }
  }
}

resource "aws_iam_role" "eval_ci" {
  name               = local.role_name
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "eval_ci" {
  statement {
    sid       = "BedrockInvoke"
    actions   = ["bedrock:InvokeModel"]
    resources = var.bedrock_model_arns
  }
  statement {
    sid       = "S3VectorsQuery"
    actions   = ["s3vectors:QueryVectors", "s3vectors:GetVectors", "s3vectors:GetIndex"]
    resources = [var.vector_index_arn]
  }
}

resource "aws_iam_policy" "eval_ci" {
  name   = "${local.role_name}-policy"
  policy = data.aws_iam_policy_document.eval_ci.json
}

resource "aws_iam_role_policy_attachment" "eval_ci" {
  role       = aws_iam_role.eval_ci.name
  policy_arn = aws_iam_policy.eval_ci.arn
}
