#!/usr/bin/env python3
"""Decrypt Ximalaya offline audio and convert to MP3."""

from __future__ import annotations

import argparse
import base64
import io
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from mutagen.easyid3 import ID3
from mutagen.id3 import ID3NoHeaderError


XM_AES_KEY = b"ximalayaximalayaximalayaximalaya"
XM_KEY_PADDING = b"123456781234567812345678"
OUTPUT_DIR = Path("output")


@dataclass
class XMInfo:
    title: str = ""
    artist: str = ""
    album: str = ""
    tracknumber: int = 0
    size: int = 0
    header_size: int = 0
    isrc: str = ""
    encodedby: str = ""
    encoding_technology: str = ""

    def iv(self) -> bytes:
        if self.isrc:
            return bytes.fromhex(self.isrc)
        return bytes.fromhex(self.encodedby)


def get_printable_bytes(data: bytes) -> bytes:
    for index, value in enumerate(data):
        if value < 0x20 or value > 0x7E:
            return data[:index]
    return data


def get_xm_info(data: bytes) -> XMInfo:
    id3 = ID3(io.BytesIO(data), v2_version=3)
    info = XMInfo()
    info.title = str(id3["TIT2"])
    info.album = str(id3["TALB"])
    info.artist = str(id3["TPE1"])
    info.tracknumber = int(str(id3["TRCK"]))
    info.isrc = "" if id3.get("TSRC") is None else str(id3["TSRC"])
    info.encodedby = "" if id3.get("TENC") is None else str(id3["TENC"])
    info.size = int(str(id3["TSIZ"]))
    info.header_size = id3.size
    info.encoding_technology = str(id3["TSSE"])
    return info


def decrypt_xm_payload(raw_data: bytes) -> tuple[XMInfo, bytes]:
    xm_info = get_xm_info(raw_data)
    start = xm_info.header_size
    end = start + xm_info.size
    encrypted_data = raw_data[start:end]

    cipher = AES.new(XM_AES_KEY, AES.MODE_CBC, xm_info.iv())
    stage1 = cipher.decrypt(pad(encrypted_data, 16))

    stage2_input = base64.b64decode(get_printable_bytes(stage1))
    track_key = str(xm_info.tracknumber).encode()
    aes_key = XM_KEY_PADDING[: 0x18 - len(track_key)] + track_key
    cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
    stage2 = unpad(cipher.decrypt(stage2_input), 16).decode()

    decrypted = base64.b64decode(xm_info.encoding_technology + stage2)
    tail = raw_data[end:]
    return xm_info, decrypted + tail


def detect_container(data: bytes) -> str:
    if data.startswith(b"ID3"):
        return "xm"
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return "m4a"
    if data.startswith(b"fLaC"):
        return "flac"
    if data[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return "mp3"
    return "unknown"


def probe_audio(path: Path) -> bool:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and "audio" in result.stdout


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|｜：]', "_", name).strip()
    return cleaned or "output"


def convert_to_mp3(source: Path, output: Path, *, bitrate: str = "192k") -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("未找到 ffmpeg，请先安装后再运行")

    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-acodec",
            "libmp3lame",
            "-b:a",
            bitrate,
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffmpeg 转码失败")


def write_temp_audio(data: bytes, suffix: str) -> Path:
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    temp.write(data)
    temp.close()
    return Path(temp.name)


def decrypt_file(
    input_path: Path,
    output_path: Path | None = None,
    *,
    bitrate: str = "192k",
) -> Path:
    raw_data = input_path.read_bytes()
    container = detect_container(raw_data)

    if container == "xm":
        info, audio_data = decrypt_xm_payload(raw_data)
        suffix = ".m4a" if audio_data[4:8] == b"ftyp" else ".mp3"
        temp_source = write_temp_audio(audio_data, suffix)
        try:
            default_name = sanitize_filename(info.title or input_path.stem)
            target = output_path or OUTPUT_DIR / f"{default_name}.mp3"
            convert_to_mp3(temp_source, target, bitrate=bitrate)
            return target
        finally:
            temp_source.unlink(missing_ok=True)

    if container in {"m4a", "mp3", "flac"}:
        if not probe_audio(input_path):
            raise RuntimeError(
                f"文件看起来像 {container}，但 ffprobe 无法识别音频流，"
                "可能已损坏或使用了尚未支持的加密格式"
            )
        target = output_path or OUTPUT_DIR / f"{input_path.stem}.mp3"
        convert_to_mp3(input_path, target, bitrate=bitrate)
        return target

    if raw_data.startswith(b"ID3"):
        raise RuntimeError("检测到 ID3 头，但解析 XM 元数据失败")

    raise RuntimeError(
        "无法识别文件格式。支持：喜马拉雅 .xm/.x2m，以及可直接解码的 m4a/mp3/flac"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="解密喜马拉雅离线音频并转换为 MP3"
    )
    parser.add_argument(
        "input",
        type=Path,
        help="输入文件路径（.xm / .x2m / .m4a / 无后缀缓存文件）",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="输出 MP3 路径（默认 output/ 目录）",
    )
    parser.add_argument(
        "-b",
        "--bitrate",
        default="192k",
        help="MP3 码率，默认 192k",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input.expanduser().resolve()

    if not input_path.is_file():
        print(f"错误：找不到输入文件 {input_path}", file=sys.stderr)
        return 1

    try:
        output = decrypt_file(input_path, args.output, bitrate=args.bitrate)
    except ID3NoHeaderError:
        print("错误：文件不是有效的喜马拉雅 XM 格式", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    print(f"完成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
