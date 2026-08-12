# ADR 0004: Claude Haiku 4.5 は推論プロファイル経由で呼び出す

## ステータス

Accepted

## コンテキスト

計画書は「Bedrock (Claude Haiku 系、コスト重視)」を想定していたが、
`ap-northeast-1` で実際に `converse` API を呼び出して検証したところ、
以下の制約が判明した。

1. `anthropic.claude-haiku-4-5-20251001-v1:0` を **直接モデル ID として指定すると**
   `ValidationException: Invocation of model ID ... with on-demand throughput isn't supported.
   Retry your request with the ID or ARN of an inference profile that contains this model.`
   で拒否される
2. `anthropic.claude-3-haiku-20240307-v1:0` (旧世代) は
   `Access denied. This Model is marked by provider as Legacy and you have not been
   actively using the model in the last 30 days.` で使用不可
3. `ap-northeast-1` で利用可能な Haiku 4.5 の推論プロファイルは以下の2種
   - `jp.anthropic.claude-haiku-4-5-20251001-v1:0` (日本国内クロスリージョン)
   - `global.anthropic.claude-haiku-4-5-20251001-v1:0` (グローバル)
   両方で `converse` の成功を確認済み

## 決定

**`jp.anthropic.claude-haiku-4-5-20251001-v1:0`** (推論プロファイル ID) を
`BEDROCK_CHAT_MODEL_ID` として使用する。

`global.` ではなく `jp.` を選ぶ理由は、README でデータ所在地について
「日本国内リージョン内で処理される」と説明しやすく、
ポートフォリオとしてのコスト・データガバナンス意識の説明がしやすいため。

## 影響

- **Phase 2 の IAM ポリシー設計に注意が必要**:
  推論プロファイルを介した呼び出しでは、`bedrock:InvokeModel` の許可対象に
  推論プロファイルの ARN と、その配下の基盤モデル ARN の **両方**を含める必要がある
  (推論プロファイルだけでは呼び出せない場合がある)
- 計画書中の「Claude Haiku 系」という表現は、実装上は Haiku 4.5 の推論プロファイルを指すものと読み替える
- モデルの世代交代 (例: Haiku 5 系のリリース) に伴い推論プロファイル ID が
  変わる可能性があるため、`.env` で外出しにしてハードコードしない
