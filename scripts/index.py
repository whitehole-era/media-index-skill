#!/usr/bin/env python3
"""Build and query a local agent-annotated media index; no API keys required."""

import argparse
import hashlib
import html
import json
import math
import sqlite3
from pathlib import Path

BASE = Path.cwd() / "media-index"
SYNONYMS = [
    ("特写", "近景", "细节"),
    ("夜晚", "夜景", "夜间"),
    ("白天", "日间", "日光"),
    ("牛肉", "肉干", "烤肉"),
    ("全景", "远景", "环境镜头"),
    ("试吃", "品尝"),
    ("摊主", "店员"),
]
LIST_FIELDS = {"tags", "subjects", "visible_actions", "quality_notes", "possible_uses"}
TEXT_FIELDS = {"description", "shot_scale", "lighting"}
ANNOTATION_FIELDS = {"id"} | LIST_FIELDS | TEXT_FIELDS


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path, content):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def read_annotations():
    result = {}
    for path in sorted((BASE / "annotations").glob("*.json")):
        entries = read_json(path)
        if not isinstance(entries, list):
            raise ValueError(f"Annotation file must contain an array: {path}")
        for row in entries:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                raise ValueError(f"Annotation must be an object with string id: {path}")
            if row["id"] in result:
                raise ValueError("Duplicate annotation ID: " + row["id"])
            forbidden = set(row) - ANNOTATION_FIELDS
            if forbidden:
                raise ValueError(
                    f"Annotation cannot override structural/unknown fields: {sorted(forbidden)}"
                )
            for key, value in row.items():
                if key in TEXT_FIELDS and not isinstance(value, str):
                    raise ValueError(f"{key} must be text: {row['id']}")
                if key in LIST_FIELDS and (
                    not isinstance(value, list)
                    or any(not isinstance(item, str) or not item.strip() for item in value)
                ):
                    raise ValueError(f"{key} must be an array of nonempty strings: {row['id']}")
            result[row["id"]] = row
    return result


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def check_sources(assets):
    errors = []
    for asset in assets:
        path = Path(asset["path"])
        if not path.is_file():
            errors.append("missing source " + str(path))
        elif digest(path) != asset["sha256"]:
            errors.append("source changed; run prepare again: " + str(path))
    return errors


def load():
    database = BASE / "index.sqlite"
    if not database.is_file():
        raise ValueError("Index not built yet; complete annotations and run build")
    manifest = BASE / "scan.json"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        if manifest.exists():
            stored = connection.execute("SELECT value FROM metadata WHERE key='scan_id'").fetchone()
            if not stored or stored[0] != read_json(manifest)["scan_id"]:
                raise ValueError(
                    "Index is stale after preparation; complete annotations and run build"
                )
        return [
            json.loads(row[0])
            for row in connection.execute("SELECT record FROM segments ORDER BY filename,start")
        ]


