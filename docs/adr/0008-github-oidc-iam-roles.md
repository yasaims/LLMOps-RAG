# ADR 0008: GitHub Actions は OIDC で AWS を操作し、IAM ロールを 3 本に分割する

## ステータス

Accepted

## コンテキスト

GitHub Actions が Terraform の plan/apply と RAG 品質評価を実行する。長期の AWS アクセスキーを (public リポジトリの) GitHub Secrets に置かないため、GitHub OIDC (`token.actions.githubusercontent.com`) を使う。

論点は 2 つ:

1. OIDC provider・IAM ロールをどの state で管理するか
2. ロールを何本に分け、それぞれにどこまで権限を与えるか

CI が `infra/envs/dev` を管理する以上、CI 用ロールを `envs/dev` 自身に定義すると、初回 apply 時点でロールが存在しない (鶏卵問題)。そのため `infra/bootstrap` ([ADR 0006](0006-lambda-container-http-api.md)) と同様に手動 apply の state に置く。

## 決定

### 配置

- **`infra/bootstrap/github_oidc.tf`** (手動 apply): OIDC provider と `llmops-rag-ci-tf-plan` / `llmops-rag-ci-tf-apply`。**CI からは変更できない**
- **`infra/modules/ci-eval`** (`infra/envs/dev` から呼ぶ・CI 管理): `llmops-rag-dev-eval-ci`。Lambda 実行ロールと同じ ARN (`module.api.bedrock_model_arns`, `module.vector_store.index_arn`) を受け取るため、**eval CI は本番 Lambda が持たない権限では通らない**。provider は `data.aws_iam_openid_connect_provider.github` で参照するのみ

### ロール分割と信頼ポリシー

| ロール | 用途 | trust policy の `sub` (末尾) |
|---|---|---|
| `llmops-rag-ci-tf-plan` | `terraform-plan.yml` | `:pull_request` |
| `llmops-rag-ci-tf-apply` | `terraform-apply.yml` | `:ref:refs/heads/main` |
| `llmops-rag-dev-eval-ci` | `eval.yml` | 上記 2 つ (`StringEquals` に配列を渡すと OR) |

`sub` は **`StringEquals` の完全一致のみ** (ワイルドカード不使用)。plan は PR からしか、apply は main への push からしか assume できず、plan の資格情報が漏れても apply には昇格できない。

#### ⚠️ immutable subject claim

`sub` のリポジトリ部分は **従来形式と immutable 形式の両方を列挙する**。実際に発行されるトークンは次の形式:

```json
{ "sub": "repo:yasaims@148611624/LLMOps-RAG@1332093841:pull_request",
  "repository": "yasaims/LLMOps-RAG" }
```

従来形式 (`repo:yasaims/LLMOps-RAG:...`) だけでは完全一致せず、3 ロールすべてが `AssumeRoleWithWebIdentity` で落ちる。

- 現在値は `gh api repos/<owner>/<repo>/actions/oidc/customization/sub` の `sub_claim_prefix` から `"repo:"` を除いたもの (Terraform では `var.github_repo_immutable`)
- `use_immutable_subject: false` / `use_default: true` でも **`sub_claim_prefix` には既に ID が入っている**。旧形式と判断してはならない
- ID は不変なので、アカウント名・リポジトリ名の変更に追随不要
- ワイルドカードにはせず、`values` のリスト (OR 評価) で両対応する。GitHub 側が既定を戻しても壊れない

### 権限とガードレール

