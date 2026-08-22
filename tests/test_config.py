"""Configuration guards. A guard that is only documented is not a guard."""

from __future__ import annotations

import pytest

from wapas.config import Settings


def test_live_razorpay_keys_are_refused():
    """There is no live code path in this repository, and this makes it structural."""
    with pytest.raises(ValueError, match="test-mode only"):
        Settings(_env_file=None, RAZORPAY_KEY_ID="rzp_live_abc123")


def test_unrecognised_key_prefix_is_refused():
    with pytest.raises(ValueError, match="unrecognised Razorpay key prefix"):
        Settings(_env_file=None, RAZORPAY_KEY_ID="sk_test_abc123")


def test_test_keys_are_accepted():
    s = Settings(_env_file=None, RAZORPAY_KEY_ID="rzp_test_abc123")
    assert s.razorpay_key_id.startswith("rzp_test_")


def test_provider_without_credentials_is_refused():
    with pytest.raises(ValueError, match="NVIDIA_API_KEY is unset"):
        Settings(_env_file=None, WAPAS_LLM_PROVIDER="nvidia", NVIDIA_API_KEY=None)


def test_secrets_are_masked_in_repr():
    s = Settings(_env_file=None, WAPAS_LLM_PROVIDER="nvidia", NVIDIA_API_KEY="nvapi-supersecret")
    assert "supersecret" not in repr(s)
    assert s.nvidia_api_key.get_secret_value() == "nvapi-supersecret"
