#!/usr/bin/env python3
"""Scan local photos/videos and prepare visual evidence for agent annotation."""

import argparse
import concurrent.futures
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

VIDEOS = {".mov", ".mp4", ".m4v", ".mkv", ".avi", ".webm"}
IMAGES = {".heic", ".heif", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


def read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def run(command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{command[0]} failed: {result.stderr.strip()}")
    return result.stdout


def probe(path, source, ffprobe):
    relative = path.relative_to(source).as_posix()
    asset = {
        "id": "a" + hashlib.sha256(relative.encode()).hexdigest()[:16],
        "filename": path.name,
        "relative_path": relative,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": digest(path),
        "kind": "video" if path.suffix.lower() in VIDEOS else "image",
    }
    if asset["kind"] == "video":
        metadata = json.loads(
            run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
        )
        video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
        duration = float(video.get("duration") or metadata.get("format", {}).get("duration", 0))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(f"Cannot determine positive duration: {path}")
        rotation = next(
            (d["rotation"] for d in video.get("side_data_list", []) if "rotation" in d),
            float(video.get("tags", {}).get("rotate", 0)),
        )
        asset.update(
            duration=duration,
            width=video["width"],
            height=video["height"],
            rotation=rotation,
            color_transfer=video.get("color_transfer"),
            has_audio=any(s["codec_type"] == "audio" for s in metadata["streams"]),
        )
        rotated = abs(rotation) % 180 == 90
        asset["display_width"] = asset["height"] if rotated else asset["width"]
        asset["display_height"] = asset["width"] if rotated else asset["height"]
        # A seek after the final frame's PTS produces no image, although that
        # frame remains displayed until the stream ends. Read tail packet PTS
        # without decoding the whole video, including variable/low frame rates.
        tail = json.loads(
            run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-read_intervals",
                    f"{max(0, duration - 5)}%",
                    "-show_packets",
                    "-show_entries",
                    "packet=pts_time",
                    "-of",
                    "json",
                    str(path),
                ]
            )
        )
        origin = float(metadata.get("format", {}).get("start_time", 0))
        timestamps = [
            float(packet["pts_time"]) - origin
            for packet in tail.get("packets", [])
            if "pts_time" in packet
        ]
        if not timestamps:
            raise ValueError(f"Cannot determine final video frame timestamp: {path}")
        asset["last_frame_time"] = max(0, min(max(timestamps), duration))
    return asset


def windows_for(asset, window=12, long_window=20, long_threshold=300):
    if asset["kind"] == "image":
        ranges = [(None, None)]
    else:
        step = long_window if asset["duration"] > long_threshold else window
        ranges = [
            (i * step, min((i + 1) * step, asset["duration"]))
            for i in range(math.ceil(asset["duration"] / step))
        ]
    result = []
    for start, end in ranges:
        times = (
            []
            if start is None or end is None
            else [start + (end - start) * f for f in (0.1, 0.5, 0.9)]
        )
        seeks = [min(time, asset.get("last_frame_time", time)) for time in times]
        evidence = json.dumps([asset["sha256"], start, end, times, seeks, "preview-v2"])
        identifier = asset["id"] + "_" + hashlib.sha256(evidence.encode()).hexdigest()[:16]
        count = 1 if start is None else len(times)
        result.append(
            {
                "id": identifier,
                "asset_id": asset["id"],
                "filename": asset["filename"],
                "path": asset["path"],
                "kind": asset["kind"],
                "source_sha256": asset["sha256"],
                "start": start,
                "end": end,
                "sample_times": times,
                "preview_seek_times": seeks,
                "previews": [f"previews/{identifier}_{i}.jpg" for i in range(count)],
                "annotation_status": "pending",
                "audio_status": "not_transcribed",
                "coverage": "single_image"
                if start is None
                else "three_sampled_frames_not_continuous_review",
            }
        )
    return result


def image_preview(source, destination):
    try:
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((1000, 1000))
            image.save(destination, quality=92)
    except UnidentifiedImageError as error:
        sips = shutil.which("sips")
        if source.suffix.lower() not in {".heic", ".heif"} or not sips:
            raise RuntimeError(
                f"Cannot decode {source}; HEIC requires macOS sips or a Pillow HEIF decoder"
            ) from error
        run([sips, "-s", "format", "jpeg", "-Z", "1000", str(source), "--out", str(destination)])
        with Image.open(destination) as image:
            ImageOps.exif_transpose(image).convert("RGB").save(destination, quality=92)


def make_preview(segment, asset, output, ffmpeg):
    for index, relative in enumerate(segment["previews"]):
        destination = output / relative
        if destination.is_file():
            continue
        temporary = destination.with_suffix(".tmp.jpg")
        if segment["kind"] == "image":
            image_preview(Path(segment["path"]), temporary)
        else:
            filters = "scale=480:480:force_original_aspect_ratio=decrease"
            if asset.get("color_transfer") in {"arib-std-b67", "smpte2084"}:
                filters = (
                    "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                    "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,"
                    "format=yuv420p," + filters
                )
            run(
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-y",
                    "-ss",
                    str(segment["preview_seek_times"][index]),
                    "-i",
                    segment["path"],
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-vf",
                    filters,
                    str(temporary),
                ]
            )
        if not temporary.is_file():
            raise RuntimeError(f"No preview produced: {segment['id']}")
        temporary.replace(destination)


