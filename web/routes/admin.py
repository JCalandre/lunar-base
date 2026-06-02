"""Admin routes: list event/banner availability straight from the master-data
bin, and repack the bin to match the chosen selection (with a dated backup)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from web import config
from web.services import event_service

router = APIRouter()
templates = Jinja2Templates(directory=str(config.ROOT / "web" / "templates"))


@router.get("/admin/events", response_class=HTMLResponse)
def admin_events_view(request: Request, message: str | None = None, error: str | None = None):
    groups: list[dict] = []
    load_error = error
    bin_path = None
    try:
        bin_path = str(event_service.masterdata_bin.bin_path())
        for k in event_service.kinds():
            groups.append({"key": k.key, "label": k.label, "rows": event_service.list_events(k.key)})
    except (FileNotFoundError, KeyError, OSError, ValueError) as e:
        load_error = str(e)
        groups = []
    return templates.TemplateResponse(
        request,
        "admin_events.html",
        {"active": "admin", "groups": groups, "message": message,
         "error": load_error, "bin_path": bin_path},
    )


@router.post("/admin/events/apply")
def admin_events_apply(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """payload: {"selections": {"quest": [ids...], "banner": [ids...]}}."""
    selections = payload.get("selections")
    if not isinstance(selections, dict) or not selections:
        return JSONResponse({"ok": False, "error": "selections object is required"}, status_code=400)
    parsed: dict[str, list[int]] = {}
    try:
        for kind, ids in selections.items():
            parsed[str(kind)] = [int(i) for i in ids]
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "selection ids must be integers"}, status_code=400)
    try:
        result = event_service.apply(parsed)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except (FileNotFoundError, KeyError, OSError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, **result})
