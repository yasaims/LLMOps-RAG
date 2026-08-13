"""Lambda (コンテナイメージ) のエントリーポイント。

`app.api.main:app` の FastAPI アプリを Mangum で ASGI → Lambda イベントに変換する。
lifespan は Lambda 実行環境の初回起動時 (コールドスタート) にのみ走ればよいため
"auto" のままにし、ウォーム実行時はハンドラの呼び出しのみが走る。
"""

from __future__ import annotations

from mangum import Mangum

from app.api.main import app

handler = Mangum(app)
