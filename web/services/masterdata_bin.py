"""Read and repack the encrypted master-data bin (the file the game server
actually loads).

The lunar-tear server reads ONLY the encrypted MasterMemory bin
(assets/release/*.bin.e); the decoded JSON under data/masterdata/ is ignored.
So toggling event/banner availability for real means editing this bin: decrypt
(AES-256-CBC) -> walk the msgpack TOC -> mutate the int64 datetime columns of a
table in place -> re-pack (preserving each table's LZ4 ext framing) -> re-encrypt.

In-place int64 mutation matters: msgpack.packb re-encodes C#'s int64 columns
with Python's tighter encodings, producing byte-different blobs the client's
schema validator rejects. So writes patch the original bytes directly; only
read-only inspection uses msgpack.unpackb.

Ported from lunar-scripts/patch_masterdata.py (proven against the live bin).
"""

from __future__ import annotations

import struct
import time
from datetime import datetime
from pathlib import Path

import lz4.block
import msgpack
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from web import config

# Same AES key/IV MasterMemory ships with for this title.
_KEY = bytes.fromhex("36436230313332314545356536624265")
_IV = bytes.fromhex("45666341656634434165356536446141")

# Far-future end used by events that are already open (~2031-01-01 UTC) and a
# safe "long ago" timestamp (2000-01-01 UTC) for an open start / a closed end.
ACTIVE_END: int = 1924991999000
PAST: int = 946684800000


# --- LZ4 / ext-type helpers ---

def _read_lz4_ext_header(ext_data: bytes):
    """Strip the msgpack int prefix C#'s LZ4MessagePack writes before the LZ4 bytes."""
    tag = ext_data[0]
    if tag == 0xd2: return struct.unpack(">i", ext_data[1:5])[0], ext_data[5:]
    if tag == 0xce: return struct.unpack(">I", ext_data[1:5])[0], ext_data[5:]
    if tag == 0xd1: return struct.unpack(">h", ext_data[1:3])[0], ext_data[3:]
    if tag == 0xcd: return struct.unpack(">H", ext_data[1:3])[0], ext_data[3:]
    if tag <= 0x7f: return tag, ext_data[1:]
    raise ValueError(f"Unexpected msgpack tag 0x{tag:02x} in LZ4 ext header")


def _build_lz4_ext_blob(decompressed: bytes) -> bytes:
    compressed = lz4.block.compress(decompressed, store_size=False)
    header = b"\xd2" + struct.pack(">i", len(decompressed))
    return msgpack.packb(msgpack.ExtType(99, header + compressed), use_bin_type=True)


# --- msgpack binary walker ---

def _skip(data, pos):
    tag = data[pos]
    if tag <= 0x7f or tag >= 0xe0: return pos + 1
    if 0xa0 <= tag <= 0xbf:        return pos + 1 + (tag & 0x1f)
    if 0x90 <= tag <= 0x9f:
        p = pos + 1
        for _ in range(tag & 0x0f): p = _skip(data, p)
        return p
    if 0x80 <= tag <= 0x8f:
        p = pos + 1
        for _ in range((tag & 0x0f) * 2): p = _skip(data, p)
        return p
    fixed = {0xc0: 1, 0xc2: 1, 0xc3: 1, 0xca: 5, 0xcb: 9, 0xcc: 2, 0xcd: 3,
             0xce: 5, 0xcf: 9, 0xd0: 2, 0xd1: 3, 0xd2: 5, 0xd3: 9, 0xd4: 3,
             0xd5: 4, 0xd6: 6, 0xd7: 10, 0xd8: 18}
    if tag in fixed: return pos + fixed[tag]
    length_prefixed = {0xc4: (1, "B"), 0xc5: (2, ">H"), 0xc6: (4, ">I"),
                       0xd9: (1, "B"), 0xda: (2, ">H"), 0xdb: (4, ">I"),
                       0xc7: (1, "B"), 0xc8: (2, ">H"), 0xc9: (4, ">I")}
    if tag in length_prefixed:
        sz, fmt = length_prefixed[tag]
        n = struct.unpack(fmt, data[pos + 1:pos + 1 + sz])[0]
        extra = 1 if tag in (0xc7, 0xc8, 0xc9) else 0
        return pos + 1 + sz + extra + n
    array_map = {0xdc: (2, ">H"), 0xdd: (4, ">I"), 0xde: (2, ">H"), 0xdf: (4, ">I")}
    if tag in array_map:
        sz, fmt = array_map[tag]
        n = struct.unpack(fmt, data[pos + 1:pos + 1 + sz])[0]
        items = n * 2 if tag in (0xde, 0xdf) else n
        p = pos + 1 + sz
        for _ in range(items): p = _skip(data, p)
        return p
    raise ValueError(f"Unknown msgpack tag 0x{tag:02x} at pos {pos}")


