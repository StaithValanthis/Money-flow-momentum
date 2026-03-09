"""Tests for idempotent order ID generation."""

import time
from src.execution.executor import Executor
from src.config.config import ExecutionConfig, StopTPConfig
from unittest.mock import MagicMock


def test_order_link_id():
    """Test order link ID is unique."""
    config = ExecutionConfig(idempotent_order_link=True)
    stop_tp = StopTPConfig()
    client = MagicMock()
    exec = Executor(client, config, stop_tp)

    ids = []
    for _ in range(5):
        ids.append(exec._order_link_id("entry"))
        time.sleep(0.001)
    assert len(set(ids)) == 5
    assert all("entry_" in i for i in ids)

