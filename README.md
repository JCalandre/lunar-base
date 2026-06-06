# Lunar Base

A browser-based management interface for someone who lives on the moon and manages The Cage. Sits alongside **lunar-tear** and lets you back up, restore, and edit the player database from a browser.

> Web-based control panel for a [Lunar Tear](https://github.com/Walter-Sparrow/lunar-tear) private server.

---

## Requirements

- Windows 10/11, Linux, or macOS
- Python 3.10 or newer (tested on 3.14)
- Go 1.25 or newer on `PATH` *(needed to build the `lunar-base-grant` shim; without it stages 1+ won't work)*
- A working [Lunar Tear](https://github.com/Walter-Sparrow/lunar-tear) checkout at the sibling path `..\lunar-tear\`
- The [lunar-scripts](https://gitlab.com/walter-sparrow-group/lunar-scripts) repo at `..\lunar-scripts\` *(only needed for the one-time master-data dump in stage 2+)*
- The encrypted master data binary at `..\lunar-tear\server\assets\release\20240404193219.bin.e` *(populated by the lunar-tear setup, not by us)*

### Expected directory layout

```
NierRein Repos\
├── lunar-tear\
├── lunar-scripts\
└── lunar-base\        ← this repo
```

---

## Setup & Running

### Setup (run once)

**Windows:**

```bat
setup.bat
```

**Linux / macOS:**

```sh
chmod +x setup.sh run-lunar-base.sh   # first time only
./setup.sh
```

Creates a virtual environment in `.venv/` and installs Python dependencies from `web/requirements.txt`. Re-run any time dependencies change or after pulling new shim sources.

### Run

**Windows:**

```bat
run-lunar-base.bat
```

**Linux / macOS:**

```sh
./run-lunar-base.sh
```

By default Lunar Base **auto-detects this PC's LAN IP** and binds to it, so the app is reachable from other devices on your network. The startup banner prints the exact address — e.g. `http://192.168.1.42:8888` — use that from this PC *and* from any other device. (Note: with this default `http://127.0.0.1:8888` does **not** work; that's intentional.) Press `Ctrl+C` in the terminal to stop the server.

> On Windows, the first launch may pop a Firewall prompt — allow Python on **Private** networks only.

> [!IMPORTANT]
> Lunar Base requires a **login**. Game accounts sign in with their username and
> password (verified against lunar-tear's `auth.db`, read-only) and can only see
> and edit **their own** record. An **admin** account can reach every record.
> Still only run it on a network you trust — admin holds full edit rights.

### Authentication & the admin account

Lunar Base reads game logins from lunar-tear's `..\lunar-tear\server\db\auth.db`
(it never writes to it). Each game account is scoped to the single game record
bound to it (`game.db users.facebook_id == auth_users.id`).

The admin account is **local to Lunar Base** (stored hashed in `data\admin.json`,
gitignored) and is **never** written into `auth.db`. Create or reset it with:

```bat
.venv\Scripts\python.exe tools\set_admin_password.py
```

Until an admin is configured, the login page shows a reminder and only game
accounts can sign in. The session secret is taken from `LUNAR_BASE_SECRET`, or a
random one is generated and persisted to `data\.session_secret`.

### Changing the bind address / port

The bind address and port are resolved in `web/config.py` and can be overridden with the `LUNAR_BASE_HOST` and `LUNAR_BASE_PORT` environment variables:

| `LUNAR_BASE_HOST` | Effect |
|---|---|
| *(unset — default)* | Auto-detect this PC's LAN IP. Reachable on the network; **not** served on `127.0.0.1`. |
| `0.0.0.0` | Bind every interface, including `127.0.0.1`. Most robust if your LAN IP changes. |
| `127.0.0.1` | This PC only — nothing on the network can reach it. |
| a specific IP | Bind exactly that address. |

Examples:

**Windows (PowerShell):**

```powershell
$env:LUNAR_BASE_HOST = "0.0.0.0"
.\run-lunar-base.bat
```

**Linux / macOS:**

```sh
LUNAR_BASE_HOST=0.0.0.0 ./run-lunar-base.sh
```

> Auto-detection picks the interface used for your default route. If detection fails (no network), it falls back to `0.0.0.0` so the server still starts. If your LAN IP changes often (e.g. a phone hotspot), `LUNAR_BASE_HOST=0.0.0.0` avoids having to care about the exact address.

---

## Master Data & English Names

Stages 1+ (currency / costume / weapon / upgrade / memoir editors) require two things derived from the game's data files:

- **Master data tables** decoded from the encrypted `.bin.e` to JSON.
- **English display names** extracted from lunar-tear's text-bundle revisions.

`setup.bat` handles both automatically on first run. Subsequent runs detect existing output and skip.

| Step | Output directory | Source |
|------|-----------------|--------|
| Master-data dump | `data\masterdata\` | `..\lunar-tear\server\assets\release\*.bin.e` |
| Names extraction | `data\names\` | `data\masterdata\` + `..\lunar-tear\server\assets\revisions\` |

Both output directories are gitignored and together hold ~700 JSON files.

> If the game's data ever changes (a server-side patch), redump by deleting `data\masterdata\` and `data\names\`, then re-running `setup.bat`.

### Manual fallback

If the master-data dump is skipped (lunar-scripts or `.bin.e` missing) or fails, run it yourself:

```bat
cd ..\lunar-scripts
py dump_masterdata.py --input ..\lunar-tear\server\assets\release\20240404193219.bin.e --output ..\lunar-base\data\masterdata
```

The dump needs `pycryptodome msgpack lz4`. `setup.bat` installs these into `.venv\` automatically; for a fully manual run, install them globally:

```bat
pip install pycryptodome msgpack lz4
```

If the names extraction is skipped or fails, run it from the `lunar-base` root:

```bat
.venv\Scripts\python.exe tools\extract_names.py
```

Defaults read from `data\masterdata\` and `..\lunar-tear\server\assets\revisions\`, writing to `data\names\`. Run with `--help` to override any of those paths.

---

## Stages

| # | Name | Status | Description |
|---|------|--------|-------------|
| 0a | Backup & Restore |  Done | Snapshot `game.db`, restore from snapshots. Restore refuses while lunar-tear is running. Rolling pool keeps the 50 most recent snapshots. |
| 0b | Read-only Viewer |  Done | Pick a player, see currencies and inventory counts. |
| 1 | Item Editor |  Done | Top up gems, gold, materials, consumables, and important items. All grants are additive and routed through lunar-tear's `GrantPossession`. Per-tab **GRANT ALL CHOSEN** batches every row with an amount set; **MAX ALL** on Consumables/Materials runs a curated rule set in a single transaction. |
| 2 | Costume Editor |  Done | Grant 4-star (R40) and 3-star (R30) playable costumes via `GrantCostume`. R20 story-starter costumes are excluded. Sort order: Recollections of Dusk (Frozen-Heart / F-H) » Dark Memory » Other 4-Star » 3-Star, alphabetical within each group. |
| 3 | Weapon Editor |  Done | Grant playable weapons via `GrantWeapon`, cascading into skills, abilities, weapon notes, and story unlocks. R20 chains excluded. 519-entry catalog split into RoD » Dark Memory » Other 4-Star » 3-Star. RoD and Dark Memory grant the final R50 form; others grant the base step for in-game evolution. Hard 999-row inventory cap enforced; oversized batches refused with a clear error. Already-owned weapons filtered client-side. **After mass-adding DM weapons, run "Skip All DM Cutscenes" from the Upgrade Manager** — the game queues a forced cutscene per DM acquisition and only plays one per launch, which soft-locks progression until the queue drains. |
| 4 | Upgrade Manager |  Done | Three sections, ten actions: **Characters** (Exalt All Available, Fill Mythic Slab Pages); **Inventory** (Add All Missing Companions / Remnants / Debris); **Mass Upgrades** (Upgrade All Companions to lv50, Upgrade All Weapons cost-bypassing the full evolve/ascend/refine/enhance/skill path, Upgrade All Costumes cost-bypassing awaken/ascend/enhance/active-skill plus 3 unlocked karma slots, Skip All DM Cutscenes to clear the queued DM-acquisition cutscene loop, Fill All Karma Slots with rarest-or-chosen effect per slot). |
| 5 | Memoir Editor |  Done | R40 memoir grants and edits. **Build a Set** grants the 3 memoirs of any of the 18 sets at lv15 with caller-chosen primary main-stat (one of 6 percent/Agility tier-4 options) and 4 sub-stat slots (perfect-roll defaults editable). **Upgrade All Memoirs** sweeps every owned memoir to lv15. **Fix Slots** rewrites the 4 sub-status rows on a single memoir. 999-memoir inventory cap pre-flighted. |

---

## Architecture

```
lunar-base\
├── web\          Python (FastAPI + Jinja2) — UI and orchestration
├── tools\        Supporting scripts and the Go shim
│   ├── extract_names.py       Resolves entity IDs to English names from lunar-tear's text bundles
│   └── grant\
│       ├── src\               Go source for the lunar-base-grant shim
│       └── grant[.exe]        Compiled binary (built by setup.bat / setup.sh, gitignored;
│                              named grant.exe on Windows, grant elsewhere)
└── data\         Gitignored — master-data JSON, name maps, and DB backups
```

- **`web\`** reads `game.db` directly via the `sqlite3` standard library; all mutations shell out to the Go shim.
- **`tools\grant\grant.exe`** (or `tools/grant/grant` on Linux/macOS) reads one JSON request from stdin and writes one JSON response to stdout. Implemented actions: `grant_possession`, `grant_batch`, `grant_costume_batch`, `grant_weapon_batch`, `grant_companion_batch`, `grant_thought_batch`, `exalt_characters`, `release_panels`, `upgrade_all_companions`, `upgrade_all_weapons`, `upgrade_all_costumes`, `fill_karma_slots`, `set_costume_karma_batch`, `grant_memoir_batch`, `upgrade_all_memoirs`, `set_memoir_subs_batch`, `mark_contents_stories_played`.
- **`tools\extract_names.py`** is adapted from Engels (used with permission).

### How the Go shim is built

Lunar Base never modifies lunar-tear's source tree, but the shim must import lunar-tear's internal grant code. Go's `internal/` package rule requires the importing code to live inside `lunar-tear/server/`, so the setup script (`setup.bat` on Windows, `setup.sh` on Linux/macOS) does the following each run:

1. Copies `tools\grant\src\*.go` into `..\lunar-tear\server\cmd\lunar-base-grant\` (creating it if needed). The `lunar-base-grant` name is distinct from lunar-tear's own commands so its origin is obvious.
2. Runs `go build` against that directory and writes `grant.exe` back to `tools\grant\grant.exe` inside lunar-base.

The `lunar-base-grant\` directory will appear under `lunar-tear\server\cmd\` after running `setup.bat` — this is expected. Lunar Base does not edit, delete, or version-control anything else in lunar-tear's tree.

> If `go` is not on your PATH, the build is skipped with a warning and stages 1+ will not work. Install Go 1.25+ and re-run `setup.bat`.

---

## Safety

- Lunar Base **only writes** to `..\lunar-tear\server\db\game.db` and `..\lunar-tear\server\cmd\lunar-base-grant\`. No other files in `lunar-tear\` or `lunar-scripts\` are touched.
- Every mutation takes an **automatic backup** beforehand, filed under `data\backups\` with a reason tag (`item-editor`, `costume-editor`, `weapon-editor`, `upgrade-manager`, `memoir-editor`, `pre-restore`, or `manual`). Backups are pruned to the 50 most recent of any kind.
- **Restore refuses** if it detects lunar-tear is running, preventing active database corruption.
- All grants are **additive** — Lunar Base never decreases quantities. Roll back via backup if needed.

---

## License

[MIT](LICENSE)

---

## Legal Disclaimer

Lunar Tear is a fan-made, non-commercial preservation and research project dedicated to keeping a certain discontinued mobile game playable for educational and archival purposes.

This project is not affiliated with, endorsed by, or approved by the original publisher or any of its subsidiaries. All trademarks, copyrights, and intellectual property related to the original game and its associated franchises belong to their respective owners. All code in this repository is original work developed through clean-room reverse engineering for interoperability with the game client. No copyrighted game assets, binaries, or master data are distributed in this repository.

Use at your own risk. The author assumes no liability for any damages or legal consequences that may arise from using this software. By using or contributing to this project, you are solely responsible for ensuring your usage complies with all applicable laws in your jurisdiction.

If you are a rights holder with concerns regarding this project, please contact the me directly.
