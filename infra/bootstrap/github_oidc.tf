# GitHub Actions が長期アクセスキーなしで AWS を操作するための OIDC provider + IAM ロール
# (Phase 3, ADR 0008)。
#
# CI が infra/envs/dev を管理する以上、そのためのロールを envs/dev 自身の中には置けない
# (鶏卵問題: 初回 apply ができない)。tfstate バケット・ECR と同じくここで一度きり手動 apply
# する。ロールは 2 本、命名は llmops-rag-ci-* (このファイルでのみ管理・CI からは変更不可):
#   - llmops-rag-ci-tf-plan  : PR からのみ assume 可能。terraform plan 専用の読み取り権限
#   - llmops-rag-ci-tf-apply : main への push からのみ assume 可能。envs/dev のスタック全体
#                              (Lambda/API Gateway/S3 Vectors/SNS/CloudWatch/Budgets/ECR) の
#                              作成・更新権限
#
# 「CI が自分自身に権限を昇格させる」経路を防ぐため、両ロールに guardrail ポリシーを付け、
# llmops-rag-ci-* ロール自体の変更と OIDC provider の改変を明示 Deny する。
# eval 用の read-only ロール (llmops-rag-dev-eval-ci) は Lambda 実行ロールの ARN をそのまま
# 再利用できる infra/envs/dev 側 (infra/modules/ci-eval) に置く — 詳細は ADR 0008 参照。

resource "aws_iam_openid_connect_provider" "github_actions" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # thumbprint_list は AWS provider v5 以降 Optional。AWS 側が主要クラウド事業者のルート CA を
  # 内部で信頼するようになったため、GitHub の中間証明書のローテーションを追随する必要がない。
  thumbprint_list = []
}

locals {
  ci_tf_plan_role_name  = "llmops-rag-ci-tf-plan"
  ci_tf_apply_role_name = "llmops-rag-ci-tf-apply"
  dev_prefix            = "${var.project}-${var.env}"
  tfstate_key           = "envs/${var.env}/terraform.tfstate"
}

# --- 信頼ポリシー: sub は完全一致のみ (ワイルドカードは使わない) -----------
#
# ⚠️ sub は「従来形式」と「immutable subject claim 形式」の両方を列挙する。GitHub は sub に
#    アカウント ID / リポジトリ ID を埋め込む形式へ既定を切り替えており、実際のトークンは
#    repo:yasaims@148611624/LLMOps-RAG@1332093841:... で発行される (詳細は ADR 0008)。
#    StringEquals の values はリストなら OR 評価になるので、`repo:owner@*/repo@*` のような
#    ワイルドカードに緩めることなく両対応できる。GitHub 側が既定を戻しても壊れない。
#    現在の値は `gh api repos/<owner>/<repo>/actions/oidc/customization/sub` で確認できる。

data "aws_iam_policy_document" "assume_ci_tf_plan" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github_actions.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repo}:pull_request",
        "repo:${var.github_repo_immutable}:pull_request",
      ]
    }
  }
}

data "aws_iam_policy_document" "assume_ci_tf_apply" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github_actions.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repo}:ref:refs/heads/main",
        "repo:${var.github_repo_immutable}:ref:refs/heads/main",
      ]
    }
  }
}

resource "aws_iam_role" "ci_tf_plan" {
  name               = local.ci_tf_plan_role_name
  assume_role_policy = data.aws_iam_policy_document.assume_ci_tf_plan.json
}

resource "aws_iam_role" "ci_tf_apply" {
  name               = local.ci_tf_apply_role_name
  assume_role_policy = data.aws_iam_policy_document.assume_ci_tf_apply.json
}

# --- guardrail (plan/apply 共通): 自己権限昇格の防止 ------------------------

data "aws_iam_policy_document" "ci_guardrail" {
  statement {
    sid     = "DenySelfPrivilegeEscalation"
    effect  = "Deny"
    actions = ["iam:*"]
    resources = [
      aws_iam_role.ci_tf_plan.arn,
      aws_iam_role.ci_tf_apply.arn,
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/llmops-rag-ci-*",
    ]
  }
  statement {
    # ⚠️ ここを `iam:*OpenIDConnectProvider*` の 1 行にしてはいけない。ワイルドカードが
    #    GetOpenIDConnectProvider / ListOpenIDConnectProviders / ListOpenIDConnectProviderTags
    #    にもマッチしてしまい、infra/envs/dev の
    #    `data "aws_iam_openid_connect_provider" "github"` が読めなくなって
    #    plan / apply が AccessDenied (explicit deny) で失敗する。
    #    guardrail の意図は「改ざんの防止」であって「読み取りの禁止」ではないので、
    #    変更系アクションだけを列挙する。Deny は Allow より強いため、
    #    ReadOnlyAccess 側で読み取りを許可しても上書きできない点に注意。
    sid    = "DenyOidcProviderTampering"
    effect = "Deny"
    actions = [
      "iam:CreateOpenIDConnectProvider",
      "iam:DeleteOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint",
      "iam:AddClientIDToOpenIDConnectProvider",
      "iam:RemoveClientIDFromOpenIDConnectProvider",
      "iam:TagOpenIDConnectProvider",
      "iam:UntagOpenIDConnectProvider",
    ]
    resources = ["*"]
  }
  statement {
    sid    = "DenyAccountAndUserEscalation"
    effect = "Deny"
    actions = [
      "iam:CreateUser",
      "iam:CreateAccessKey",
      "iam:AttachUserPolicy",
      "iam:PutUserPolicy",
      "organizations:*",
      "account:*",
    ]
    resources = ["*"]
  }
  statement {
    sid       = "DenyTfstateBucketDeletion"
    effect    = "Deny"
    actions   = ["s3:DeleteBucket"]
    resources = [aws_s3_bucket.tfstate.arn]
  }
}