def _read_array_len(data, pos):
    tag = data[pos]
    if 0x90 <= tag <= 0x9f: return tag & 0x0f, pos + 1
    if tag == 0xdc:         return struct.unpack(">H", data[pos + 1:pos + 3])[0], pos + 3
    if tag == 0xdd:         return struct.unpack(">I", data[pos + 1:pos + 5])[0], pos + 5
    raise ValueError(f"Expected array at pos {pos}, got tag 0x{tag:02x}")


def _read_int(data, pos):
    tag = data[pos]
    if tag <= 0x7f: return tag
    if tag == 0xcc: return data[pos + 1]
    if tag == 0xcd: return struct.unpack(">H", data[pos + 1:pos + 3])[0]
    if tag == 0xce: return struct.unpack(">I", data[pos + 1:pos + 5])[0]
    if tag == 0xd3: return struct.unpack(">q", data[pos + 1:pos + 9])[0]
    raise ValueError(f"read_int: unexpected tag 0x{tag:02x} at pos {pos}")


# --- bin decode / encode ---

def bin_path() -> Path:
    p = config.find_master_data_bin()
    if p is None:
        raise FileNotFoundError(
            "No *.bin.e found under <lunar-tear>/server/assets/release/. "
            "Point LUNAR_TEAR_DIR at the server folder that holds the running bin."
        )
    return p


def _decrypt(raw: bytes) -> bytes:
    return unpad(AES.new(_KEY, AES.MODE_CBC, _IV).decrypt(raw), AES.block_size)


def _parse(decrypted: bytes):
    """Split the decrypted file into (toc, data_blob)."""
    try:
        return msgpack.unpackb(decrypted, raw=False, strict_map_key=False), b""
    except msgpack.ExtraData as e:
        return e.unpacked, e.extra


def _table_bytes(toc, data_blob, name):
    """Return (bytearray of the decompressed table, was_lz4_compressed)."""
    if name not in toc:
        raise KeyError(f"table {name!r} not in master data")
    offset, length = toc[name]
    src = data_blob[offset:offset + length]
    unpacked = msgpack.unpackb(src, raw=True)
    if isinstance(unpacked, msgpack.ExtType) and unpacked.code == 99:
        unc_len, lz4_data = _read_lz4_ext_header(unpacked.data)
        return bytearray(lz4.block.decompress(lz4_data, uncompressed_size=unc_len)), True
    return bytearray(src), False


def decode_rows(name: str) -> list[list]:
    """Decode a whole table to a list of rows (each a list of column values)."""
    toc, data_blob = _parse(_decrypt(bin_path().read_bytes()))
    table, _ = _table_bytes(toc, data_blob, name)
    return msgpack.unpackb(bytes(table), raw=True)


# --- window read / write ---

def is_active(start: int, end: int, now_ms: int) -> bool:
    return (start == 0 or now_ms >= start) and (end == 0 or now_ms < end)


def read_windows(name: str, id_col: int, start_col: int, end_col: int,
                 now_ms: int | None = None) -> list[dict]:
    """Read id / start / end / active for every row of a table."""
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    out: list[dict] = []
    for row in decode_rows(name):
        start = int(row[start_col] or 0) if len(row) > start_col else 0
        end = int(row[end_col] or 0) if len(row) > end_col else 0
        out.append({
            "id": int(row[id_col]),
            "start": start,
            "end": end,
            "active": is_active(start, end, now),
        })
    return out


