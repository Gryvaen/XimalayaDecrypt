#!/usr/bin/env python3
"""Transcribe audio files (MP3, M4A, etc.) to text."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

MODEL_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
}

DEFAULT_HF_ENDPOINT = "https://huggingface.co"
HF_MIRROR_ENDPOINT = "https://hf-mirror.com"
OUTPUT_DIR = Path("output")


def format_srt_timestamp(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds % 1) * 1000))
    if millis == 1000:
        secs += 1
        millis = 0
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def segments_to_text(segments: list, *, with_timestamps: bool) -> str:
    lines: list[str] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        if with_timestamps:
            start = format_srt_timestamp(segment.start).replace(",", ".")
            lines.append(f"[{start}] {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


def segments_to_srt(segments: list) -> str:
    blocks: list[str] = []
    index = 1
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        blocks.append(
            f"{index}\n"
            f"{format_srt_timestamp(segment.start)} --> "
            f"{format_srt_timestamp(segment.end)}\n"
            f"{text}\n"
        )
        index += 1
    return "\n".join(blocks)


def cuda_is_usable() -> bool:
    try:
        import ctypes

        import ctranslate2

        if ctranslate2.get_cuda_device_count() <= 0:
            return False
        ctypes.CDLL("libcublas.so.12")
        return True
    except Exception:
        return False


def resolve_runtime(device: str, compute_type: str | None) -> tuple[str, str]:
    if device == "auto":
        if cuda_is_usable():
            return "cuda", compute_type or "float16"
        return "cpu", compute_type or "int8"

    if compute_type:
        return device, compute_type
    return device, "float16" if device == "cuda" else "int8"


def configure_hf_endpoint(endpoint: str | None) -> None:
    if endpoint:
        os.environ["HF_ENDPOINT"] = endpoint.rstrip("/")


def download_whisper_model(
    model: str,
    *,
    model_path: Path | None = None,
    download_root: Path | None = None,
    hf_endpoint: str | None = None,
) -> str:
    if model_path is not None:
        if not model_path.exists():
            raise FileNotFoundError(f"本地模型目录不存在：{model_path}")
        return str(model_path)

    candidate = Path(model)
    if candidate.exists():
        return str(candidate)

    repo_id = MODEL_REPOS.get(model, model)
    configure_hf_endpoint(hf_endpoint)

    from huggingface_hub import snapshot_download

    cache_dir = str(download_root) if download_root else None
    endpoints = [hf_endpoint] if hf_endpoint else [DEFAULT_HF_ENDPOINT, HF_MIRROR_ENDPOINT]
    last_error: Exception | None = None

    for endpoint in endpoints:
        configure_hf_endpoint(endpoint)
        try:
            return snapshot_download(
                repo_id,
                cache_dir=cache_dir,
                max_workers=1,
            )
        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        "Whisper 模型下载失败。可尝试：\n"
        "  1. 设置镜像：export HF_ENDPOINT=https://hf-mirror.com\n"
        "  2. 手动下载后指定：python transcribe.py audio.mp3 --model-path ./models/faster-whisper-small\n"
        f"原始错误：{last_error}"
    ) from last_error


def load_whisper_model(
    model: str,
    *,
    model_path: Path | None = None,
    download_root: Path | None = None,
    hf_endpoint: str | None = None,
    device: str,
    compute_type: str,
):
    from faster_whisper import WhisperModel

    local_model = download_whisper_model(
        model,
        model_path=model_path,
        download_root=download_root,
        hf_endpoint=hf_endpoint,
    )
    try:
        return WhisperModel(
            local_model,
            device=device,
            compute_type=compute_type,
            local_files_only=True,
        )
    except Exception as exc:
        if device == "cpu":
            raise
        print(
            f"警告：{device} 不可用（{exc}），回退到 CPU",
            file=sys.stderr,
        )
        fallback_compute = "int8" if compute_type != "int8" else compute_type
        return WhisperModel(
            local_model,
            device="cpu",
            compute_type=fallback_compute,
            local_files_only=True,
        )


def transcribe_file(
    input_path: Path,
    *,
    model: str = "small",
    language: str = "zh",
    device: str = "auto",
    compute_type: str | None = None,
    with_timestamps: bool = False,
    model_path: Path | None = None,
    download_root: Path | None = None,
    hf_endpoint: str | None = None,
) -> tuple[str, str, object, list]:
    runtime_device, runtime_compute = resolve_runtime(device, compute_type)
    whisper = load_whisper_model(
        model,
        model_path=model_path,
        download_root=download_root,
        hf_endpoint=hf_endpoint,
        device=runtime_device,
        compute_type=runtime_compute,
    )

    segments_iter, info = whisper.transcribe(
        str(input_path),
        language=language,
        vad_filter=True,
    )
    segments = list(segments_iter)
    plain_text = segments_to_text(segments, with_timestamps=False)
    display_text = segments_to_text(segments, with_timestamps=with_timestamps)
    srt_text = segments_to_srt(segments)
    return display_text, srt_text, info, segments


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将音频文件转写为文字")
    parser.add_argument("input", type=Path, help="输入音频路径（mp3 / m4a / wav 等）")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="输出文本路径，默认 output/ 目录下与输入同名的 .txt",
    )
    parser.add_argument(
        "--srt",
        nargs="?",
        const="",
        default=None,
        help="输出 SRT 字幕；不跟路径时默认写入 output/ 目录",
    )
    parser.add_argument(
        "-m",
        "--model",
        default="small",
        choices=["tiny", "base", "small", "medium", "large-v3"],
        help="Whisper 模型大小，默认 small（中文效果与速度较均衡）",
    )
    parser.add_argument(
        "-l",
        "--language",
        default="zh",
        help="语言代码，默认 zh（中文）",
    )
    parser.add_argument(
        "--device",
        default="auto",
        choices=["auto", "cpu", "cuda"],
        help="推理设备，默认 auto",
    )
    parser.add_argument(
        "--compute-type",
        default=None,
        help="计算精度，如 int8 / float16；默认 CPU 用 int8，GPU 用 float16",
    )
    parser.add_argument(
        "-t",
        "--timestamps",
        action="store_true",
        help="在文本中附带每段起始时间戳",
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        default=None,
        help="本地 Whisper 模型目录（跳过在线下载）",
    )
    parser.add_argument(
        "--download-root",
        type=Path,
        default=None,
        help="模型缓存目录，默认 ~/.cache/huggingface/hub",
    )
    parser.add_argument(
        "--hf-endpoint",
        default=None,
        help="Hugging Face 镜像地址，如 https://hf-mirror.com",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input.expanduser().resolve()

    if not input_path.is_file():
        print(f"错误：找不到输入文件 {input_path}", file=sys.stderr)
        return 1

    output_path = args.output or OUTPUT_DIR / f"{input_path.stem}.txt"
    srt_path: Path | None = None
    if args.srt is not None:
        srt_path = Path(args.srt) if args.srt else OUTPUT_DIR / f"{input_path.stem}.srt"

    try:
        display_text, srt_text, info, _ = transcribe_file(
            input_path,
            model=args.model,
            language=args.language,
            device=args.device,
            compute_type=args.compute_type,
            with_timestamps=args.timestamps,
            model_path=args.model_path,
            download_root=args.download_root,
            hf_endpoint=args.hf_endpoint,
        )
    except ImportError:
        print(
            "错误：未安装 faster-whisper，请运行 pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(display_text + "\n", encoding="utf-8")

    print(f"语言：{info.language}（置信度 {info.language_probability:.2%}）")
    print(f"文本：{output_path}")

    if srt_path is not None:
        srt_path.parent.mkdir(parents=True, exist_ok=True)
        srt_path.write_text(srt_text, encoding="utf-8")
        print(f"字幕：{srt_path}")

    if not args.output and not args.timestamps:
        print()
        print(display_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
