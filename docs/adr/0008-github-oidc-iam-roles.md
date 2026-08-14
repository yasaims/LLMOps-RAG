# ADR 0008: GitHub Actions は OIDC で AWS を操作し、IAM ロールを 3 本に分割する

## ステータス

Accepted

## コンテキスト

Phase 3 で GitHub Actions が Terraform の plan/apply と RAG 品質評価を実行するようになる
ため、長期の AWS アクセスキーを (public リポジトリの) GitHub Secrets に置かずに済む
GitHub OIDC (`token.actions.githubusercontent.com`) を使う。

論点は 2 つ:

1. OIDC provider・IAM ロールをどこで Terraform 管理するか
2. ロールを何本に分け、それぞれどこまでの権限を持たせるか

CI が `infra/envs/dev` を管理する以上、そのためのロールを `envs/dev` 自身の中に定義する
ことはできない (鶏卵問題: 初回 apply の時点でそのロールがまだ存在しない)。
`infra/bootstrap` (tfstate 用 S3 + ECR を一度きり手動 apply する state。ADR 0006) と
同じ位置づけで扱うのが自然と判断した。

## 決定

### 配置

- **`infra/bootstrap/github_oidc.tf`** (新規, 手動 apply): OIDC provider +
  `llmops-rag-ci-tf-plan` / `llmops-rag-ci-tf-apply` の 2 ロール。この 2 つは
  bootstrap state 側だけが管理し、**CI からは変更できない**
- **`infra/modules/ci-eval`** (新規, `infra/envs/dev` から呼ぶ・CI が管理):
  `llmops-rag-dev-eval-ci` ロール。`infra/modules/api` が Lambda 実行ロールに
  与えている Bedrock/S3Vectors 権限 ARN (`module.api.bedrock_model_arns`,
  `module.vector_store.index_arn`) をそのまま渡すことで、**eval CI は本番 Lambda が
  持たない権限では絶対に通らない**ことを構造的に保証している。`envs/dev` 側は
  `data.aws_iam_openid_connect_provider.github` で bootstrap 側が作った provider を
  参照するだけで、provider 自体の管理には関与しない

### ロール分割と信頼ポリシー

| ロール | 用途 | trust policy の `sub` (末尾) |
|---|---|---|
| `llmops-rag-ci-tf-plan` | `terraform-plan.yml` | `:pull_request` |
| `llmops-rag-ci-tf-apply` | `terraform-apply.yml` | `:ref:refs/heads/main` |
| `llmops-rag-dev-eval-ci` | `eval.yml` | 上記 2 つ (`StringEquals` に配列を渡すと OR 条件になる) |

`sub` はすべて `StringEquals` の完全一致にし、ワイルドカードは使わない。plan は PR から
しか assume できず、apply は main への push からしかできない — plan 用の資格情報が
漏れても apply (実際にインフラを変更する権限) には昇格できない。

#### ⚠️ immutable subject claim (2026-08 に判明)

`sub` のリポジトリ部分は **従来形式と immutable 形式の両方を列挙する**。GitHub は `sub` に
アカウント ID / リポジトリ ID を埋め込む形式へ既定を切り替えており、実際に発行される
トークンは以下になる:

```json
{ "sub": "repo:yasaims@148611624/LLMOps-RAG@1332093841:pull_request",
  "repository": "yasaims/LLMOps-RAG" }
```

`repo:yasaims/LLMOps-RAG:pull_request` だけを `StringEquals` にすると完全一致せず、
`Not authorized to perform sts:AssumeRoleWithWebIdentity` で 3 ロールすべてが落ちる。
Phase 3 の CI が当初一度も通らなかったのはこれが原因だった。

- 現在の値は `gh api repos/<owner>/<repo>/actions/oidc/customization/sub` の
  `sub_claim_prefix` から `"repo:"` を除いた文字列。Terraform 側は
  `var.github_repo_immutable` として持つ
- ⚠️ このリポジトリの応答は `use_immutable_subject: false` かつ `use_default: true` だが、
  **`sub_claim_prefix` には既に ID が入っている**。「オプトインしていないから旧形式のはず」
  という判断は誤りで、既定のプレフィックス自体が新形式になっている
- ID は不変なので、アカウント名やリポジトリ名を変更しても追随不要
- `repo:yasaims@*/LLMOps-RAG@*:...` のようなワイルドカードには**しない**。
  `StringEquals` の `values` はリストなら OR 評価されるため、完全一致のまま両対応できる。
  GitHub 側が既定を戻した場合にも壊れない

### 権限とガードレール

