# -*- coding: utf-8 -*-
"""
固件文件加载器，只支持 .bin 文件
返回连续的二进制数据
"""

import os
from typing import Optional


class FirmwareLoaderError(Exception):
    pass


def load_firmware(file_path: str) -> bytes:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".bin":
        return _load_bin(file_path)
    else:
        raise FirmwareLoaderError(f"Unsupported file type: {ext}. Only .bin files are supported.")


def _load_bin(file_path: str) -> bytes:
    with open(file_path, "rb") as f:
        return f.read()


def _load_bin(file_path: str) -> bytes:
    with open(file_path, "rb") as f:
        return f.read()


def _load_hex(file_path: str) -> bytes:
    if IntelHex is None:
        raise FirmwareLoaderError("intelhex not installed. Run: pip install intelhex")
    ih = IntelHex(file_path)
    start = ih.minaddr()
    end = ih.maxaddr()
    if start is None or end is None:
        raise FirmwareLoaderError("No data in HEX file")
    return bytes(ih.tobinarray(start=start, size=end - start + 1))


def _load_elf(file_path: str) -> bytes:
    if ELFFile is None:
        raise FirmwareLoaderError("pyelftools not installed. Run: pip install pyelftools")
    with open(file_path, "rb") as f:
        elf = ELFFile(f)
        segments = []
        for seg in elf.iter_segments():
            if seg.header.p_type == "PT_LOAD":
                segments.append({
                    "vaddr": seg.header.p_vaddr,
                    "data": seg.data(),
                    "filesz": seg.header.p_filesz
                })
        if not segments:
            raise FirmwareLoaderError("No loadable segments in ELF")
        segments.sort(key=lambda s: s["vaddr"])
        min_addr = segments[0]["vaddr"]
        max_addr = segments[-1]["vaddr"] + len(segments[-1]["data"])
        image = bytearray(b'\xFF') * (max_addr - min_addr)
        for seg in segments:
            offset = seg["vaddr"] - min_addr
            data_len = len(seg["data"])
            image[offset:offset+data_len] = seg["data"]
        return bytes(image)