- **plan ロール**: `ReadOnlyAccess` + tfstate の read + S3 native locking (`use_lockfile=true`) 用ロックオブジェクトの read/write。`s3vectors:Get*/List*` は `ReadOnlyAccess` の追随遅れへの保険として明示追加
- **apply ロール**: `${project}-${env}-*` の命名規約でリソースレベルにスコープしたカスタムポリシー 2 本 (`-compute`: Lambda/Logs/API Gateway/ECR/IAM ロール管理、`-data`: S3 Vectors/S3 docs/SNS/CloudWatch アラーム/Budgets)。分割は customer-managed policy の 6,144 文字上限対策。API Gateway (`apigateway:*` on `/apis*`) のみリソースレベル権限が実用的でないため広く許可
- **eval ロール**: Lambda 実行ロールと同一の `bedrock:InvokeModel` (embed FM + chat 推論プロファイル + ルーティング先 FM) と `s3vectors:QueryVectors/GetVectors/GetIndex` のみ。**`PutVectors` は含めない** (取り込みは常にローカル/バッチ)
- **guardrail ポリシー** (plan/apply 共通): CI の自己権限昇格を塞ぐため、`llmops-rag-ci-*` ロール・ポリシーへの `iam:*`、OIDC provider の変更系、`iam:CreateUser` 等によるアクセスキー作成、`organizations:*`/`account:*`、tfstate バケットの `s3:DeleteBucket` を明示 Deny。apply の IAM 権限は `role/llmops-rag-dev-*` にしかスコープされないため、意図を明示する二重化
  - ⚠️ **OIDC provider の Deny を `iam:*OpenIDConnectProvider*` にしない**。`Get`/`List` までマッチし、`data "aws_iam_openid_connect_provider"` が読めず plan/apply が explicit deny で落ちる。Deny は Allow に勝つため `ReadOnlyAccess` でも救えない。変更系 7 アクション (`Create`/`Delete`/`UpdateThumbprint`/`AddClientID`/`RemoveClientID`/`Tag`/`Untag`) だけを列挙する
  - ⚠️ apply ロールは `role/${dev_prefix}-*` にしかスコープされないため、`iam:GetOpenIDConnectProvider` / `iam:ListOpenIDConnectProviders` の Allow を `-compute` に明示している (plan は `ReadOnlyAccess` で足りる)
  - 検証は `aws iam simulate-principal-policy` が早い。ただし `ListOpenIDConnectProviders` はアカウント全体アクションなので、`--resource-arns` に provider ARN を渡すと許可済みでも `implicitDeny` と出る。この 1 件は `--resource-arns` なしで確認する
- ⚠️ **plan が通っても apply の権限は検証されない**。plan は `ReadOnlyAccess` で大半の読み取りを通すが、apply は `${dev_prefix}-*` の列挙式スコープしか持たない。ロールを追加・変更する際は、apply が要求する各アクションを AWS サービス認可リファレンスと突き合わせる:
  - `logs:DescribeLogGroups` など **Resource types が空欄のアクションは `Resource = "*"` でなければ機能しない** (プレフィックス ARN や `log-group:*` でも implicitDeny)。「リスト系は全部同じ」と類推せず、アクションごとに確認する
  - customer-managed policy を作る場合は `policy/${dev_prefix}-*` への権限 (`IamManageDevPolicies`) が必要。`IamManageDevRoles` (`role/...`) では足りない

## 検討した代替案

- **GitHub Environment (`aws-dev`) で `sub` を `environment:aws-dev` にする案**: 承認ゲートとデプロイ履歴は得られるが設定が増え、main マージで自動 apply する方針と合わないため見送り。手動承認が必要になれば拡張できる
- **単一ロールに統合する案**: plan と apply の権限差を作れず、fork からもトリガーされうる PR が apply 相当の権限を持つため不採用

## 影響

- **public リポジトリのリスク**: fork PR でも `sub` は upstream を指す `...:pull_request` で同一となり、trust policy だけでは区別できない。`yasaims` は個人アカウントのため「fork PR ワークフローの承認必須」を無効化する設定項目自体がなく、初回コントリビューターの fork PR は常にメンテナの手動承認待ちになる。万一実行されても plan ロールは read 系、eval ロールは read-only で、実害は Bedrock 課金に限られ、AWS Budgets で検知できる
- IAM ロールの変更は `infra/bootstrap` の手動 apply が必要。**CI が自分自身の権限を変更できないようにするための意図的な制約**
- ⚠️ **trust policy が壊れると CI 経由では直せない**。apply ロールが assume できず `terraform-apply.yml` が動かないため、`infra/bootstrap` と `infra/envs/dev` の**両方**をローカルの管理者資格情報から手動 apply する。bootstrap だけ直しても eval ロールの `sub` が旧いままで、必須チェック `eval` が通らず PR をマージできない
- ⚠️ OIDC provider の `thumbprint_list = []` は AWS が値を補完するため、`terraform plan` に恒常的な差分が出る (AWS は thumbprint を参照しないため無害)。これに依存する `aws_iam_policy_document` が apply 時読み取りに繰り下がり、trust policy と guardrail が `(known after apply)` と表示されるが、中身は同一で実質 no-op
- 詳細なポリシーは `infra/bootstrap/github_oidc.tf` と `infra/modules/ci-eval/main.tf`、全体像は `docs/iam-permissions.md` を参照