resource "aws_iam_policy" "ci_guardrail" {
  name   = "llmops-rag-ci-guardrail"
  policy = data.aws_iam_policy_document.ci_guardrail.json
}

resource "aws_iam_role_policy_attachment" "plan_guardrail" {
  role       = aws_iam_role.ci_tf_plan.name
  policy_arn = aws_iam_policy.ci_guardrail.arn
}

resource "aws_iam_role_policy_attachment" "apply_guardrail" {
  role       = aws_iam_role.ci_tf_apply.name
  policy_arn = aws_iam_policy.ci_guardrail.arn
}

# --- plan ロール: ReadOnlyAccess + tfstate の読み取り + ロックオブジェクトの読み書き -----

resource "aws_iam_role_policy_attachment" "plan_readonly" {
  role       = aws_iam_role.ci_tf_plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "plan_state" {
  statement {
    sid       = "TfstateList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.tfstate.arn]
  }
  statement {
    sid       = "TfstateRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.tfstate.arn}/${local.tfstate_key}"]
  }
  statement {
    # terraform init -backend-config=... の use_lockfile=true が使う S3 native locking 用。
    sid       = "TfstateLock"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.tfstate.arn}/${local.tfstate_key}.tflock"]
  }
  statement {
    # s3vectors は新しいサービスのため ReadOnlyAccess が未追随の場合の保険。
    sid       = "S3VectorsReadOnlyFallback"
    actions   = ["s3vectors:Get*", "s3vectors:List*"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "plan_state" {
  name   = "llmops-rag-ci-tf-plan-state"
  policy = data.aws_iam_policy_document.plan_state.json
}

resource "aws_iam_role_policy_attachment" "plan_state" {
  role       = aws_iam_role.ci_tf_plan.name
  policy_arn = aws_iam_policy.plan_state.arn
}

# --- apply ロール: tfstate の読み書き + envs/dev スタックの管理権限 --------

data "aws_iam_policy_document" "apply_state" {
  statement {
    sid       = "TfstateList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.tfstate.arn]
  }
  statement {
    sid       = "TfstateReadWrite"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.tfstate.arn}/${local.tfstate_key}"]
  }
  statement {
    sid       = "TfstateLock"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.tfstate.arn}/${local.tfstate_key}.tflock"]
  }
}

resource "aws_iam_policy" "apply_state" {
  name   = "llmops-rag-ci-tf-apply-state"
  policy = data.aws_iam_policy_document.apply_state.json
}

resource "aws_iam_role_policy_attachment" "apply_state" {
  role       = aws_iam_role.ci_tf_apply.name
  policy_arn = aws_iam_policy.apply_state.arn
}

# コンピュート系 (Lambda / CloudWatch Logs / API Gateway / ECR / IAM ロール管理) と
# データ・課金系 (S3 Vectors / S3 docs バケット / SNS / CloudWatch アラーム / Budgets) を
# 分けているのは機能的な理由ではなく、customer-managed policy の 6,144 文字上限に余裕を
# 持たせるため。

