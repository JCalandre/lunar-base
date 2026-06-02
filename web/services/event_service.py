"""List and toggle event availability by reading and repacking the encrypted
master-data bin (the file the game server actually loads).

Two kinds of "event" live in different master-data tables, both carrying
StartDatetime / EndDatetime (epoch ms):

  - quest:  m_event_quest_chapter (limited-time event quests)
  - banner: m_mom_banner          (gacha event / pickup summon banners)

`list_events` reads the current on/off state straight from the bin. `apply`
repacks the bin so the chosen ids are the only active ones of their table
(others get a past EndDatetime), writing a dated backup of the old bin first.
Unlike the old JSON approach, this takes effect once the game client
re-downloads master data (it does so automatically because the repack changes
the bin's mtime, hence its reported version).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from web import config
from web.services import masterdata_bin

MOM_BANNER_DOMAIN_GACHA = 1  # m_mom_banner.DestinationDomainType for gacha banners
_BANNER_ASSET_COL = 4        # m_mom_banner.BannerAssetName


@dataclass(frozen=True)
class _Kind:
    key: str
    label: str
    table: str
    id_col: int
    start_col: int
    end_col: int


_KINDS: dict[str, _Kind] = {
    "quest": _Kind("quest", "Event Quests", "m_event_quest_chapter", 0, 8, 9),
    "banner": _Kind("banner", "Event Summon Banners", "m_mom_banner", 0, 6, 7),
}


@dataclass(frozen=True)
class EventRow:
    id: int
    name: str
    start: int
    end: int
    start_str: str
    end_str: str
    active: bool


def kinds() -> list[_Kind]:
    return list(_KINDS.values())


def _fmt(ms: int) -> str:
    if not ms:
        return "—"
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return str(ms)


def _as_str(v) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v) if v is not None else ""


# --- name resolution -------------------------------------------------------

_quest_names_cache: dict[int, str] | None = None


def _quest_names() -> dict[int, str]:
    """Real event-quest names from an extracted names file, if one exists
    (event_quests.json from tools/extract_names.py). EventQuestChapterId -> name."""
    global _quest_names_cache
    if _quest_names_cache is not None:
        return _quest_names_cache
    import json
    out: dict[int, str] = {}
    candidates = [
        config.NAMES_DIR / "event_quests.json",
        config.LUNAR_TEAR_DIR / "server" / "assets" / "names" / "event_quests.json",
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for rec in data.get("records", []):
            rid, name = rec.get("id"), rec.get("name")
            if isinstance(rid, int) and isinstance(name, str) and name:
                out[rid] = name
        break
    _quest_names_cache = out
    return out


_event_by_start_cache: dict[int, str] | None = None


def _event_name_by_start() -> dict[int, str]:
    """Map an event's StartDatetime -> its real event-quest name, so a banner can
    show its event's name instead of the art-asset id (a banner and its quest
    chapter launch on the same date). Empty until event_quests.json exists."""
    global _event_by_start_cache
    if _event_by_start_cache is not None:
        return _event_by_start_cache
    names = _quest_names()
    out: dict[int, str] = {}
    if names:
        try:
            for r in masterdata_bin.decode_rows("m_event_quest_chapter"):
                start = int(r[8] or 0)
                name = names.get(int(r[0]))
                if start and name and start not in out:
                    out[start] = name
        except (FileNotFoundError, KeyError, OSError):
            pass
    _event_by_start_cache = out
    return out


def invalidate_name_caches() -> None:
    global _quest_names_cache, _event_by_start_cache
    _quest_names_cache = None
    _event_by_start_cache = None


def _name(kind: str, row: list) -> str:
    if kind == "banner":
        by_start = _event_name_by_start().get(int(row[6] or 0))
        return by_start or _as_str(row[_BANNER_ASSET_COL]) or f"Banner {row[0]}"
    chapter_id = int(row[0])
    return _quest_names().get(chapter_id) or f"Event Quest {chapter_id}"


def _included(kind: str, row: list) -> bool:
    if kind == "banner":
        return len(row) > 2 and row[2] == MOM_BANNER_DOMAIN_GACHA
    return True


# --- read / apply ----------------------------------------------------------

def list_events(kind: str, now_ms: int | None = None) -> list[EventRow]:
    """Current id / name / window / active state for a kind, read from the bin."""
    if kind not in _KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    cfg = _KINDS[kind]
    rows: list[EventRow] = []
    for r in masterdata_bin.decode_rows(cfg.table):
        if not _included(kind, r):
            continue
        start = int(r[cfg.start_col] or 0)
        end = int(r[cfg.end_col] or 0)
        rows.append(EventRow(
            id=int(r[cfg.id_col]),
            name=_name(kind, r),
            start=start,
            end=end,
            start_str=_fmt(start),
            end_str=_fmt(end),
            active=masterdata_bin.is_active(start, end, now),
        ))
    rows.sort(key=lambda e: (not e.active, e.id))
    return rows


def apply(selections: dict[str, list[int]], now_ms: int | None = None) -> dict:
    """Repack the bin so each kind's selected ids are its only active rows.

    selections: {kind: [active id, ...]}. Rows of a table that the UI does not
    manage (e.g. non-gacha banners) are left untouched. Writes a dated backup of
    the old bin, then overwrites it. Returns the masterdata_bin summary plus the
    relaunch reminder.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    specs = []
    for kind, ids in selections.items():
        if kind not in _KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        cfg = _KINDS[kind]
        managed = {e.id for e in list_events(kind, now)}
        active = {int(i) for i in ids} & managed
        specs.append({
            "table": cfg.table,
            "id_col": cfg.id_col,
            "start_col": cfg.start_col,
            "end_col": cfg.end_col,
            "active_ids": active,
            "managed_ids": managed,
        })
    if not specs:
        raise ValueError("no event kinds selected")
    result = masterdata_bin.apply_windows(specs, now)
    result["backup_name"] = Path(result["backup"]).name
    return result
