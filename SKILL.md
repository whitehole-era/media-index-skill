---
name: media-index
description: Index local photos and videos into a searchable visual catalog using sampled frames and agent-written annotations. Use when finding footage, organizing a media library, or preparing editing candidates; audio transcription is not included.
---

# Media Index

Create a local catalog that connects visible content to source files and candidate time windows.
Use this skill's Python scripts for metadata, previews, validation, and retrieval.
Use the current agent's image-viewing tools to inspect previews and write the content annotations.
Read [references/annotations.md](references/annotations.md) before annotating.

## Environment

Resolve this skill's installation directory and run commands from it, or use absolute script paths.
Requirements: Python 3.10+, FFmpeg/ffprobe on PATH, and Pillow in the Python environment.
Use `.venv/bin/python` when a skill-local virtual environment exists, or activate it first.
See [README.md](README.md) for installation in Codex and Claude Code.
No separate model API key or base URL configuration is needed.

## Prepare the library

Choose a source directory and a separate output directory. Keep generated files outside the source tree.
The source media stays in place; preprocessing writes metadata and derived images into the output.

```bash
python3 scripts/prepare.py --source /path/media --output /path/index --recursive
```

Omit `--recursive` to scan only the source directory itself.
The preparation step creates:

- `assets.json`: source file metadata and asset identities.
- `windows.json`: image records and video candidate windows with sample times.
- `pending.json`: records awaiting visual annotation.
- `previews/` and `sheets/`: derived images for inspection.
- `annotations/`: initially empty; the agent writes JSON batches here.

Photos receive a single visual record. Videos are divided into consecutive 12-second windows;
videos longer than 300 seconds use 20-second windows. The final window can be shorter.
Each video window samples frames at 10%, 50%, and 90% of its own duration.
These windows cover the timeline but do not establish exact shot or action boundaries.
Near EOF, FFmpeg seeks clamp to the last frame timestamp; `preview_seek_times` records this,
and the preview may repeat. Keep requested `sample_times` and extraction seeks distinct.

## Inspect and annotate

List work awaiting annotation:

```bash
python3 scripts/index.py --index /path/index pending
```

Actually open and inspect each photo preview and each video window's three sample frames.
Contact sheets can aid navigation; enlarge individual previews when details are unclear.
Associate the image being viewed with its exact window ID before writing an annotation.
Do not derive content labels from filenames, timestamps, metadata, or neighboring windows alone.

Write one or more UTF-8 files under `/path/index/annotations/`, each containing a JSON array.
Copy each `id` from the prepared records. Include a factual `description` and non-empty `tags`.
Use optional `subjects`, `visible_actions`, `shot_scale`, `lighting`, and `quality_notes` for observed details.
Put editing ideas in `possible_uses`, keeping them separate from visible facts.
See the annotation reference for types, examples, and the permitted field list.

Only add annotation fields. Never replace source paths, asset IDs, time ranges, sample times,
preview paths, or other structural fields supplied by preparation.
If evidence is unclear, describe the visible ambiguity instead of inventing an action or identity.
If images cannot be viewed, leave the relevant records pending and state the visual-tool blocker.

## Build and validate

```bash
python3 scripts/index.py --index /path/index build
python3 scripts/index.py --index /path/index validate
python3 scripts/index.py --index /path/index stats
```

Build requires an annotation for every prepared record and fails when annotations are missing.
It creates JSONL records, a SQLite index, and an HTML gallery in the output directory.
Read validation output before describing an index as complete.
Complete here means the prepared records have valid annotations, not continuous audiovisual review.

## Retrieve editing candidates

```bash
python3 scripts/index.py --index /path/index search '夜晚 摊位' --kind video --limit 5 --match all --max-duration 12
python3 scripts/index.py --index /path/index show WINDOW_ID
```

Separate keywords with spaces. `--match all` requires every keyword group; `--match any` accepts any group.
`--kind image` selects photos. `--max-duration` filters video candidate-window duration.
Retrieval uses keywords and a small synonym dictionary, not embeddings or vector search.
Report source file, candidate start/end times, visible evidence, and any quality concerns.
Before selecting an exact edit, open the original candidate range and verify action boundaries and sound.

## Refresh and report honestly

Rerun preparation after source changes. Changed source files receive new segment IDs and need new annotations;
do not assign an old annotation to a new ID without inspecting its current previews.
Use `pending` to identify remaining work, then rebuild and validate the index.

Report asset/window counts, the output location, validation result, and the sampling limits.
Audio is not transcribed. Do not claim dialogue, music, sound quality, or events between sampled frames.
Local preprocessing does not upload media; viewing images through a hosted agent follows that agent's
existing model routing and data handling. Do not change API credentials or provider configuration.
