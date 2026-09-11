"""Keystone -- FastAPI entry point."""
import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import APP_NAME, SECRET_KEY, SESSION_COOKIE, STATIC_DIR
from app.deps import AuthRedirect
from app.routers import api, auth, changelog, pages, servers
from app.seed import seed
from app.hours_scheduler import process_hours_deadline

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
app.include_router(changelog.router)
app.include_router(api.router)


@app.on_event("startup")
async def on_startup() -> None:
    seed()
    async def hours_deadline_loop():
        while True:
            await asyncio.to_thread(process_hours_deadline)
            await asyncio.sleep(60)
    app.state.hours_deadline_task = asyncio.create_task(hours_deadline_loop())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    task = getattr(app.state, "hours_deadline_task", None)
    if task:
        task.cancel()