data "aws_iam_policy_document" "apply_stack_compute" {
  statement {
    sid       = "LambdaManage"
    actions   = ["lambda:*"]
    resources = ["arn:aws:lambda:${var.region}:${data.aws_caller_identity.current.account_id}:function:${local.dev_prefix}-*"]
  }
  statement {
    sid       = "LambdaAccountLevel"
    actions   = ["lambda:GetAccountSettings"]
    resources = ["*"]
  }
  statement {
    sid     = "LogsManage"
    actions = ["logs:*"]
    resources = [
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.dev_prefix}-*",
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.dev_prefix}-*:*",
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/apigateway/${local.dev_prefix}-*",
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/apigateway/${local.dev_prefix}-*:*",
    ]
  }
  statement {
    # HTTP API はリソースレベル権限が実用的でないため広めに許可する。guardrail 側の Deny は
    # 適用されないので、apigateway の乱用は Budgets/CloudWatch アラームでの検知に頼る。
    sid       = "ApiGatewayManage"
    actions   = ["apigateway:*"]
    resources = ["arn:aws:apigateway:${var.region}::/apis*"]
  }
  statement {
    sid       = "EcrManage"
    actions   = ["ecr:*"]
    resources = ["arn:aws:ecr:${var.region}:${data.aws_caller_identity.current.account_id}:repository/${var.ecr_repo_name}"]
  }
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    # Lambda 実行ロール (llmops-rag-dev-*-role) の管理。llmops-rag-ci-* は guardrail が
    # 明示 Deny するため、ここで role/${dev_prefix}-* に限定しても自己権限昇格の経路にはならない。
    sid = "IamManageDevRoles"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:GetRole",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:PassRole",
    ]
    resources = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.dev_prefix}-*"]
  }
  statement {
    # infra/envs/dev の `data "aws_iam_openid_connect_provider" "github"` が
    # module.ci_eval に渡す ARN を解決するために使う (List → Get の順に呼ばれる)。
    # plan ロールは ReadOnlyAccess で賄えるが、apply ロールの IAM 権限は
    # role/${dev_prefix}-* にしかスコープされていないため、ここで明示的に許可する。
    # 読み取り専用なので guardrail の「改ざん防止」の意図とは競合しない。
    sid       = "IamReadOidcProvider"
    actions   = ["iam:GetOpenIDConnectProvider", "iam:ListOpenIDConnectProviders"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "apply_stack_compute" {
  name   = "llmops-rag-ci-tf-apply-compute"
  policy = data.aws_iam_policy_document.apply_stack_compute.json
}

resource "aws_iam_role_policy_attachment" "apply_stack_compute" {
  role       = aws_iam_role.ci_tf_apply.name
  policy_arn = aws_iam_policy.apply_stack_compute.arn
}

data "aws_iam_policy_document" "apply_stack_data" {
  statement {
    sid     = "S3VectorsManage"
    actions = ["s3vectors:*"]
    resources = [
      "arn:aws:s3vectors:${var.region}:${data.aws_caller_identity.current.account_id}:bucket/${local.dev_prefix}-*",
      "arn:aws:s3vectors:${var.region}:${data.aws_caller_identity.current.account_id}:bucket/${local.dev_prefix}-*/index/*",
    ]
  }
  statement {
    sid       = "S3VectorsAccountLevel"
    actions   = ["s3vectors:ListVectorBuckets", "s3vectors:CreateVectorBucket"]
    resources = ["*"]
  }
  statement {
    sid     = "DocsBucketManage"
    actions = ["s3:*"]
    resources = [
      "arn:aws:s3:::${local.dev_prefix}-docs-*",
      "arn:aws:s3:::${local.dev_prefix}-docs-*/*",
    ]
  }
  statement {
    sid       = "S3AccountLevel"
    actions   = ["s3:ListAllMyBuckets", "s3:GetBucketLocation"]
    resources = ["*"]
  }
  statement {
    sid       = "SnsManage"
    actions   = ["sns:*"]
    resources = ["arn:aws:sns:${var.region}:${data.aws_caller_identity.current.account_id}:${local.dev_prefix}-*"]
  }
  statement {
    sid = "CloudWatchAlarmsManage"
    actions = [
      "cloudwatch:PutMetricAlarm",
      "cloudwatch:DeleteAlarms",
      "cloudwatch:DescribeAlarms",
      "cloudwatch:TagResource",
      "cloudwatch:UntagResource",
      "cloudwatch:ListTagsForResource",
    ]
    resources = ["arn:aws:cloudwatch:${var.region}:${data.aws_caller_identity.current.account_id}:alarm:${local.dev_prefix}-*"]
  }
  statement {
    sid       = "BudgetsManage"
    actions   = ["budgets:*"]
    resources = ["arn:aws:budgets::${data.aws_caller_identity.current.account_id}:budget/${local.dev_prefix}-*"]
  }
  statement {
    # terraform 自体は Bedrock リソースを作らないが、推論プロファイル ID の妥当性を
    # plan/apply 時に確認できるよう読み取りのみ許可する。
    sid       = "BedrockDescribe"
    actions   = ["bedrock:GetInferenceProfile", "bedrock:ListInferenceProfiles", "bedrock:GetFoundationModel"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "apply_stack_data" {
  name   = "llmops-rag-ci-tf-apply-data"
  policy = data.aws_iam_policy_document.apply_stack_data.json
}

resource "aws_iam_role_policy_attachment" "apply_stack_data" {
  role       = aws_iam_role.ci_tf_apply.name
  policy_arn = aws_iam_policy.apply_stack_data.arn
}