def validate(records, assets):
    errors, ids = [], set()
    asset_map = {asset["id"]: asset for asset in assets}
    if len(asset_map) != len(assets):
        errors.append("duplicate asset ID")
    for row in records:
        identifier = row["id"]
        if identifier in ids:
            errors.append("duplicate segment ID " + identifier)
        ids.add(identifier)
        asset = asset_map.get(row["asset_id"])
        if not asset:
            errors.append("missing asset " + identifier)
            continue
        if (
            not isinstance(row.get("description"), str)
            or not row["description"].strip()
            or not isinstance(row.get("tags"), list)
            or not row["tags"]
            or any(not isinstance(tag, str) or not tag.strip() for tag in row["tags"])
        ):
            errors.append("missing or invalid content annotation " + identifier)
        if row["kind"] != asset["kind"] or row["path"] != asset.get("path", row["path"]):
            errors.append("source mismatch " + identifier)
        if row.get("source_sha256", asset.get("sha256")) != asset.get("sha256"):
            errors.append("source hash mismatch " + identifier)
        if row["kind"] == "video":
            numbers = [row["start"], row["end"], *row["sample_times"]]
            valid_numbers = all(
                isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n)
                for n in numbers
            )
            if not valid_numbers or not 0 <= row["start"] < row["end"] <= asset["duration"] + 0.01:
                errors.append("invalid range " + identifier)
            elif any(not row["start"] <= time <= row["end"] for time in row["sample_times"]):
                errors.append("sample outside range " + identifier)
            if len(row["sample_times"]) != 3:
                errors.append("expected three sample times " + identifier)
        elif row["start"] is not None or row["end"] is not None or row["sample_times"]:
            errors.append("invalid image range " + identifier)
        if not Path(row["path"]).is_file():
            errors.append("missing source " + identifier)
        expected = 3 if row["kind"] == "video" else 1
        if len(row["previews"]) != expected:
            errors.append("incorrect preview count " + identifier)
        for preview in row["previews"]:
            path = Path(preview)
            if path.is_absolute() or BASE.resolve() not in (BASE / path).resolve().parents:
                errors.append("preview outside index " + preview)
            elif not (BASE / path).is_file():
                errors.append("missing preview " + preview)
    for asset in assets:
        rows = [row for row in records if row["asset_id"] == asset["id"]]
        if not rows:
            errors.append("asset not indexed " + asset["filename"])
        if asset["kind"] == "video":
            spans = sorted(
                (row["start"], row["end"])
                for row in rows
                if isinstance(row["start"], (int, float)) and isinstance(row["end"], (int, float))
            )
            if (
                not spans
                or abs(spans[0][0]) > 0.01
                or abs(spans[-1][1] - asset["duration"]) > 0.01
                or any(abs(first[1] - second[0]) > 0.01 for first, second in zip(spans, spans[1:]))
            ):
                errors.append("incomplete window coverage " + asset["filename"])
        elif len(rows) != 1:
            errors.append("expected one record per image " + asset["filename"])
    return errors


def pending():
    annotations = read_annotations()
    windows = read_json(BASE / "windows.json")
    return [
        row
        for row in windows
        if row["id"] not in annotations
        or not annotations[row["id"]].get("description", "").strip()
        or not annotations[row["id"]].get("tags")
    ]


def build():
    assets = read_json(BASE / "assets.json")
    windows = read_json(BASE / "windows.json")
    annotations = read_annotations()
    extras = set(annotations) - {row["id"] for row in windows}
    records = []
    for window in windows:
        annotation = annotations.get(window["id"], {})
        row = {**window, **annotation}
        row["annotation_status"] = "sampled_visual_annotation" if annotation else "pending"
        row["review_scope"] = (
            "Sampled frames only; verify complete actions and audio before editing"
            if row["kind"] == "video"
            else "Single-image visual annotation"
        )
        records.append(row)
    errors = validate(records, assets) + check_sources(assets)
    if errors:
        raise ValueError(json.dumps({"errors": errors}, ensure_ascii=False))
    manifest = BASE / "scan.json"
    scan_id = read_json(manifest)["scan_id"] if manifest.exists() else ""
    report = {
        "assets": len(assets),
        "videos": sum(a["kind"] == "video" for a in assets),
        "images": sum(a["kind"] == "image" for a in assets),
        "segments": len(records),
        "annotated": len(records),
        "exact_duplicate_files": sum("duplicate_of" in a for a in assets),
        "ignored_obsolete_annotation_ids": sorted(extras),
        "scan_id": scan_id,
        "errors": [],
        "retrieval": "Keyword AND/OR with small synonym dictionary; no vector search",
        "coverage": "Three sampled frames per video window; one preview per photo",
        "audio": "Not transcribed",
    }
    temporary = BASE / "index.sqlite.tmp"
    if temporary.exists():
        temporary.unlink()
    with sqlite3.connect(temporary) as connection:
        connection.execute(
            "CREATE TABLE segments(id TEXT PRIMARY KEY,asset_id TEXT,filename TEXT,kind TEXT,start REAL,end REAL,record TEXT)"
        )
        connection.executemany(
            "INSERT INTO segments VALUES(?,?,?,?,?,?,?)",
            [
                (
                    row["id"],
                    row["asset_id"],
                    row["filename"],
                    row["kind"],
                    row["start"],
                    row["end"],
                    json.dumps(row, ensure_ascii=False),
                )
                for row in records
            ],
        )
        connection.execute("CREATE INDEX by_asset ON segments(asset_id,start)")
        connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)")
        connection.execute("INSERT INTO metadata VALUES(?,?)", ("scan_id", scan_id))
    enriched = []
    for asset in assets:
        rows = [row for row in records if row["asset_id"] == asset["id"]]
        enriched.append(
            {
                **asset,
                "description": "；".join(dict.fromkeys(row["description"] for row in rows)),
                "tags": sorted({tag for row in rows for tag in row["tags"]}),
                "segment_ids": [row["id"] for row in rows],
            }
        )
    for filename, rows in [("segments.jsonl", records), ("assets.jsonl", enriched)]:
        write_text(
            BASE / filename, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        )
    gallery(records)
    write_text(BASE / "validation.json", json.dumps(report, ensure_ascii=False, indent=2))
    temporary.replace(BASE / "index.sqlite")
    return report


