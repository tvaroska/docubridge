"""Pure-Python MS-CFB (OLE2) binary compound file reader and MAPI stream decoders.

Supports bounded stream extraction and sibling red-black directory traversal for
Outlook ``.msg`` submission envelopes without third-party C or PyPI dependencies.
"""

from __future__ import annotations

import struct

OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ENDOFCHAIN = 0xFFFFFFFE
_FREESECT = 0xFFFFFFFF
_SPECIAL_SECTORS = frozenset({0xFFFFFFFC, 0xFFFFFFFD, _ENDOFCHAIN, _FREESECT})


class Ole2DirEntry:
    """Directory entry node inside an MS-CFB / OLE2 binary compound file."""

    __slots__ = (
        "child_id",
        "left_sibling",
        "name",
        "obj_type",
        "right_sibling",
        "start_sector",
        "stream_size",
    )

    def __init__(
        self,
        name: str,
        obj_type: int,
        left_sibling: int,
        right_sibling: int,
        child_id: int,
        start_sector: int,
        stream_size: int,
    ) -> None:
        """Initialize an OLE2 directory entry descriptor."""
        self.name = name
        self.obj_type = obj_type
        self.left_sibling = left_sibling
        self.right_sibling = right_sibling
        self.child_id = child_id
        self.start_sector = start_sector
        self.stream_size = stream_size


