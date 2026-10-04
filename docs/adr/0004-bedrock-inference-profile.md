# ADR 0004: Claude Haiku 4.5 は推論プロファイル経由で呼び出す

## ステータス

Accepted

## コンテキスト

`ap-northeast-1` で Haiku 4.5 を `converse` で呼び出すには制約がある。

- `anthropic.claude-haiku-4-5-20251001-v1:0` を直接指定すると、on-demand 非対応で `ValidationException` になる
- 旧世代の Claude 3 Haiku は Legacy 扱いで利用できない
- 利用可能な推論プロファイルは `jp.` (日本国内クロスリージョン) と `global.` の 2 種

## 決定

**`jp.anthropic.claude-haiku-4-5-20251001-v1:0`** を `BEDROCK_CHAT_MODEL_ID` として使う。
`jp.` を選ぶのは、データ処理が日本国内リージョンに収まると説明できるため。

## 影響

- **IAM**: `bedrock:InvokeModel` の Resource に、推論プロファイル ARN と配下の基盤モデル ARN の **両方** が必要
- 推論プロファイル ID はモデル世代交代で変わりうるため、`.env` で外出しにしてハードコードしない
