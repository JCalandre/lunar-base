"""Routes for the Memoir Editor (Stage 5b).

Three flows on /users/{user_id}/memoirs:
  - Build a Set: grant 3 memoirs from a chosen set at lv15 with chosen
    primary main-stat per memoir + 4 sub-stats per memoir (one shim call).
  - Upgrade All Memoirs: set every owned memoir to lv15.
  - Fix Slots: rewrite the 4 sub-status rows on a single owned memoir.

A backup is taken automatically before every action (reason
`memoir-editor`).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from web import config, session
from web.services import memoir_service, userdata_service

router = APIRouter()
templates = Jinja2Templates(directory=str(config.ROOT / "web" / "templates"))


def _redirect(target: str, *, message: str | None = None, error: str | None = None) -> RedirectResponse:
    params: dict[str, str] = {}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    qs = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(url=f"{target}{qs}", status_code=303)


def _ok(outcome: memoir_service.MemoirOutcome) -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "applied": outcome.succeeded,
        "duration_ms": outcome.duration_ms,
        "detail": outcome.detail,
    })


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


@router.get("/memoirs", response_class=HTMLResponse)
def memoir_editor_index(request: Request):
    try:
        users = userdata_service.list_users()
    except FileNotFoundError as e:
        return _redirect("/", error=str(e))
    remembered = session.remembered_redirect(request, "/memoirs", users)
    if remembered is not None:
        return remembered
    if len(users) == 1:
        return RedirectResponse(url=f"/users/{users[0].user_id}/memoirs", status_code=303)
    return RedirectResponse(url="/users", status_code=303)


@router.get("/users/{user_id}/memoirs", response_class=HTMLResponse)
def memoir_editor_view(request: Request, user_id: int):
    try:
        users = userdata_service.list_users()
    except FileNotFoundError as e:
        return _redirect("/", error=str(e))
    user_match = next((u for u in users if u.user_id == user_id), None)
    if user_match is None:
        return _redirect("/users", error=f"User {user_id} not found.")

    owned_count = userdata_service.get_memoir_count(user_id)
    owned_rows = userdata_service.list_owned_memoirs(user_id)

    # Render-side dictionary so the template can show "Set Name :: Memoir
    # Name" for each owned uuid in the Fix Slots picker.
    group_to_memoir: dict[int, dict] = {}
    for s in memoir_service.SETS:
        for m in s["memoirs"]:
            group_to_memoir[m["group_id"]] = {
                "memoir_name": m["name"],
                "set_name": s["name"],
            }

    owned_for_picker: list[dict] = []
    for r in owned_rows:
        # parts_id -> group_id: groups are 20 part rows each, R40 occupies
        # ids (g-1)*20+16 .. (g-1)*20+20. We accept any rarity here so the
        # picker can show R10/20/30 memoirs too if the user happens to own
        # them, but the editor's Build flow only ever creates R40.
        gid = (r.parts_id - 1) // 20 + 1
        info = group_to_memoir.get(gid, {"memoir_name": f"parts_id={r.parts_id}", "set_name": ""})
        owned_for_picker.append({
            "uuid": r.user_parts_uuid,
            "parts_id": r.parts_id,
            "level": r.level,
            "main_id": r.parts_status_main_id,
            "memoir_name": info["memoir_name"],
            "set_name": info["set_name"],
        })

    return templates.TemplateResponse(
        request,
        "memoir_editor.html",
        {
            "active": "memoirs",
            "user_id": user_id,
            "user_name": user_match.name,
            "sets": memoir_service.list_sets(),
            "primary_options": memoir_service.PRIMARY_OPTIONS,
            "sub_options": memoir_service.SUB_OPTIONS,
            "default_sub_order": memoir_service.DEFAULT_SUB_ORDER,
            "owned_count": owned_count,
            "inventory_cap": memoir_service.MEMOIR_INVENTORY_CAP,
            "owned_for_picker": owned_for_picker,
        },
    )


# ---------------- JSON endpoints --------------------------------------------

@router.post("/users/{user_id}/memoirs/grant_set")
def grant_set_endpoint(user_id: int, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """Body shape:
        {
          "set_id": 4,
          "memoirs": [
              {"group_id": 10, "primary_key": "crit_rate",
               "subs": [{"slot": 1, "sub_key": "crit_rate", "value": 250},
                        {"slot": 2, "sub_key": "crit_dmg",  "value": 360},
                        {"slot": 3, "sub_key": "atk_pct",   "value": 125},
                        {"slot": 4, "sub_key": "atk_flat",  "value": 600}]},
              ...
          ]
        }
    """
    try:
        set_id = int(payload["set_id"])
    except (KeyError, TypeError, ValueError):
        return _err("set_id required")
    memoirs = payload.get("memoirs")
    if not isinstance(memoirs, list) or not memoirs:
        return _err("memoirs must be a non-empty list")

    try:
        current = userdata_service.get_memoir_count(user_id)
        outcome = memoir_service.grant_set(
            user_id=user_id,
            set_id=set_id,
            memoirs=memoirs,
            current_inventory=current,
        )
    except memoir_service.MemoirError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)
    return _ok(outcome)


@router.post("/users/{user_id}/memoirs/upgrade_all")
def upgrade_all_endpoint(user_id: int) -> JSONResponse:
    try:
        outcome = memoir_service.upgrade_all(user_id)
    except memoir_service.MemoirError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)
    return _ok(outcome)


@router.post("/users/{user_id}/memoirs/fix_slots")
def fix_slots_endpoint(user_id: int, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    """Body shape:
        {
          "user_parts_uuid": "abc-...",
          "subs": [{"slot": 1, "sub_key": "crit_rate", "value": 250}, ...]
        }
    """
    uuid = payload.get("user_parts_uuid")
    subs = payload.get("subs")
    if not isinstance(uuid, str) or not uuid:
        return _err("user_parts_uuid required")
    if not isinstance(subs, list) or not subs:
        return _err("subs must be a non-empty list")

    try:
        outcome = memoir_service.fix_slots(user_id, uuid, subs)
    except memoir_service.MemoirError as e:
        return _err(str(e))
    except FileNotFoundError as e:
        return _err(f"Backup failed: {e}", status=500)
    return _ok(outcome)
