"""
Auth - Google / GitHub sign-in for the web UI.

Signing in is what gives each person their own history: every run is stored
with its owner's user ID, and server.py only ever shows a user their own runs.

Sign-in turns on as soon as one OAuth provider is configured (see
config.py:AuthConfig). Without any provider - e.g. running on your own PC -
there is no sign-in and every request acts as the single local user, exactly
like the app behaved before users existed.

The signed-in user lives in a signed (not encrypted) session cookie managed by
Starlette's SessionMiddleware: it holds only the user's own ID, name, email
and avatar URL, and can't be forged without SESSION_SECRET.
"""

import secrets
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from config import settings
from utils import storage
from utils.logger import get_logger

logger = get_logger(__name__)

LOCAL_USER = {"id": storage.LOCAL_USER_ID, "name": "Local user", "email": "", "avatar_url": "", "provider": "local"}

router = APIRouter()
_oauth = None  # authlib OAuth registry, created in setup_auth() only when sign-in is enabled


def setup_auth(app: FastAPI) -> None:
    """Install the session cookie middleware, OAuth clients and auth routes on the app."""
    global _oauth
    cfg = settings.auth

    secret = cfg.session_secret
    if not secret:
        secret = secrets.token_urlsafe(32)
        if cfg.enabled:
            logger.warning(
                "SESSION_SECRET is not set - using a random one, so everyone is signed out "
                "whenever the server restarts. Set SESSION_SECRET in production."
            )
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie="truthlens_session",
        max_age=30 * 24 * 3600,
        same_site="lax",
        https_only=cfg.public_url.startswith("https://"),
    )

    if cfg.enabled:
        from authlib.integrations.starlette_client import OAuth

        _oauth = OAuth()
        if "google" in cfg.providers:
            _oauth.register(
                name="google",
                client_id=cfg.google_client_id,
                client_secret=cfg.google_client_secret,
                server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                client_kwargs={"scope": "openid email profile"},
            )
        if "github" in cfg.providers:
            _oauth.register(
                name="github",
                client_id=cfg.github_client_id,
                client_secret=cfg.github_client_secret,
                access_token_url="https://github.com/login/oauth/access_token",
                authorize_url="https://github.com/login/oauth/authorize",
                api_base_url="https://api.github.com/",
                client_kwargs={"scope": "read:user user:email"},
            )
        logger.info(f"Sign-in enabled with: {', '.join(cfg.providers)}")

    app.include_router(router)


def current_user(request: Request) -> Optional[dict]:
    """FastAPI dependency: the signed-in user, the local user if sign-in is off, or None."""
    if not settings.auth.enabled:
        return LOCAL_USER
    return request.session.get("user")


def require_user(user: Optional[dict] = Depends(current_user)) -> dict:
    """FastAPI dependency: like current_user, but responds 401 when nobody is signed in."""
    if not user:
        raise HTTPException(401, "Please sign in first")
    return user


def _callback_url(request: Request, provider: str) -> str:
    if settings.auth.public_url:
        return f"{settings.auth.public_url}/auth/callback/{provider}"
    return str(request.url_for("auth_callback", provider=provider))


def _client(provider: str):
    if _oauth is None or provider not in settings.auth.providers:
        raise HTTPException(404, "Unknown sign-in provider")
    return _oauth.create_client(provider)


async def _fetch_profile(provider: str, client, token: dict) -> dict:
    """Normalize the provider's profile into our user dict."""
    if provider == "google":
        info = token.get("userinfo") or await client.userinfo(token=token)
        return {
            "id": f"google:{info['sub']}",
            "name": info.get("name") or info.get("email", ""),
            "email": info.get("email", ""),
            "avatar_url": info.get("picture", ""),
            "provider": "google",
        }

    profile = (await client.get("user", token=token)).json()
    email = profile.get("email") or ""
    if not email:
        # GitHub hides the email unless it's public; ask for the primary one.
        emails = (await client.get("user/emails", token=token)).json()
        if isinstance(emails, list):
            email = next((e["email"] for e in emails if e.get("primary") and e.get("verified")), "")
    return {
        "id": f"github:{profile['id']}",
        "name": profile.get("name") or profile.get("login", ""),
        "email": email,
        "avatar_url": profile.get("avatar_url", ""),
        "provider": "github",
    }


@router.get("/auth/login/{provider}")
async def auth_login(provider: str, request: Request):
    client = _client(provider)
    return await client.authorize_redirect(request, _callback_url(request, provider))


@router.get("/auth/callback/{provider}", name="auth_callback")
async def auth_callback(provider: str, request: Request):
    client = _client(provider)
    try:
        token = await client.authorize_access_token(request)
        user = await _fetch_profile(provider, client, token)
    except Exception as e:
        # Cancelled consent screen, expired/mismatched state, provider outage...
        logger.warning(f"Sign-in with {provider} failed: {e}")
        return RedirectResponse("/?auth_error=1")

    try:
        storage.upsert_user(user["id"], provider, user["name"], user["email"], user["avatar_url"])
    except Exception as e:
        logger.warning(f"Could not record user {user['id']}: {e}")

    request.session.clear()
    request.session["user"] = user
    return RedirectResponse("/")


@router.post("/auth/logout")
async def auth_logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/api/me")
async def me(user: Optional[dict] = Depends(current_user)):
    return {
        "auth_enabled": settings.auth.enabled,
        "providers": settings.auth.providers,
        "user": user,
    }
