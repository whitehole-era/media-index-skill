# Visual annotation format

Write UTF-8 JSON files to the index's `annotations/` directory. Each file contains an array;
the build reads all `*.json` batches. A prepared window ID must appear exactly once across those files.

```json
[
  {
    "id": "COPY_THE_EXACT_PREPARED_WINDOW_ID",
    "description": "夜间画面中，一个亮灯摊位前站着两个人；台面上可见摆放的食品。",
    "tags": ["夜晚", "摊位", "人物", "食品"],
    "subjects": ["两个人", "亮灯摊位", "食品台面"],
    "visible_actions": ["人物站在摊位前"],
    "shot_scale": "中景",
    "lighting": "夜间，摊位灯光较亮",
    "quality_notes": ["远处文字无法辨认"],
    "possible_uses": ["可考虑作为夜市场景的环境镜头；剪辑前复核原片"]
  }
]
```

The example describes an illustrative observation, not a template to copy onto unseen material.

| Field | Type | Meaning |
|---|---|---|
| `id` | Non-empty string, required | Exact ID copied from `pending.json` or `windows.json` |
| `description` | Non-empty string, required | Visible content in natural language |
| `tags` | Non-empty array of non-empty strings, required | Searchable content words |
| `subjects` | Array of strings, optional | Visible people, objects, and setting |
| `visible_actions` | Array of strings, optional | Actions supported by the inspected images |
| `shot_scale` | String, optional | For example close-up, medium shot, or wide shot |
| `lighting` | String, optional | Visible light and exposure conditions |
| `quality_notes` | Array of strings, optional | Blur, obstruction, framing, readability, or other visible limits |
| `possible_uses` | Array of strings, optional | Editing suggestions, explicitly separate from observations |

Only these fields belong in annotation files. Fields such as `asset_id`, `source`, `path`, `filename`,
`kind`, `start`, `end`, `sample_times`, `previews`, and duration come from preparation and must not
be supplied or overwritten by annotations. IDs are references, not editable labels.

Open the relevant images with the current agent's visual tools before writing each annotation.
For a video window, inspect all three samples. Record observations across those samples without
implying that an action continued for the whole window or that nothing else happened between them.
Use a clear description when samples differ, for example “首张可见空台面，后两张可见一只手靠近杯子”.

Do not infer a specific person, location, product, taste, dialogue, or emotional state from a filename
or an ambiguous gesture. A hand holding food does not alone establish eating or tasting.
Do not record absence of motion from three stills. Audio has not been transcribed or assessed.
Use `possible_uses` for ideas such as an opening shot or transition; those are suggestions, not facts.

Sampled-frame labels support candidate retrieval. A 12- or 20-second index window is not an exact
shot, a precise action interval, or proof of continuous full-video review. Inspect the original footage
and sound before making edit points or claims that depend on them.

After source changes, use the new prepared IDs, inspect the current images, and annotate again.
Missing current annotations and duplicate IDs fail the build. Obsolete annotation IDs are ignored and listed in `ignored_obsolete_annotation_ids`; they are never reassigned to new content. Remove obsolete entries when convenient.

`sample_times` records the requested 10%, 50%, and 90% positions. `preview_seek_times` records FFmpeg seek positions; positions after the final video frame timestamp are clamped to that frame, so short or low-frame-rate windows may show the same frame more than once. Contact sheets display both times.
