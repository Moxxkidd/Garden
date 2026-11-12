"""Manual collection contract: explicit mode and non-replayable observations."""

import pytest
from pydantic import ValidationError

from app.schemas.identity import IdentityCollectionRequest


def test_manual_mode_requires_one_explicit_identity_and_keeps_automatic_default():
    assert (
        IdentityCollectionRequest(
            url="http://localhost/", target_id=1, include_anonymous=True
        ).interaction_mode
        == "automatic"
    )
    with pytest.raises(ValidationError):
        IdentityCollectionRequest(
            url="http://localhost/", target_id=1, include_anonymous=True, interaction_mode="manual"
        )
    with pytest.raises(ValidationError):
        IdentityCollectionRequest(
            url="http://localhost/", target_id=1, profile_ids=[1, 2], interaction_mode="manual"
        )
