"""Lunar Base FastAPI entrypoint.

Run with:
    python -m uvicorn web.app:app --host 127.0.0.1 --port 8888

(or use the run-lunar-base.bat / run-lunar-base.sh helper, which honor the
LUNAR_BASE_HOST and LUNAR_BASE_PORT environment variables)
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from web import config
from web.routes import backup as backup_routes
from web.routes import costume_editor as costume_editor_routes
from web.routes import item_editor as item_editor_routes
from web.routes import memoir_editor as memoir_editor_routes
from web.routes import upgrade_manager as upgrade_manager_routes
from web.routes import users as users_routes
from web.routes import weapon_editor as weapon_editor_routes


def create_app() -> FastAPI:
    app = FastAPI(title="Lunar Base", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount(
        "/static",
        StaticFiles(directory=str(config.ROOT / "web" / "static")),
        name="static",
    )
    app.include_router(backup_routes.router)
    app.include_router(users_routes.router)
    app.include_router(item_editor_routes.router)
    app.include_router(costume_editor_routes.router)
    app.include_router(weapon_editor_routes.router)
    app.include_router(upgrade_manager_routes.router)
    app.include_router(memoir_editor_routes.router)
    return app


app = create_app()