def make_sheets(segments, output):
    sheets = []
    for page in range(math.ceil(len(segments) / 6)):
        canvas = Image.new("RGB", (1260, 1500), "#171717")
        draw = ImageDraw.Draw(canvas)
        rows = segments[page * 6 : (page + 1) * 6]
        for index, segment in enumerate(rows):
            y = index * 250
            draw.text(
                (8, y + 5), f"{segment['id']}  {segment['start']}..{segment['end']}", fill="white"
            )
            for frame, relative in enumerate(segment["previews"]):
                with Image.open(output / relative) as image:
                    image.thumbnail((390, 210))
                    x = 10 + frame * 415
                    canvas.paste(image, (x + (390 - image.width) // 2, y + 30))
                label = (
                    f"target {segment['sample_times'][frame]:.3f}s / seek {segment['preview_seek_times'][frame]:.3f}s"
                    if segment["sample_times"]
                    else "image"
                )
                draw.text((x, y + 235), label, fill="#bbbbbb")
        path = f"sheets/pending-{page + 1:04d}.jpg"
        canvas.save(output / path, quality=90)
        sheets.append({"path": path, "segment_ids": [row["id"] for row in rows]})
    return sheets


def prepare(source, output, recursive=False, window=12, long_window=20, long_threshold=300, jobs=4):
    source, output = source.resolve(), output.resolve()
    if not source.is_dir():
        raise ValueError(f"Source directory does not exist: {source}")
    if source == output or output in source.parents:
        raise ValueError("Output must not equal or contain the source directory")
    if (
        any(not math.isfinite(n) or n <= 0 for n in (window, long_window, long_threshold))
        or jobs < 1
    ):
        raise ValueError("Window sizes, threshold and jobs must be positive and finite")
    previous = read_json(output / "scan.json", {})
    if previous and previous["source_root"] != str(source):
        raise ValueError(
            "This output belongs to another source root; choose a new output directory"
        )
    files = sorted(
        p
        for p in (source.rglob("*") if recursive else source.iterdir())
        if p.is_file()
        and not p.is_symlink()
        and output not in p.parents
        and not any(part.startswith(".") for part in p.relative_to(source).parts)
        and p.suffix.lower() in VIDEOS | IMAGES
    )
    if not files:
        raise ValueError("No supported media found; use --recursive for subdirectories")
    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if any(p.suffix.lower() in VIDEOS for p in files) and (not ffprobe or not ffmpeg):
        raise RuntimeError("Video indexing requires ffmpeg and ffprobe on PATH")
    for directory in ("previews", "sheets", "annotations"):
        (output / directory).mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        assets = list(pool.map(lambda path: probe(path, source, ffprobe), files))
    seen = {}
    for asset in assets:
        if asset["sha256"] in seen:
            asset["duplicate_of"] = seen[asset["sha256"]]
        else:
            seen[asset["sha256"]] = asset["id"]
    windows = [
        row for asset in assets for row in windows_for(asset, window, long_window, long_threshold)
    ]
    by_id = {asset["id"]: asset for asset in assets}
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        list(
            pool.map(lambda row: make_preview(row, by_id[row["asset_id"]], output, ffmpeg), windows)
        )
    # A changed source during extraction must not receive evidence for an old checksum.
    for asset in assets:
        if digest(Path(asset["path"])) != asset["sha256"]:
            raise RuntimeError(f"Source changed during preparation: {asset['path']}; run again")
    annotated = set()
    for path in sorted((output / "annotations").glob("*.json")):
        entries = read_json(path)
        if not isinstance(entries, list):
            raise ValueError(f"Annotation file must contain an array: {path}")
        annotated.update(
            row["id"]
            for row in entries
            if isinstance(row.get("description"), str)
            and row["description"].strip()
            and isinstance(row.get("tags"), list)
            and row["tags"]
        )
    pending = [row for row in windows if row["id"] not in annotated]
    sheets = make_sheets(pending, output)
    signature = hashlib.sha256(json.dumps([assets, windows], sort_keys=True).encode()).hexdigest()
    manifest = {
        "schema_version": 1,
        "source_root": str(source),
        "scan_id": signature,
        "recursive": recursive,
        "assets": len(assets),
        "segments": len(windows),
        "pending": len(pending),
        "sheets": sheets,
    }
    for filename, data in [
        ("assets.json", assets),
        ("windows.json", windows),
        ("pending.json", pending),
        ("scan.json", manifest),
    ]:
        write_json(output / filename, data)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--window", type=float, default=12)
    parser.add_argument("--long-window", type=float, default=20)
    parser.add_argument("--long-threshold", type=float, default=300)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(**vars(args)), ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, KeyError, StopIteration) as error:
        parser.exit(1, f"Preparation failed: {error}\n")


if __name__ == "__main__":
    main()
