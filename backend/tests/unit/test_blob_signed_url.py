from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.core import blob as blob_core
from backend.core.config import Settings


@pytest.mark.asyncio
async def test_signed_download_url_defaults_to_one_minute(monkeypatch):
    captured: dict[str, datetime] = {}

    def fake_generate_blob_sas(**kwargs):
        captured["expiry"] = kwargs["expiry"]
        return "sig=fake"

    monkeypatch.setattr(blob_core, "generate_blob_sas", fake_generate_blob_sas)

    before = datetime.now(UTC)
    url = await blob_core.signed_download_url(
        object(),  # unused in connection-string mode
        Settings(),  # type: ignore[call-arg]
        skill_id="demo-skill",
        version="1.0.0",
    )
    after = datetime.now(UTC)

    assert url.endswith("/published/demo-skill/1.0.0/bundle.tar.gz?sig=fake")
    assert before + timedelta(seconds=50) <= captured["expiry"] <= after + timedelta(seconds=70)
