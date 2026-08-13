# Lambda (コンテナイメージ) 用ビルド。x86_64 固定 (Windows からのクロスビルドを避ける)。
FROM public.ecr.aws/lambda/python:3.12

# 依存関係だけを先にインストールしてレイヤーキャッシュを効かせる。
# requirements.txt は `scripts/push_image.ps1` が `uv export` で都度生成する。
COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY app ${LAMBDA_TASK_ROOT}/app

CMD ["app.api.lambda_handler.handler"]
