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

import re
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
    category: str = ""


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


_banner_names_cache: dict[int, str] | None = None


def _banner_names() -> dict[int, str]:
    """Real summon-banner titles from gacha_banners.json (Engels output), keyed
    by MomBannerId. Only entries whose title actually resolved (name_found)."""
    global _banner_names_cache
    if _banner_names_cache is not None:
        return _banner_names_cache
    import json
    out: dict[int, str] = {}
    candidates = [
        config.NAMES_DIR / "gacha_banners.json",
        config.LUNAR_TEAR_DIR / "server" / "assets" / "names" / "gacha_banners.json",
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
            if rec.get("name_found") and isinstance(rid, int) and isinstance(name, str) and name:
                out[rid] = name
        break
    _banner_names_cache = out
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
                if name and _PLACEHOLDER_RE.match(name):
                    name = None  # don't propagate "Event Quest Chapter N" to banners
                if start and name and start not in out:
                    out[start] = name
        except (FileNotFoundError, KeyError, OSError):
            pass
    _event_by_start_cache = out
    return out


# Many event-quest chapters have no localized name (Engels can't resolve them
# either) — they're recurring content. Label them by EventQuestType category
# instead of "Event Quest Chapter N". Categories derived from the chapters that
# DO have names (type 1 -> "Record: ...", type 2 -> "Variation: ...", etc.).
_PLACEHOLDER_RE = re.compile(r"^Event Quest(?: Chapter)? \d+$")
_EVENT_CATEGORIES = {
    1: "Record", 2: "Variation", 3: "Subjugation", 4: "Daily", 5: "Guerrilla",
    6: "Labyrinth", 7: "Tower", 8: "Event", 9: "Special", 10: "Abyss Tower",
    11: "Chambers of Dusk", 12: "Fate Board",
}


def _category(kind: str, row: list) -> str:
    """Grouping label. Quests use their EventQuestType ("Record", "Variation",
    "Tower", …). Banners use the category of the event they launched with (same
    start date), falling back to the banner's asset-prefix kind."""
    if kind == "quest":
        etype = int(row[1]) if len(row) > 1 and row[1] is not None else 0
        return _EVENT_CATEGORIES.get(etype, f"Type {etype}")
    cat = _event_category_by_start().get(int(row[6] or 0))
    if cat:
        return cat
    asset = _as_str(row[_BANNER_ASSET_COL])
    if asset.startswith("common_"):
        return "Chapter"
    if asset.startswith("step_up_"):
        return "Step-Up"
    if asset.startswith("limited_"):
        return "Premium"
    return "Other"


def _category_label(row: list) -> str:
    chapter_id = int(row[0])
    cat = _category("quest", row)
    return f"{cat} — Chapter {chapter_id}"


_event_cat_by_start_cache: dict[int, str] | None = None


def _event_category_by_start() -> dict[int, str]:
    """Map an event's StartDatetime -> its category, so a summon banner can be
    grouped by the event it launched alongside (banner & quest share a date)."""
    global _event_cat_by_start_cache
    if _event_cat_by_start_cache is not None:
        return _event_cat_by_start_cache
    out: dict[int, str] = {}
    try:
        for r in masterdata_bin.decode_rows("m_event_quest_chapter"):
            start = int(r[8] or 0)
            etype = int(r[1]) if len(r) > 1 and r[1] is not None else 0
            if start and start not in out:
                out[start] = _EVENT_CATEGORIES.get(etype, f"Type {etype}")
    except (FileNotFoundError, KeyError, OSError):
        pass
    _event_cat_by_start_cache = out
    return out


def invalidate_name_caches() -> None:
    global _quest_names_cache, _banner_names_cache, _event_by_start_cache, _event_cat_by_start_cache
    _quest_names_cache = None
    _banner_names_cache = None
    _event_by_start_cache = None
    _event_cat_by_start_cache = None


# Manual EventQuestChapterId -> display name overrides. These quests either have
# no localized name in the extracted data or carry a wrong one, so they would not
# sort alphabetically in the admin list. An override wins over everything (the
# extracted name and the category fallback). "\n" reproduces the game's two-line
# label; it collapses to a space when rendered inline.
_QUEST_NAME_OVERRIDES: dict[int, str] = {
    310001: "JP 1.5-Year Anniv. Eve\nChallenge Quest",
    320001: "JP 1.5-Year Anniv. Eve\nOnce per Day Quests",
    400019: "Crossover Commemoration\nOnce Per Day Quest",
    400047: "Crossover Special\nEnhancement Quest",
    400048: "JP 1.5-Year Anniv.\nSpecial Enhan. Quest",
    400060: "JP 1.5 Anniv.\nSpecial Enhan. Quest",
    501: "Record: Den of Madness (Resurrected)",
    502: "Record: Pure Hills (Resurrected)",
    503: "Record: Valley of Light (Resurrected)",
    505: "Record: City of Discontent (Resurrected)",
    506: "Record: Blood Oath's Edge (Resurrected)",
    507: "Record: Seat of Shadow (Resurrected)",
    509: "Record: Sunset Port (Resurrected)",
    510: "Record: The Cage of Reincarnation (Resurrected)",
    512: "Record: Garden of Benediction (Resurrected)",
    514: "Record: Town of Deceit (Resurrected)",
    515: "Record: Chamber of Prayer (Resurrected)",
    517: "Record: Bridge of Supplication (Resurrected)",
    519: "Record: Festive Fountain (Resurrected)",
    521: "Record: Garden of Paradise (Resurrected)",
    522: "Record: Foundation of Fortune (Resurrected)",
    524: "Record: Stronghold of Desolation (Resurrected)",
    526: "Record: Vestige of Paradise (Resurrected)",
    527: "Record: Covetous Grove (Resurrected)",
    531: "Record: Original Sin's Door (Resurrected)",
    534: "Record: Playful Seas (Resurrected)",
    539: "Record: Trench of Lost Bonds (Resurrected)",
    542: "Record: Happy Home (Resurrected)",
    544: "Record: The Pumpkin Box (Resurrected)",
    546: "Record: Mechanical Foundation (Resurrected)",
    551: "Record: The Wishing Spot (Resurrected)",
    554: "Record: Forest of Temptation (Resurrected)",
    572: "Record: Happy Home (Resurrected)",
    581: "Record: A Distant Peak (Resurrected)",
    584: "Record: Garden of Benediction (Resurrected)",
    585: "Record: Foundation of Fortune (Resurrected)",
    587: "Record: Happy Home (Resurrected)",
    588: "Record: Eternal Steps (Resurrected)",
    589: "Record: Ritual of Reverie (Resurrected)",
}


def _name(kind: str, row: list) -> str:
    if kind == "banner":
        real = _banner_names().get(int(row[0]))
        if real:
            return real
        by_start = _event_name_by_start().get(int(row[6] or 0))
        return by_start or _as_str(row[_BANNER_ASSET_COL]) or f"Banner {row[0]}"
    chapter_id = int(row[0])
    override = _QUEST_NAME_OVERRIDES.get(chapter_id)
    if override:
        return override
    name = _quest_names().get(chapter_id)
    if name and not _PLACEHOLDER_RE.match(name):
        return name
    return _category_label(row)


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
            category=_category(kind, r),
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


# Display-sort columns per kind. Event quests carry both SortOrder (col 2) and
# DisplaySortOrder (col 10); set both so whichever the client menu uses is
# ordered. Summons order by m_mom_banner.SortOrderDesc (col 1), which the server
# sorts the gacha list by.
_SORT_COLS = {"quest": [2, 10], "banner": [1]}


def reorder(kind: str, ordered_ids: list[int], now_ms: int | None = None) -> dict:
    """Set a kind's display-sort column to the given explicit order (the i-th id
    gets sort value i). Ids not in the list are appended after it in id order so
    every row still gets a rank; unknown ids are dropped. Repacks with a backup.
    Works for any arrangement — alphabetical, manual drag, by date, etc."""
    if kind not in _KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    cfg = _KINDS[kind]
    valid = {e.id for e in list_events(kind, now_ms)}
    ids: list[int] = []
    seen: set[int] = set()
    for i in ordered_ids:
        i = int(i)
        if i in valid and i not in seen:
            ids.append(i)
            seen.add(i)
    for e_id in sorted(valid - seen):
        ids.append(e_id)
    result = masterdata_bin.apply_order([{
        "table": cfg.table,
        "id_col": cfg.id_col,
        "sort_cols": _SORT_COLS[kind],
        "ordered_ids": ids,
    }])
    result["backup_name"] = Path(result["backup"]).name
    result["count"] = len(ids)
    return result