class Ole2CompoundReader:
    """Pure-Python MS-CFB (OLE2) binary compound file reader for ``.msg`` envelopes."""

    def __init__(self, data: bytes, *, max_stream_bytes: int) -> None:
        """Parse the MS-CFB header, DIFAT, FAT, MiniFAT, and directory stream."""
        if len(data) < 512 or data[:8] != OLE2_MAGIC:
            raise ValueError("Invalid OLE2/CFBF header magic bytes")
        self._data = data
        self._max_stream_bytes = max_stream_bytes

        sector_shift = struct.unpack_from("<H", data, 30)[0]
        mini_shift = struct.unpack_from("<H", data, 32)[0]
        if sector_shift not in (9, 12) or mini_shift != 6:
            raise ValueError(
                f"Unsupported OLE2 sector shifts: sector={sector_shift}, mini={mini_shift}"
            )
        self.sector_size = 1 << sector_shift
        self.mini_sector_size = 1 << mini_shift
        num_fat_sectors = struct.unpack_from("<I", data, 44)[0]
        first_dir_sector = struct.unpack_from("<I", data, 48)[0]
        self.mini_cutoff = struct.unpack_from("<I", data, 56)[0]
        first_minifat_sector = struct.unpack_from("<I", data, 60)[0]
        num_minifat_sectors = struct.unpack_from("<I", data, 64)[0]
        first_difat_sector = struct.unpack_from("<I", data, 68)[0]
        num_difat_sectors = struct.unpack_from("<I", data, 72)[0]

        difat: list[int] = [
            s
            for s in struct.unpack_from("<109I", data, 76)
            if s not in _SPECIAL_SECTORS
        ]
        curr_difat = first_difat_sector
        entries_per_sector = self.sector_size // 4
        for _ in range(min(num_difat_sectors, 1024)):
            if curr_difat in _SPECIAL_SECTORS:
                break
            sec_bytes = self._sector_bytes(curr_difat)
            vals = struct.unpack(f"<{entries_per_sector}I", sec_bytes)
            difat.extend(s for s in vals[:-1] if s not in _SPECIAL_SECTORS)
            curr_difat = vals[-1]

        self.fat: list[int] = []
        for fat_sec in difat[:num_fat_sectors]:
            sec_bytes = self._sector_bytes(fat_sec)
            self.fat.extend(struct.unpack(f"<{entries_per_sector}I", sec_bytes))

        self.minifat: list[int] = []
        curr_mini = first_minifat_sector
        for _ in range(min(num_minifat_sectors, 4096)):
            if curr_mini in _SPECIAL_SECTORS:
                break
            sec_bytes = self._sector_bytes(curr_mini)
            self.minifat.extend(struct.unpack(f"<{entries_per_sector}I", sec_bytes))
            curr_mini = self._next_sector(self.fat, curr_mini)

        dir_bytes = self._read_chain(
            first_dir_sector, size=None, max_bytes=max_stream_bytes
        )
        self.entries: list[Ole2DirEntry] = []
        for offset in range(0, len(dir_bytes) - 127, 128):
            chunk = dir_bytes[offset : offset + 128]
            name_len = struct.unpack_from("<H", chunk, 64)[0]
            obj_type = chunk[66]
            if obj_type == 0 or name_len < 2 or name_len > 64:
                name = ""
            else:
                name = chunk[: name_len - 2].decode("utf-16-le", errors="replace")
            left_sib, right_sib, child_id = struct.unpack_from("<3I", chunk, 68)
            start_sec = struct.unpack_from("<I", chunk, 116)[0]
            raw_size = struct.unpack_from("<Q", chunk, 120)[0]
            if self.sector_size == 512:
                # MS-CFB v3: upper 32 bits of stream_size may contain uninitialized bytes.
                raw_size &= 0xFFFFFFFF
            self.entries.append(
                Ole2DirEntry(
                    name=name,
                    obj_type=obj_type,
                    left_sibling=left_sib,
                    right_sibling=right_sib,
                    child_id=child_id,
                    start_sector=start_sec,
                    stream_size=raw_size,
                )
            )

        self._mini_stream = b""
        if self.entries and self.entries[0].obj_type == 5:
            root = self.entries[0]
            if root.stream_size > 0 and root.start_sector not in _SPECIAL_SECTORS:
                self._mini_stream = self._read_chain(
                    root.start_sector,
                    size=root.stream_size,
                    max_bytes=max_stream_bytes,
                )

    def _sector_bytes(self, sector_id: int) -> bytes:
        start = (sector_id + 1) * self.sector_size
        end = start + self.sector_size
        if start < 0 or end > len(self._data):
            raise ValueError(f"OLE2 sector {sector_id} out of bounds")
        return self._data[start:end]

    @staticmethod
    def _next_sector(table: list[int], sector_id: int) -> int:
        if 0 <= sector_id < len(table):
            return table[sector_id]
        return _ENDOFCHAIN

    def _read_chain(
        self, start_sector: int, *, size: int | None, max_bytes: int
    ) -> bytes:
        if size == 0 or start_sector in _SPECIAL_SECTORS:
            return b""
        if size is not None and size > max_bytes:
            raise ValueError(
                f"OLE2 stream size {size} exceeds max_entry_bytes={max_bytes}"
            )
        out = bytearray()
        curr = start_sector
        visited: set[int] = set()
        while curr not in _SPECIAL_SECTORS:
            if curr in visited:
                raise ValueError("Cycle detected in OLE2 FAT chain")
            visited.add(curr)
            out.extend(self._sector_bytes(curr))
            if len(out) > max_bytes and (size is None or size > max_bytes):
                raise ValueError(f"OLE2 stream exceeds max_entry_bytes={max_bytes}")
            if size is not None and len(out) >= size:
                return bytes(out[:size])
            curr = self._next_sector(self.fat, curr)
        return bytes(out[:size] if size is not None else out)

    def read_stream(self, entry: Ole2DirEntry) -> bytes:
        """Read the byte payload of a regular or mini-FAT stream entry."""
        if entry.stream_size == 0 or entry.start_sector in _SPECIAL_SECTORS:
            return b""
        if entry.stream_size > self._max_stream_bytes:
            raise ValueError(
                f"OLE2 stream {entry.name!r} ({entry.stream_size} bytes) exceeds limit"
            )
        if entry.obj_type != 5 and entry.stream_size < self.mini_cutoff:
            out = bytearray()
            curr = entry.start_sector
            visited: set[int] = set()
            while curr not in _SPECIAL_SECTORS and len(out) < entry.stream_size:
                if curr in visited:
                    raise ValueError("Cycle detected in OLE2 MiniFAT chain")
                visited.add(curr)
                start = curr * self.mini_sector_size
                end = start + self.mini_sector_size
                if end > len(self._mini_stream):
                    raise ValueError("OLE2 mini-sector out of bounds")
                out.extend(self._mini_stream[start:end])
                curr = self._next_sector(self.minifat, curr)
            return bytes(out[: entry.stream_size])
        return self._read_chain(
            entry.start_sector,
            size=entry.stream_size,
            max_bytes=self._max_stream_bytes,
        )

    def list_children(self, storage_entry: Ole2DirEntry) -> list[Ole2DirEntry]:
        """Traverse the sibling red-black tree to list direct children of ``storage_entry``."""
        if storage_entry.child_id == _FREESECT:
            return []
        children: list[Ole2DirEntry] = []
        visited: set[int] = set()

        def _walk(idx: int) -> None:
            if idx == _FREESECT or idx in visited or not (0 <= idx < len(self.entries)):
                return
            visited.add(idx)
            node = self.entries[idx]
            _walk(node.left_sibling)
            if node.obj_type in (1, 2):
                children.append(node)
            _walk(node.right_sibling)

        _walk(storage_entry.child_id)
        return children


def decode_mapi_text(streams: dict[str, bytes], prop_tag_hex: str) -> str:
    """Decode a MAPI string property from either Unicode (``001F``) or 8-bit (``001E``)."""
    uni_key = f"__SUBSTG1.0_{prop_tag_hex.upper()}001F"
    for k, raw in streams.items():
        if k.upper() == uni_key:
            return raw.decode("utf-16-le", errors="replace").rstrip("\x00")
    ansi_key = f"__SUBSTG1.0_{prop_tag_hex.upper()}001E"
    for k, raw in streams.items():
        if k.upper() == ansi_key:
            return raw.decode("utf-8", errors="replace").rstrip("\x00")
    return ""


def decode_mapi_binary(streams: dict[str, bytes], prop_tag_hex: str) -> bytes | None:
    """Return raw binary bytes for a MAPI ``PT_BINARY`` (``0102``) property stream."""
    bin_key = f"__SUBSTG1.0_{prop_tag_hex.upper()}0102"
    for k, raw in streams.items():
        if k.upper() == bin_key:
            return raw
    return None


__all__ = [
    "OLE2_MAGIC",
    "Ole2CompoundReader",
    "Ole2DirEntry",
    "decode_mapi_binary",
    "decode_mapi_text",
]