- **plan ロール**: AWS 管理ポリシー `ReadOnlyAccess` + tfstate オブジェクトの read +
  S3 native locking (`use_lockfile=true`) 用のロックオブジェクトへの read/write のみ
  (`ReadOnlyAccess` は書き込み系アクションを含まないため、ロック取得だけは別途許可する
  必要がある)。`s3vectors:Get*/List*` も明示追加している (新しいサービスのため
  `ReadOnlyAccess` の追随が遅れる可能性への保険)
- **apply ロール**: `${project}-${env}-*` の命名規約でリソースレベルにスコープした
  カスタムポリシーを 2 本 (`-compute`: Lambda/Logs/API Gateway/ECR/IAM ロール管理、
  `-data`: S3 Vectors/S3 docs バケット/SNS/CloudWatch アラーム/Budgets) にアタッチする。
  1 本に統合しなかったのは customer-managed policy の 6,144 文字上限に余裕を持たせる
  ためで、機能的な意味はない。API Gateway (`apigateway:*` on `/apis*`) だけは
  リソースレベル権限が実用的でないため広めに許可している
- **guardrail ポリシー** (plan/apply 共通アタッチ): 「CI が自分自身に権限を昇格させる」
  経路を構造的に塞ぐため、`llmops-rag-ci-*` ロール・ポリシー自体への `iam:*` と
  OIDC provider への `iam:*OpenIDConnectProvider*` を明示 Deny する。加えて
  `iam:CreateUser` 等によるアクセスキー作成、`organizations:*`/`account:*`、
  tfstate バケットの `s3:DeleteBucket` も Deny している。apply ロールの IAM 権限は
  `role/llmops-rag-dev-*` にしかスコープしていないため、guardrail がなくても
  `llmops-rag-ci-*` 自体は触れないが、「意図を明示するコード」として二重に残している
- **eval ロール**: Lambda 実行ロールと同一の `bedrock:InvokeModel` (embed FM + chat
  推論プロファイル + chat 推論プロファイルのルーティング先 FM) と
  `s3vectors:QueryVectors/GetVectors/GetIndex` のみ。`PutVectors` は含めない
  (Phase 2 の方針どおり、取り込みは常にローカル/バッチから行う)

## 検討した代替案

- **GitHub Environment (`aws-dev`) を使い `sub` を `environment:aws-dev` にする案**:
  承認ゲート・デプロイ履歴が GitHub 側に残るメリットがあるが、設定項目が増える。
  計画書の「main マージで apply」という自動デプロイの意図とも合わないため、
  Phase 3 では見送った (Phase 4 で手動承認を挟みたくなった場合の拡張余地として残す)
- **単一ロールに統合する案**: plan と apply で権限差を作れなくなり、PR (fork からでも
  トリガーされうる) が実質的に apply 相当の権限を持ってしまうため不採用

## 影響

- **public リポジトリであることのリスク**: fork からの PR でも OIDC トークンの `sub`
  は同じ (upstream リポジトリを指す `...:pull_request`) になり、trust policy だけでは
  区別できない。`yasaims` は Organization ではなく個人アカウントのため、GitHub 側の
  「fork PR ワークフローに承認を必須にする」設定は無効化できる項目としてそもそも
  存在せず、初回コントリビューターの fork PR ワークフローは常にメンテナの手動承認
  待ちになる (2026-08 時点で確認済み)。万一実行されても plan ロールは
  `ReadOnlyAccess` 相当、eval ロールは read-only のみで、実害は Bedrock 課金に
  限られる (AWS Budgets で検知可能)
- IAM ロールの変更は `infra/bootstrap` の手動 apply が必要 (自動化しない = 意図的。
  CI が自分自身の権限を変更できてしまうことを避けるため)
- ⚠️ **trust policy が壊れると CI 経由では直せない (鶏卵)**。apply ロール自身が assume
  できないので `terraform-apply.yml` が動かず、`llmops-rag-dev-eval-ci` を持つ
  `infra/envs/dev` も CI からは apply できない。この場合は `infra/bootstrap` と
  `infra/envs/dev` の両方をローカルの管理者資格情報から手動 apply して復旧する。
  bootstrap だけ直しても eval ロールが旧 `sub` のままなので、必須チェックの `eval` が
  通らず PR をマージできない
- ⚠️ OIDC provider の `thumbprint_list = []` は AWS 側が値を自動補完するため、
  `terraform plan` に恒常的な差分として出る (機能的には無害 — AWS は主要 IdP の
  ルート CA を内部で信頼しており thumbprint を参照しない)。この provider が
  「変更あり」と判定されると、それに依存する `aws_iam_policy_document` データソースが
  apply 時読み取りに繰り下がり、trust policy と guardrail ポリシーが
  `(known after apply)` として差分表示される。中身は同一で実質 no-op
- 詳細な IAM ポリシーは `infra/bootstrap/github_oidc.tf` と
  `infra/modules/ci-eval/main.tf` を参照