def _set_windows_blob(table: bytearray, id_col, start_col, end_col,
                      active_ids, managed_ids, now_ms):
    """Mutate the decompressed table in place. Rows whose id is in `managed_ids`
    (or all rows if managed_ids is None) get an open window when active, a past
    End otherwise; rows outside managed_ids are left exactly as-is. Returns
    (activated, deactivated)."""
    activated = deactivated = 0
    row_count, pos = _read_array_len(table, 0)
    for _ in range(row_count):
        col_count, p = _read_array_len(table, pos)
        row_id = _read_int(table, p)  # id is col 0 by convention here
        cp = p
        if managed_ids is not None and row_id not in managed_ids:
            for _ in range(col_count):  # untouched row: just advance past it
                cp = _skip(table, cp)
            pos = cp
            continue
        keep = row_id in active_ids
        for ci in range(col_count):
            if table[cp] == 0xd3:  # int64 column
                if keep and ci == end_col:
                    struct.pack_into(">q", table, cp + 1, ACTIVE_END)
                    activated += 1
                elif keep and ci == start_col:
                    cur = struct.unpack(">q", table[cp + 1:cp + 9])[0]
                    if cur > now_ms:  # parked in the future -> pull into the past
                        struct.pack_into(">q", table, cp + 1, PAST)
                elif (not keep) and ci == end_col:
                    struct.pack_into(">q", table, cp + 1, PAST)
                    deactivated += 1
            cp = _skip(table, cp)
        pos = cp
    return activated, deactivated


def _dated_backup(path: Path) -> Path:
    """Copy the bin to <name>.<YYYYMMDD-HHMMSS>.bak next to it. Returns the backup path."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(path.name + f".{stamp}.bak")
    backup.write_bytes(path.read_bytes())
    return backup


def apply_windows(specs: list[dict], now_ms: int | None = None) -> dict:
    """Repack the bin so each spec's `active_ids` are the only active rows among
    its `managed_ids`. specs: [{table, id_col, start_col, end_col,
    active_ids: set[int], managed_ids: set[int] | None}]. Rows outside
    managed_ids are left untouched. Backs the old bin up (dated) before
    overwriting. Returns a summary dict.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    path = bin_path()
    decrypted = _decrypt(path.read_bytes())
    toc, data_blob = _parse(decrypted)
    if not isinstance(toc, dict):
        raise ValueError("unexpected master-data header (no table-of-contents)")

    new_blobs: dict[str, bytes] = {}
    results = []
    for spec in specs:
        name = spec["table"]
        table, compressed = _table_bytes(toc, data_blob, name)
        managed = spec.get("managed_ids")
        activated, deactivated = _set_windows_blob(
            table, spec["id_col"], spec["start_col"], spec["end_col"],
            set(spec["active_ids"]), set(managed) if managed is not None else None, now)
        new_blobs[name] = _build_lz4_ext_blob(bytes(table)) if compressed else bytes(table)
        results.append({"table": name, "activated": activated, "deactivated": deactivated})

    # Rebuild: tables in TOC order, swapping in the mutated blobs.
    sorted_tables = sorted(toc.items(), key=lambda kv: kv[1][0])
    new_toc, parts, offset = {}, [], 0
    for tname, (o, length) in sorted_tables:
        part = new_blobs[tname] if tname in new_blobs else data_blob[o:o + length]
        new_toc[tname] = (offset, len(part))
        parts.append(part)
        offset += len(part)
    new_decrypted = msgpack.packb(new_toc, use_bin_type=True) + b"".join(parts)
    re_encrypted = AES.new(_KEY, AES.MODE_CBC, _IV).encrypt(pad(new_decrypted, AES.block_size))

    backup = _dated_backup(path)
    path.write_bytes(re_encrypted)
    return {"bin": str(path), "backup": str(backup), "tables": results}
