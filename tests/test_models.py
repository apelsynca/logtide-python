import pytest

from logtide_sdk.models import ClientOptions


def test_client_options_requires_dsn_or_url_and_key() -> None:
    with pytest.raises(ValueError, match="Either dsn or api_url"):
        ClientOptions()

    with pytest.raises(ValueError, match="Either dsn or api_url"):
        ClientOptions(api_url="http://localhost:8080", api_key=None)

    with pytest.raises(ValueError, match="Either dsn or api_url"):
        ClientOptions(api_url="http://localhost:8080", api_key="")


def test_client_options_accepts_dsn() -> None:
    opts = ClientOptions(dsn="https://lp_abc@logs.example.com")

    assert opts.api_url == "https://logs.example.com"
    assert opts.api_key == "lp_abc"


def test_client_options_explicit_still_works() -> None:
    opts = ClientOptions(api_url="http://localhost:8080", api_key="lp_k")

    assert opts.api_url == "http://localhost:8080"
    assert opts.api_key == "lp_k"