def terms(query):
    result = []
    for word in query.split():
        result.append(next((g for g in SYNONYMS if word in g), (word,)))
    return result


def search(records, query="", kind=None, max_duration=None, limit=10, match="all"):
    groups = terms(query.lower())
    result = []
    for r in records:
        if kind and r["kind"] != kind:
            continue
        duration = None if r["start"] is None else r["end"] - r["start"]
        if max_duration is not None and (duration is None or duration > max_duration):
            continue
        text = " ".join(
            str(r.get(k, ""))
            for k in [
                "filename",
                "description",
                "tags",
                "shot_scale",
                "lighting",
                "subjects",
                "quality_notes",
                "visible_actions",
                "possible_uses",
            ]
        ).lower()
        hits = [any(w in text for w in group) for group in groups]
        if groups and not (all(hits) if match == "all" else any(hits)):
            continue
        result.append((sum(hits), r))
    result.sort(key=lambda item: (-item[0], item[1]["filename"], item[1]["start"] or 0))
    return [r for _, r in result[:limit]]


def gallery(records):
    cards = []
    for r in records:
        esc = html.escape
        ran = "图片" if r["start"] is None else f"{r['start']:.2f}–{r['end']:.2f} 秒"
        imgs = "".join(
            f'<a href="{esc(p)}"><img loading="lazy" src="{esc(p)}"></a>' for p in r["previews"]
        )
        text = " ".join(
            str(r.get(k, ""))
            for k in [
                "filename",
                "description",
                "tags",
                "shot_scale",
                "lighting",
                "subjects",
                "visible_actions",
                "quality_notes",
                "possible_uses",
            ]
        )
        source = Path(r["path"]).as_uri()
        cards.append(
            f'<article data-kind="{r["kind"]}" data-search="{esc(text)}"><header><b>{esc(r["filename"])}</b> <span>{ran}</span></header><div class="frames">{imgs}</div><p>{esc(r["description"])}</p><p class="tags">{esc(" · ".join(r["tags"]))}</p><p class="notes">{esc("；".join(r.get("quality_notes", [])))}</p><details><summary>证据与剪辑用途</summary><p>抽帧时间：{esc(str(r["sample_times"]))}</p><p>{esc(r["review_scope"])}</p><p>候选用途：{esc("；".join(r.get("possible_uses", [])))}</p><code>{esc(r["id"])}</code></details><a href="{esc(source)}">打开原素材</a></article>'
        )
    page = (
        r"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>素材索引</title><style>body{font:16px system-ui;background:#171512;color:#f3eadc;margin:24px;max-width:1600px}h1{font-size:28px}nav{position:sticky;top:0;background:#171512ee;padding:14px 0;z-index:2}input,select{font:inherit;padding:10px;border-radius:6px;border:1px solid #777;background:#28241f;color:inherit}input{width:65%}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(350px,1fr));gap:16px}article{background:#25211c;padding:16px;border-radius:10px}header{display:flex;justify-content:space-between;gap:8px;font-size:14px;margin-bottom:12px}.frames{display:flex;gap:4px;height:220px}.frames a{flex:1;min-width:0}.frames img{width:100%;height:100%;object-fit:contain;background:#111}.tags,a{color:#eabf7c}.notes,details{color:#c0b7aa;font-size:14px}p{line-height:1.6}details{margin:10px 0}code{word-break:break-all}[hidden]{display:none!important}</style><h1>本地素材索引</h1><p>图片逐张描述，视频按候选时间窗口标注。视频说明基于每窗3张抽帧，检索命中后仍需复核完整动作与声音。</p><nav><input id="q" placeholder="关键词用空格分隔，如：夜晚 摊位 / 牛肉 特写 / 试吃"><select id="kind"><option value="">全部素材</option><option value="video">视频</option><option value="image">图片</option></select><p id="count"></p></nav><main>"""
        + "".join(cards)
        + r"""</main><script>const q=document.querySelector('#q'),kind=document.querySelector('#kind'),cards=[...document.querySelectorAll('article')];function filter(){let terms=q.value.toLowerCase().trim().split(/\s+/).filter(Boolean),n=0;for(const card of cards){let show=(!kind.value||card.dataset.kind===kind.value)&&terms.every(t=>{const groups=[['特写','近景','细节'],['夜晚','夜景','夜间'],['白天','日间','日光'],['牛肉','肉干','烤肉'],['全景','远景','环境镜头'],['试吃','品尝'],['摊主','店员']];return (groups.find(g=>g.includes(t))||[t]).some(w=>card.dataset.search.toLowerCase().includes(w));});card.hidden=!show;if(show)n++;}document.querySelector('#count').textContent=`${n} / ${cards.length} 条索引`;}q.addEventListener('input',filter);kind.addEventListener('change',filter);filter();</script></html>"""
    )
    write_text(BASE / "index.html", page)


