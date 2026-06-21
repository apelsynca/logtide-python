import pytest

from logtide_sdk.models import ClientOptions


def test_client_options_requires_dsn_or_url_and_key_by_default() -> None:
    with pytest.raises(ValueError, match="Either dsn or api_url"):
        ClientOptions(local_mode=False)

    with pytest.raises(ValueError, match="Either dsn or api_url"):
        ClientOptions(api_url="http://localhost:8080", api_key=None, local_mode=False)

    with pytest.raises(ValueError, match="Either dsn or api_url"):
        ClientOptions(api_url="http://localhost:8080", api_key="")


@pytest.mark.parametrize("local_mode", ["if_unset_api_key", True])
@pytest.mark.parametrize("api_key", [None, ""])
def test_client_options_ignores_unset_api_key_if_set_unset_or_local_mode(
    api_key: str, local_mode: str
) -> None:
    ClientOptions(api_url="https://any.apiurl.dev", api_key=api_key, local_mode=local_mode)


def test_client_options_accepts_dsn() -> None:
    opts = ClientOptions(dsn="https://lp_abc@logs.example.com")

    assert opts.api_url == "https://logs.example.com"
    assert opts.api_key == "lp_abc"


def test_client_options_explicit_still_works() -> None:
    opts = ClientOptions(api_url="http://localhost:8080", api_key="lp_k")

    assert opts.api_url == "http://localhost:8080"
    assert opts.api_key == "lp_k"
