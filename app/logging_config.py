"""CloudWatch Logs Insights で集計しやすい JSON 構造化ログ。

Lambda 上ではログが自動的に CloudWatch Logs に流れるため、1 リクエスト
1 行の JSON で出力しておくことで、レイテンシ・トークン数の集計 (Phase 4 の
監視ダッシュボードの土台) をクエリだけで行えるようにする。
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from app.config import get_settings

_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra_fields = getattr(record, "extra_fields", None)
        if extra_fields:
            payload.update(extra_fields)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    settings = get_settings()
    root = logging.getLogger()
    root.setLevel(settings.log_level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    _configured = True


def log_event(logger: logging.Logger, message: str, **fields: Any) -> None:
    logger.info(message, extra={"extra_fields": fields})