def main():
    global BASE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True, help="Prepared index directory")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "stats", "validate", "pending"):
        commands.add_parser(name)
    show = commands.add_parser("show")
    show.add_argument("id", help="Segment ID, asset ID or exact filename")
    query = commands.add_parser("search")
    query.add_argument("query", nargs="?", default="")
    query.add_argument("--kind", choices=["image", "video"])
    query.add_argument("--max-duration", type=float)
    query.add_argument("--limit", type=int, default=10)
    query.add_argument("--match", choices=["all", "any"], default="all")
    args = parser.parse_args()
    BASE = args.index.resolve()
    try:
        if args.command == "build":
            result = build()
        elif args.command == "pending":
            result = pending()
        else:
            records = load()
            if args.command == "stats":
                result = read_json(BASE / "validation.json")
            elif args.command == "validate":
                assets = read_json(BASE / "assets.json")
                errors = validate(records, assets) + check_sources(assets)
                result = {"records": len(records), "errors": errors}
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return int(bool(errors))
            elif args.command == "show":
                result = [
                    row
                    for row in records
                    if args.id in (row["id"], row["asset_id"], row["filename"])
                ]
            else:
                if args.limit < 1:
                    parser.error("--limit must be positive")
                if args.max_duration is not None and (
                    not math.isfinite(args.max_duration) or args.max_duration <= 0
                ):
                    parser.error("--max-duration must be positive and finite")
                result = search(
                    records, args.query, args.kind, args.max_duration, args.limit, args.match
                )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        parser.exit(1, f"Index operation failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
