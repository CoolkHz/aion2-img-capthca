import hmac

from fastapi import Header, HTTPException

from src.settings import get_api_secret


def verify_api_secret(
    x_api_secret: str | None = Header(default=None, alias="X-API-SECRET"),
) -> None:
    expected = get_api_secret()
    if not x_api_secret or not hmac.compare_digest(x_api_secret, expected):
        raise HTTPException(status_code=401, detail="unauthorized")

