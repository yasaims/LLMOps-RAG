from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app import bedrock
from app.config import Settings


def _throttling_error() -> ClientError:
    return ClientError({"Error": {"Code": "ThrottlingException"}}, "InvokeModel")


@pytest.mark.parametrize(
    ("budget_s", "expected_attempts"),
    [
        (0.0, 6),  # no budget: attempt limit only
        (2.5, 3),  # stops before the 4 s sleep that would exceed the budget
    ],
)
def test_throttled_call_respects_retry_budget(budget_s: float, expected_attempts: int):
    client = MagicMock()
    client.invoke_model.side_effect = _throttling_error()
    settings = Settings(bedrock_retry_budget_s=budget_s)

    with (
        patch("app.bedrock.get_settings", return_value=settings),
        patch("app.bedrock._client", return_value=client),
        patch("tenacity.nap.time.sleep"),
        pytest.raises(ClientError),
    ):
        bedrock.embed_query("q")

    assert client.invoke_model.call_count == expected_attempts
