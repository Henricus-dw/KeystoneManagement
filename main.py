"""Keystone -- FastAPI entry point."""
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import APP_NAME, SECRET_KEY, SESSION_COOKIE, STATIC_DIR
from app.deps import AuthRedirect
from app.routers import api, auth, pages, servers
from app.seed import seed

app = FastAPI(title=APP_NAME)

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie=SESSION_COOKIE,
    max_age=60 * 60 * 24 * 7,
    same_site="lax",
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.exception_handler(AuthRedirect)
async def auth_redirect_handler(request: Request, exc: AuthRedirect):
    return RedirectResponse(exc.location, status_code=303)


app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(servers.router)
app.include_router(api.router)


@app.on_event("startup")
def on_startup() -> None:
    seed()
