"""Read-only C5 connection and quote diagnostics."""
from fastapi import APIRouter
from pydantic import BaseModel

from app.config_loader import load_app_config_validated
from app.services.c5_client import C5ApiError, C5ReadOnlyClient


router = APIRouter()


class C5QuoteBody(BaseModel):
    market_hash_name: str
    reference_link: str = ""


def _client_from_config() -> tuple[C5ReadOnlyClient, dict]:
    config = load_app_config_validated()
    c5 = config.get("c5") or {}
    return C5ReadOnlyClient(
        c5.get("app_key") or "",
        timeout=float(c5.get("request_timeout_seconds", 12) or 12),
    ), c5


@router.get("/api/c5/status")
def api_c5_status():
    client, c5 = _client_from_config()
    return {
        "ok": True,
        "configured": client.configured,
        "price_compare_enabled": bool(c5.get("price_compare_enabled", False)),
        "manual_recommendation_enabled": bool(c5.get("manual_recommendation_enabled", False)),
        "read_only": True,
    }


@router.post("/api/c5/check")
def api_c5_check():
    client, _c5 = _client_from_config()
    try:
        balance = client.get_balance()
        return {"ok": True, "configured": True, "read_only": True, "balance": balance.to_dict()}
    except C5ApiError as exc:
        return {"ok": False, "configured": client.configured, "read_only": True, "error": str(exc)}


@router.post("/api/c5/quote")
def api_c5_quote(body: C5QuoteBody):
    client, c5 = _client_from_config()
    try:
        quote = client.get_executable_quote(
            body.market_hash_name,
            page_size=int(c5.get("quote_page_size", 10) or 10),
            reference_link=body.reference_link,
        )
        return {"ok": True, "read_only": True, "quote": quote.to_dict()}
    except C5ApiError as exc:
        return {"ok": False, "read_only": True, "error": str(exc)}
