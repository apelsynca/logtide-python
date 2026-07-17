from typing import Literal

import pytest

from logtide_sdk.models import ClientOptions


@pytest.mark.parametrize("local_mode", ["if_unset_api_key", True])
@pytest.mark.parametrize("api_key", [None, ""])
def test_client_options_ignores_unset_api_key_if_set_unset_or_local_mode(
    api_key: str, local_mode: Literal["if_unset_api_key"] | bool
) -> None:
    ClientOptions(api_url="https://any.apiurl.dev", api_key=api_key, local_mode=local_mode)


def test_client_options_raises_no_api_key_and_without_local_mode():
    with pytest.raises(ValueError, match="api_key must be provided"):
        ClientOptions(api_url="https://somenetwork:8080")


def test_client_options_raises_no_api_url_but_with_api_key():
    with pytest.raises(ValueError, match="api_url must be provided"):
        ClientOptions(api_key="lp_someApiKey_H3re")

    with pytest.raises(ValueError, match="api_url must be provided"):
        ClientOptions(api_url="", api_key="lp_someApiKey_H3re")


def test_client_options_accepts_dsn() -> None:
    opts = ClientOptions(dsn="https://lp_SomeKey-12951ThatIsHere@api.myselfhosted.com")

    assert opts.api_url == "https://api.myselfhosted.com"
    assert opts.api_key == "lp_SomeKey-12951ThatIsHere"


def test_client_options_explicit_still_works() -> None:
    opts = ClientOptions(api_url="http://localhost:8080", api_key="lp_k")

    assert opts.api_url == "http://localhost:8080"
    assert opts.api_key == "lp_k"
