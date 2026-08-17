"""Message catalog for every string a `kdenlive-as-code` user sees: validation errors,
CLI output, import warnings. English is the source of truth and the
fallback — both for locales that don't exist and for keys missing from a
locale that does. Pick a locale by setting `KDENLIVE_AS_CODE_LANG` (e.g. `es`); it's
opt-in only (we don't read the system `LANG`/`LC_ALL`), so behavior stays
explicit and test runs stay deterministic regardless of the machine's
locale.
"""
from __future__ import annotations

import os

_EN: dict[str, str] = {
    "validation.summary": "{n} validation problem(s):",
    "probe.ffprobe_unavailable": (
        "ffprobe is not available on PATH; can't automatically compute the "
        "duration of '{path}'. Specify an explicit `out` or `duration` in the YAML."
    ),
    "probe.ffprobe_failed": "ffprobe failed analyzing '{path}': {stderr}",
    "probe.ffprobe_timeout": "ffprobe did not respond in time analyzing '{path}'",
    "probe.duration_unreadable": "Could not read the duration of '{path}' from ffprobe's output",
    "validation.yaml_missing": "YAML file not found: {path}",
    "validation.yaml_invalid": "Invalid YAML in {path}: {exc}",
    "validation.project_section_missing": "Missing 'project:' section (or it isn't a map) in {path}",
    "validation.bin_not_list": "'bin:' must be a list of clips",
    "validation.tracks_not_list": "'timeline.tracks:' must be a list of tracks",
    "validation.unknown_top_level_keys": "Unrecognized top-level keys (check indentation): {keys}",
    "validation.scenes_not_list": "'scenes:' must be a list of scenes",
    "validation.guides_not_list": "'guides:' must be a list of guides",
    "validation.guide_missing_at": "{ctx} is invalid (missing 'at'): {item!r}",
    "validation.guide_at_negative": "{ctx}: 'at' is negative ({at})",
    "validation.transitions_not_list": "'transitions:' must be a list of transitions",
    "validation.transition_missing_type": "{ctx} is invalid (missing 'type'): {item!r}",
    "validation.transition_type_invalid": "{ctx}.type={type!r} must be 'wipe' or 'affine'",
    "validation.transition_unknown_track": "{ctx}: track_a={track_a!r}/track_b={track_b!r} must both reference a track defined in 'timeline.tracks:'",
    "validation.transition_missing_span": "{ctx} is invalid (missing 'start' or 'end')",
    "validation.transition_end_not_after_start": "{ctx}: 'end' ({end}) must be greater than 'start' ({start})",
    "validation.transition_luma_invalid": "{ctx}.luma={luma!r} must be one of {valid}",
    "validation.transition_style_invalid": "{ctx}.style={style!r} must be one of {valid}",
    "validation.project_field_required": "project.{key} is required",
    "validation.bin_entry_invalid": "Invalid 'bin:' entry (missing 'id' or 'path'): {item!r}",
    "validation.bin_type_invalid": "bin[{id}].type={type!r} is invalid, must be one of {valid}",
    "validation.title_missing_text": "bin[{id}]: type: title requires 'text'",
    "validation.title_font_size_invalid": "bin[{id}]: font_size must be greater than 0, got {font_size}",
    "validation.colorspace_invalid": "project.colorspace must be greater than 0, got {colorspace}",
    "validation.pip_size_invalid": "{ctx}: position/size must have a positive width and height, got w={w:g} h={h:g}",
    "validation.pip_out_of_bounds": (
        "{ctx}: position/size ([{x:g}, {y:g}] {w:g}x{h:g}) falls outside "
        "the project canvas ({width}x{height})"
    ),
    "validation.position_keyframes_conflicts": "{ctx}: 'position_keyframes' cannot be combined with 'position'/'size' - use one or the other",
    "validation.position_keyframes_invalid": "{ctx}: 'position_keyframes' must be a non-empty list of {{at, position, size}} keyframes",
    "validation.position_keyframes_keyframe_invalid": "{ctx}: 'position_keyframes[{idx}]' must be a mapping with 'at', 'position' [x, y], and 'size' [w, h]",
    "validation.position_keyframes_must_start_at_zero": "{ctx}: 'position_keyframes''s first keyframe must have 'at: 0'",
    "validation.position_keyframes_not_sorted": "{ctx}: 'position_keyframes' keyframes must have strictly increasing 'at' values",
    "validation.position_keyframes_out_of_bounds": (
        "{ctx}: 'position_keyframes[{idx}]' ([{x:g}, {y:g}] {w:g}x{h:g}) falls outside "
        "the project canvas ({width}x{height})"
    ),
    "validation.crop_conflicts": "{ctx}: 'crop' cannot be combined with 'position'/'size'/'position_keyframes' - use one or the other",
    "validation.crop_invalid": "{ctx}: 'crop' must be a mapping with 'top'/'right'/'bottom'/'left'",
    "validation.crop_negative": "{ctx}: 'crop' margins ('top'/'right'/'bottom'/'left') must not be negative",
    "validation.freeze_before_after_without_at": "{ctx}: 'freeze_before'/'freeze_after' require 'freeze_at' to be set",
    "validation.freeze_at_negative": "{ctx}: 'freeze_at' must not be negative, got {freeze_at}",
    "validation.freeze_at_out_of_bounds": "{ctx}: 'freeze_at'={freeze_at} is outside this clip's own duration (0..{duration}s)",
    "validation.track_missing_type": "timeline.tracks[{idx}] is invalid (missing 'type'): {item!r}",
    "validation.track_type_invalid": "timeline.tracks[{idx}].type={type!r} must be one of {valid}",
    "validation.pan_on_video_track": "timeline.tracks[{idx}]: 'pan' only applies to audio tracks",
    "validation.pan_out_of_range": "timeline.tracks[{idx}]: 'pan' must be between -1.0 and 1.0, got {pan}",
    "validation.hidden_invalid": "timeline.tracks[{idx}]: 'hidden' must be true or false, got {hidden!r}",
    "validation.locked_invalid": "timeline.tracks[{idx}]: 'locked' must be true or false, got {locked!r}",
    "validation.audio_muted_invalid": "timeline.tracks[{idx}]: 'audio_muted' must be true or false, got {audio_muted!r}",
    "validation.tracks_clips_not_list": "timeline.tracks[{idx}].clips must be a list",
    "validation.clip_missing_fields": "{ctx} is invalid (missing 'clip' or 'start'): {item!r}",
    "validation.pair_invalid": "{ctx}.{key} must be a list of 2 numbers [x, y]",
    "validation.bin_duplicate_id": "bin: duplicate clip id '{id}'",
    "validation.bin_missing_file": "bin[{id}]: file '{path}' does not exist",
    "validation.clip_unknown_reference": "{ctx}: reference to unknown clip '{clip}' (not in 'bin:')",
    "validation.in_negative": "{ctx}: 'in' is negative ({in_s})",
    "validation.out_not_greater_than_in": "{ctx}: 'out' ({out_s}) must be greater than 'in' ({in_s})",
    "validation.out_exceeds_duration": (
        "{ctx}: 'out' ({out_s:.3f}s) exceeds the actual duration of clip "
        "'{clip}' ({source_s:.3f}s)"
    ),
    "validation.start_negative": "{ctx}: 'start' is negative ({start})",
    "validation.fade_negative": "{ctx}: fade_in/fade_out cannot be negative",
    "validation.fade_exceeds_duration": (
        "{ctx}: fade_in ({fade_in}s) + fade_out ({fade_out}s) exceed the clip's "
        "duration on the timeline ({clip_len:.3f}s)"
    ),
    "validation.mute_invalid": "{ctx}: 'mute' must be true or false, got {mute!r}",
    "validation.source_in_out_incomplete": "{ctx}: 'source_in' and 'source_out' must both be set, or neither",
    "validation.source_in_out_conflicts": "{ctx}: 'source_in'/'source_out' cannot be combined with 'in'/'out'/'duration' - use one or the other",
    "validation.source_in_out_invalid": "{ctx}: 'source_out' ({source_out}) must be greater than 'source_in' ({source_in}), and 'source_in' must not be negative",
    "validation.reveal_invalid": "{ctx}: 'reveal' must be 'iris', 'flip', 'glitch', 'delaygrab', or 'cairogradient', got {reveal!r}",
    "validation.chroma_key_distance_invalid": "{ctx}: 'chroma_key_distance' must be between 0.0 and 1.0, got {chroma_key_distance}",
    "validation.overlay_font_size_invalid": "{ctx}: 'overlay_font_size' must be greater than 0, got {overlay_font_size}",
    "validation.quad_invalid": "{ctx}.{key} must be a list of 4 numbers [x, y, w, h]",
    "validation.ken_burns_incomplete": "{ctx}: 'ken_burns_from' and 'ken_burns_to' must both be set, or neither",
    "validation.ken_burns_size_invalid": "{ctx}: 'ken_burns_from'/'ken_burns_to' widths and heights must be greater than 0",
    "validation.focus_rect_size_invalid": "{ctx}: 'focus_rect' width and height must be greater than 0",
    "validation.numeric_range_invalid": "{ctx}: '{field}' must be between {min} and {max}, got {value}",
    "validation.speed_zero": "{ctx}: 'speed' cannot be 0 (use a positive value for forward, negative for reverse)",
    "validation.speed_on_image": "{ctx}: 'speed' has no effect on an image or title clip ('{clip}')",
    "validation.speed_ramp_conflicts_with_speed": "{ctx}: 'speed_ramp' cannot be combined with 'speed' - use one or the other",
    "validation.speed_ramp_invalid": "{ctx}: 'speed_ramp' must be a non-empty list of {{at, speed}} keyframes",
    "validation.speed_ramp_image_mode_invalid": "{ctx}: 'speed_ramp_image_mode' must be 'nearest' or 'blend', got {mode!r}",
    "validation.speed_ramp_keyframe_invalid": "{ctx}: 'speed_ramp[{idx}]' must be a mapping with 'at' and 'speed'",
    "validation.speed_ramp_speed_invalid": "{ctx}: 'speed_ramp[{idx}].speed' must be greater than 0, got {speed}",
    "validation.speed_ramp_must_start_at_zero": "{ctx}: 'speed_ramp''s first keyframe must have 'at: 0'",
    "validation.speed_ramp_not_sorted": "{ctx}: 'speed_ramp' keyframes must have strictly increasing 'at' values",
    "validation.speed_ramp_in_not_supported": "{ctx}: 'in' isn't supported together with 'speed_ramp' - a ramp always starts at the source's own beginning",
    "validation.speed_ramp_on_image": "{ctx}: 'speed_ramp' has no effect on an image or title clip ('{clip}')",
    "validation.speed_ramp_requires_duration": "{ctx}: 'speed_ramp' needs an explicit 'out' or 'duration' - it isn't inferred automatically",
    "validation.speed_ramp_breakpoint_out_of_range": "{ctx}: 'speed_ramp' keyframe at {at}s falls at or after the clip's own duration ({out_s}s)",
    "validation.speed_ramp_exceeds_source": "{ctx}: 'speed_ramp' would consume {consumed_s}s of source material, but only {available_s}s is available",
    "validation.clip_overlap": (
        "timeline.tracks[{idx}]({name}): overlap between clips '{prev}' "
        "(ends at frame {prev_end}) and '{next}' (starts at frame {next_start})"
    ),
    "validation.scene_clip_undefined": "{ctx}.clips[{idx}]: clip '{clip_id}' is not defined in 'bin:'",
    "validation.scene_clip_invalid": "{ctx}.clips[{idx}] is invalid: {raw!r}",
    "validation.scene_missing_clips": "scenes[{idx}] is invalid (missing 'clips'): {scene!r}",
    "validation.scene_clips_not_list": "{ctx}.clips must be a non-empty list",
    "validation.scene_transition_invalid": "{ctx}.transition={transition!r} must be one of 'none', 'dissolve', 'wipe', 'affine', 'iris', 'flip', 'glitch', 'delaygrab', 'cairogradient'",
    "validation.scene_pacing_invalid": "{ctx}.pacing={pacing!r} must be one of {valid}",
    "validation.scene_luma_invalid": "{ctx}.luma={luma!r} must be one of {valid}",
    "validation.scene_transition_needs_video": "{ctx}: transition: {transition} has no audio equivalent (it geometrically transforms/masks pixels, not samples) - use 'dissolve' for an audio-only scene",
    "validation.scene_style_invalid": "{ctx}.style={style!r} must be one of {valid}",
    "scenes.track_a": "Scenes A",
    "scenes.track_b": "Scenes B",
    "scenes.track_a_audio": "Scenes A (audio)",
    "scenes.track_b_audio": "Scenes B (audio)",
    "scenes.track_burst": "Scenes Burst",
    "reverse.no_profile": "File has no <profile>: doesn't look like a valid MLT/kdenlive project",
    "reverse.no_sequence_found": "No sequence found (tractor with kdenlive:uuid) in the file",
    "reverse.broken_reference": "{ctx}: broken reference to '{ref}'",
    "reverse.secondary_playlist_with_content": "{ctx}: secondary playlist with content (kdenlive's 'mix' feature)",
    "reverse.mix_track_warning": (
        "{ctx}: this track uses kdenlive's 'mix' feature (clips in two "
        "sub-playlists); only the first playlist with content is imported, "
        "the rest is kept in x_kdenlive_raw"
    ),
    "reverse.unrecognized_track_structure": "{ctx}: unrecognized track structure (unexpected <{tag}>)",
    "reverse.qtblend_rect_unreadable": "{ctx}: qtblend filter with unreadable 'rect'",
    "reverse.qtblend_animated_position": "{ctx}: qtblend filter with animated position (multiple keyframes)",
    "reverse.unrecognized_filter": "{ctx}: unrecognized filter ({service}/{kid})",
    "reverse.unexpected_playlist_element": "{ctx}: unexpected element in playlist (<{tag}>)",
    "reverse.entry_missing_producer": "{ctx}: entry references a nonexistent producer '{producer_id}', skipping",
    "reverse.clip_no_resource": "{ctx}: clip with a producer that has no recognizable 'resource' ({tag} {producer_id})",
    "reverse.timewarp_unsupported": "{ctx}: clip with a speed change (timewarp), not supported",
    "reverse.speed_ramp_unsupported": "{ctx}: clip with a speed ramp (timeremap), not reconstructible into speed_ramp: keyframes",
    "reverse.title_unsupported": "{ctx}: title clip richer than this generator can represent (multiple items, animation, image/rect items, or an unreadable file), not supported",
    "reverse.cross_track_transition_unsupported": "cross-track transition ({service}) between two content tracks (e.g. a wipe) - not reconstructable in the low-level YAML model, kept as-is",
    "reverse.track_not_imported": "{ctx}: track not imported",
    "reverse.extra_sequence_not_imported": "additional sequence not imported (only the active one is converted)",
    "reverse.guides_unreadable": "sequenceproperties.guides is not the expected JSON array of {{pos, comment, type}} entries, kept as-is",
    "cli.build_done": "Generated {output} ({frames} frames, {fps:g} fps)",
    "cli.validate_done": "OK: {n_tracks} track(s), {n_clips} clip(s) placed, {frames} total frames",
    "cli.import_error": "Error importing '{path}': {exc}",
    "cli.import_done": "Generated {output}",
    "cli.warning_prefix": "  warning: {w}",
    "cli.raw_items_notice": (
        "  {n} unrecognized item(s) kept in 'x_kdenlive_raw:' inside {output} "
        "(review them; nothing was lost, but they won't be regenerated by 'kdenlive-as-code build')."
    ),
    "cli.reconstruct_missing_extra": (
        "'reconstruct' needs the optional numpy/Pillow dependencies. "
        "Install them with: pip install kdenlive-as-code[reconstruct]"
    ),
    "reconstruct.ffmpeg_or_ffprobe_missing": (
        "ffmpeg/ffprobe not found on PATH; 'reconstruct' needs them to decode "
        "frames and audio. Install ffmpeg (which bundles ffprobe) and make "
        "sure it's on PATH."
    ),
    "reconstruct.ffmpeg_failed": "ffmpeg failed: {stderr}",
    "reconstruct.ffmpeg_timeout": "ffmpeg didn't finish within {timeout}s (a genuinely stuck process, or footage too long/slow to decode in that window)",
    "reconstruct.ffprobe_failed": "ffprobe failed on '{path}': {stderr}",
    "reconstruct.ffprobe_timeout": "ffprobe didn't finish within 30s on '{path}'",
    "reconstruct.no_video_stream": "'{path}' has no video stream",
    "reconstruct.unexpected_wav_sample_width": "unexpected WAV sample width from ffmpeg: {width}",
    "reconstruct.no_confident_match": "{where}: no confident asset match ({detail})",
    "reconstruct.best_guess_detail": "closest was '{best_guess}' at {confidence_pct}% confidence",
    "reconstruct.no_candidate": "no candidate at all (empty asset bank, or no signal in this segment)",
    "reconstruct.progress.probing": "Probing '{name}'...",
    "reconstruct.progress.building_visual_bank": "Scanning assets for visual matching...",
    "reconstruct.progress.visual_asset_image": "  - {name} (image)",
    "reconstruct.progress.visual_asset_video": "  - {name} (video, extracting frame signatures)",
    "reconstruct.progress.building_audio_bank": "Scanning assets for audio matching...",
    "reconstruct.progress.audio_asset": "  - {name}",
    "reconstruct.progress.extracting_render_signatures": "Extracting frame signatures from '{name}' (this can take a while for long footage)...",
    "reconstruct.progress.segments_found": "Found {n} scene-cut segment(s), matching each against the asset bank...",
    "reconstruct.progress.matching_segment": "  [{i}/{n}] segment {start}s-{end}s...",
    "reconstruct.progress.matching_audio": "Extracting and matching the render's audio track...",
    "cli.reconstruct_error": "Error reconstructing from '{path}': {exc}",
    "cli.reconstruct_done": "Generated {output}",
    "cli.reconstruct_unmatched_notice": (
        "  {n} segment(s) with no confident match kept in 'x_reconstruct_unmatched:' "
        "inside {output} (review them by hand; nothing was guessed)."
    ),
    "cli.help.description": "kdenlive project generator from YAML",
    "cli.help.build": "Generates a .kdenlive from a YAML",
    "cli.help.project_path": "Path to the project's .yaml file",
    "cli.help.build_output": "Output path (default: same name with .kdenlive extension)",
    "cli.help.validate": "Validates a YAML without generating the .kdenlive",
    "cli.help.import": "Converts an existing .kdenlive to YAML",
    "cli.help.kdenlive_path": "Path to the source .kdenlive file",
    "cli.help.import_output": "Output path (default: same name with .yaml extension)",
    "cli.help.reconstruct": "Reconstructs a YAML from an already-rendered video + an assets folder (best-effort, content-based matching)",
    "cli.help.reconstruct_video": "Path to the already-rendered video file",
    "cli.help.reconstruct_assets_dir": "Path to the folder of candidate source assets",
    "cli.help.reconstruct_output": "Output path (default: same name with .yaml extension)",
    "cli.render_missing_melt": (
        "'melt' (the MLT engine kdenlive itself renders with) was not found "
        "on PATH. Install kdenlive or MLT, or generate the .kdenlive with "
        "'kdenlive-as-code build' and render it yourself."
    ),
    "cli.render_failed": "melt failed rendering '{path}': {stderr}",
    "cli.render_done": "Rendered {output} ({frames} frames, {fps:g} fps)",
    "cli.help.render": "Builds a .kdenlive from a YAML and renders it to a video with melt",
    "cli.help.render_output": "Output video path (default: same name as the YAML, .mp4 extension)",
    "cli.help.keep_kdenlive": "Keep the intermediate .kdenlive instead of discarding it (default: same name as the YAML, .kdenlive extension)",
    "cli.render_kept_kdenlive": "Kept {path}",
    "cli.help.lang": "Output language (overrides KDENLIVE_AS_CODE_LANG for this run)",
    "cli.write_failed": "Error writing '{path}': {exc}",
}

_ES: dict[str, str] = {
    "validation.summary": "{n} problema(s) de validación:",
    "probe.ffprobe_unavailable": (
        "ffprobe no está disponible en PATH; no se puede calcular la duración de "
        "'{path}' automáticamente. Especifica `out` o `duration` explícitos en el YAML."
    ),
    "probe.ffprobe_failed": "ffprobe falló al analizar '{path}': {stderr}",
    "probe.ffprobe_timeout": "ffprobe no respondió a tiempo analizando '{path}'",
    "probe.duration_unreadable": "No se pudo leer la duración de '{path}' en la salida de ffprobe",
    "validation.yaml_missing": "No existe el archivo YAML: {path}",
    "validation.yaml_invalid": "YAML inválido en {path}: {exc}",
    "validation.project_section_missing": "Falta la sección 'project:' (o no es un mapa) en {path}",
    "validation.bin_not_list": "'bin:' debe ser una lista de clips",
    "validation.tracks_not_list": "'timeline.tracks:' debe ser una lista de pistas",
    "validation.unknown_top_level_keys": "Claves de nivel superior no reconocidas (revisa la indentación): {keys}",
    "validation.scenes_not_list": "'scenes:' debe ser una lista de escenas",
    "validation.guides_not_list": "'guides:' debe ser una lista de guías",
    "validation.guide_missing_at": "{ctx} inválida (falta 'at'): {item!r}",
    "validation.guide_at_negative": "{ctx}: 'at' negativo ({at})",
    "validation.transitions_not_list": "'transitions:' debe ser una lista de transiciones",
    "validation.transition_missing_type": "{ctx} inválida (falta 'type'): {item!r}",
    "validation.transition_type_invalid": "{ctx}.type={type!r} debe ser 'wipe' o 'affine'",
    "validation.transition_unknown_track": "{ctx}: track_a={track_a!r}/track_b={track_b!r} deben referenciar una pista definida en 'timeline.tracks:'",
    "validation.transition_missing_span": "{ctx} inválida (falta 'start' o 'end')",
    "validation.transition_end_not_after_start": "{ctx}: 'end' ({end}) debe ser mayor que 'start' ({start})",
    "validation.transition_luma_invalid": "{ctx}.luma={luma!r} debe ser uno de {valid}",
    "validation.transition_style_invalid": "{ctx}.style={style!r} debe ser uno de {valid}",
    "validation.project_field_required": "project.{key} es obligatorio",
    "validation.bin_entry_invalid": "Entrada de 'bin:' inválida (falta 'id' o 'path'): {item!r}",
    "validation.bin_type_invalid": "bin[{id}].type={type!r} inválido, debe ser uno de {valid}",
    "validation.title_missing_text": "bin[{id}]: type: title requiere 'text'",
    "validation.title_font_size_invalid": "bin[{id}]: font_size debe ser mayor que 0, se recibió {font_size}",
    "validation.colorspace_invalid": "project.colorspace debe ser mayor que 0, se recibió {colorspace}",
    "validation.pip_size_invalid": "{ctx}: position/size debe tener ancho y alto positivos, se recibió w={w:g} h={h:g}",
    "validation.pip_out_of_bounds": (
        "{ctx}: position/size ([{x:g}, {y:g}] {w:g}x{h:g}) queda fuera "
        "del lienzo del proyecto ({width}x{height})"
    ),
    "validation.position_keyframes_conflicts": "{ctx}: 'position_keyframes' no se puede combinar con 'position'/'size' - usa uno u otro",
    "validation.position_keyframes_invalid": "{ctx}: 'position_keyframes' debe ser una lista no vacía de keyframes {{at, position, size}}",
    "validation.position_keyframes_keyframe_invalid": "{ctx}: 'position_keyframes[{idx}]' debe ser un mapeo con 'at', 'position' [x, y] y 'size' [w, h]",
    "validation.position_keyframes_must_start_at_zero": "{ctx}: el primer keyframe de 'position_keyframes' debe tener 'at: 0'",
    "validation.position_keyframes_not_sorted": "{ctx}: los keyframes de 'position_keyframes' deben tener valores 'at' estrictamente crecientes",
    "validation.position_keyframes_out_of_bounds": (
        "{ctx}: 'position_keyframes[{idx}]' ([{x:g}, {y:g}] {w:g}x{h:g}) queda fuera "
        "del lienzo del proyecto ({width}x{height})"
    ),
    "validation.crop_conflicts": "{ctx}: 'crop' no se puede combinar con 'position'/'size'/'position_keyframes' - usa uno u otro",
    "validation.crop_invalid": "{ctx}: 'crop' debe ser un mapeo con 'top'/'right'/'bottom'/'left'",
    "validation.crop_negative": "{ctx}: los márgenes de 'crop' ('top'/'right'/'bottom'/'left') no pueden ser negativos",
    "validation.freeze_before_after_without_at": "{ctx}: 'freeze_before'/'freeze_after' requieren que 'freeze_at' esté definido",
    "validation.freeze_at_negative": "{ctx}: 'freeze_at' no puede ser negativo, se recibió {freeze_at}",
    "validation.freeze_at_out_of_bounds": "{ctx}: 'freeze_at'={freeze_at} queda fuera de la duración de este clip (0..{duration}s)",
    "validation.track_missing_type": "timeline.tracks[{idx}] inválida (falta 'type'): {item!r}",
    "validation.track_type_invalid": "timeline.tracks[{idx}].type={type!r} debe ser uno de {valid}",
    "validation.pan_on_video_track": "timeline.tracks[{idx}]: 'pan' sólo se aplica a pistas de audio",
    "validation.pan_out_of_range": "timeline.tracks[{idx}]: 'pan' debe estar entre -1.0 y 1.0, se recibió {pan}",
    "validation.hidden_invalid": "timeline.tracks[{idx}]: 'hidden' debe ser true o false, se recibió {hidden!r}",
    "validation.locked_invalid": "timeline.tracks[{idx}]: 'locked' debe ser true o false, se recibió {locked!r}",
    "validation.audio_muted_invalid": "timeline.tracks[{idx}]: 'audio_muted' debe ser true o false, se recibió {audio_muted!r}",
    "validation.tracks_clips_not_list": "timeline.tracks[{idx}].clips debe ser una lista",
    "validation.clip_missing_fields": "{ctx} inválido (falta 'clip' o 'start'): {item!r}",
    "validation.pair_invalid": "{ctx}.{key} debe ser una lista [x, y] de 2 números",
    "validation.bin_duplicate_id": "bin: id de clip duplicado '{id}'",
    "validation.bin_missing_file": "bin[{id}]: no existe el archivo '{path}'",
    "validation.clip_unknown_reference": "{ctx}: referencia a clip desconocido '{clip}' (no está en 'bin:')",
    "validation.in_negative": "{ctx}: 'in' negativo ({in_s})",
    "validation.out_not_greater_than_in": "{ctx}: 'out' ({out_s}) debe ser mayor que 'in' ({in_s})",
    "validation.out_exceeds_duration": (
        "{ctx}: 'out' ({out_s:.3f}s) excede la duración real del clip "
        "'{clip}' ({source_s:.3f}s)"
    ),
    "validation.start_negative": "{ctx}: 'start' negativo ({start})",
    "validation.fade_negative": "{ctx}: fade_in/fade_out no pueden ser negativos",
    "validation.fade_exceeds_duration": (
        "{ctx}: fade_in ({fade_in}s) + fade_out ({fade_out}s) superan la "
        "duración del clip en el timeline ({clip_len:.3f}s)"
    ),
    "validation.mute_invalid": "{ctx}: 'mute' debe ser true o false, se recibió {mute!r}",
    "validation.source_in_out_incomplete": "{ctx}: 'source_in' y 'source_out' deben indicarse ambos, o ninguno",
    "validation.source_in_out_conflicts": "{ctx}: 'source_in'/'source_out' no se pueden combinar con 'in'/'out'/'duration' - usa uno u otro",
    "validation.source_in_out_invalid": "{ctx}: 'source_out' ({source_out}) debe ser mayor que 'source_in' ({source_in}), y 'source_in' no puede ser negativo",
    "validation.reveal_invalid": "{ctx}: 'reveal' debe ser 'iris', 'flip', 'glitch', 'delaygrab' o 'cairogradient', se recibió {reveal!r}",
    "validation.chroma_key_distance_invalid": "{ctx}: 'chroma_key_distance' debe estar entre 0.0 y 1.0, se recibió {chroma_key_distance}",
    "validation.overlay_font_size_invalid": "{ctx}: 'overlay_font_size' debe ser mayor que 0, se recibió {overlay_font_size}",
    "validation.quad_invalid": "{ctx}.{key} debe ser una lista [x, y, w, h] de 4 números",
    "validation.ken_burns_incomplete": "{ctx}: 'ken_burns_from' y 'ken_burns_to' deben indicarse ambos, o ninguno",
    "validation.ken_burns_size_invalid": "{ctx}: los anchos/altos de 'ken_burns_from'/'ken_burns_to' deben ser mayores que 0",
    "validation.focus_rect_size_invalid": "{ctx}: el ancho y alto de 'focus_rect' deben ser mayores que 0",
    "validation.numeric_range_invalid": "{ctx}: '{field}' debe estar entre {min} y {max}, se recibió {value}",
    "validation.speed_zero": "{ctx}: 'speed' no puede ser 0 (usa un valor positivo para avance, negativo para reversa)",
    "validation.speed_on_image": "{ctx}: 'speed' no tiene efecto en un clip de imagen o título ('{clip}')",
    "validation.speed_ramp_conflicts_with_speed": "{ctx}: 'speed_ramp' no se puede combinar con 'speed' - usa uno u otro",
    "validation.speed_ramp_invalid": "{ctx}: 'speed_ramp' debe ser una lista no vacía de keyframes {{at, speed}}",
    "validation.speed_ramp_image_mode_invalid": "{ctx}: 'speed_ramp_image_mode' debe ser 'nearest' o 'blend', se recibió {mode!r}",
    "validation.speed_ramp_keyframe_invalid": "{ctx}: 'speed_ramp[{idx}]' debe ser un mapeo con 'at' y 'speed'",
    "validation.speed_ramp_speed_invalid": "{ctx}: 'speed_ramp[{idx}].speed' debe ser mayor que 0, se recibió {speed}",
    "validation.speed_ramp_must_start_at_zero": "{ctx}: el primer keyframe de 'speed_ramp' debe tener 'at: 0'",
    "validation.speed_ramp_not_sorted": "{ctx}: los keyframes de 'speed_ramp' deben tener valores 'at' estrictamente crecientes",
    "validation.speed_ramp_in_not_supported": "{ctx}: 'in' no es compatible junto con 'speed_ramp' - un ramp siempre empieza en el inicio propio de la fuente",
    "validation.speed_ramp_on_image": "{ctx}: 'speed_ramp' no tiene efecto en un clip de imagen o título ('{clip}')",
    "validation.speed_ramp_requires_duration": "{ctx}: 'speed_ramp' necesita 'out' o 'duration' explícito - no se infiere automáticamente",
    "validation.speed_ramp_breakpoint_out_of_range": "{ctx}: el keyframe de 'speed_ramp' en {at}s cae en o después de la duración propia del clip ({out_s}s)",
    "validation.speed_ramp_exceeds_source": "{ctx}: 'speed_ramp' consumiría {consumed_s}s de material fuente, pero solo hay {available_s}s disponibles",
    "validation.clip_overlap": (
        "timeline.tracks[{idx}]({name}): solape entre clips '{prev}' "
        "(termina en fotograma {prev_end}) y '{next}' (empieza en fotograma {next_start})"
    ),
    "validation.scene_clip_undefined": "{ctx}.clips[{idx}]: clip '{clip_id}' no está definido en 'bin:'",
    "validation.scene_clip_invalid": "{ctx}.clips[{idx}] inválido: {raw!r}",
    "validation.scene_missing_clips": "scenes[{idx}] inválida (falta 'clips'): {scene!r}",
    "validation.scene_clips_not_list": "{ctx}.clips debe ser una lista no vacía",
    "validation.scene_transition_invalid": "{ctx}.transition={transition!r} debe ser uno de 'none', 'dissolve', 'wipe', 'affine', 'iris', 'flip', 'glitch', 'delaygrab', 'cairogradient'",
    "validation.scene_pacing_invalid": "{ctx}.pacing={pacing!r} debe ser uno de {valid}",
    "validation.scene_luma_invalid": "{ctx}.luma={luma!r} debe ser uno de {valid}",
    "validation.scene_transition_needs_video": "{ctx}: transition: {transition} no tiene equivalente en audio (transforma/enmascara píxeles geométricamente, no muestras) - usa 'dissolve' para una escena solo de audio",
    "validation.scene_style_invalid": "{ctx}.style={style!r} debe ser uno de {valid}",
    "scenes.track_a": "Escenas A",
    "scenes.track_b": "Escenas B",
    "scenes.track_a_audio": "Escenas A (audio)",
    "scenes.track_b_audio": "Escenas B (audio)",
    "scenes.track_burst": "Escenas Burst",
    "reverse.no_profile": "El archivo no tiene <profile>: no parece un proyecto MLT/kdenlive válido",
    "reverse.no_sequence_found": "No se ha encontrado ninguna secuencia (tractor con kdenlive:uuid) en el archivo",
    "reverse.broken_reference": "{ctx}: referencia rota a '{ref}'",
    "reverse.secondary_playlist_with_content": "{ctx}: playlist secundario con contenido (función 'mix' de kdenlive)",
    "reverse.mix_track_warning": (
        "{ctx}: esta pista usa la función de 'mix' de kdenlive (clips en dos "
        "sub-playlists); sólo se importa el primer playlist con contenido, "
        "el resto queda en x_kdenlive_raw"
    ),
    "reverse.unrecognized_track_structure": "{ctx}: estructura de pista no reconocida (<{tag}> inesperado)",
    "reverse.qtblend_rect_unreadable": "{ctx}: filtro qtblend con 'rect' ilegible",
    "reverse.qtblend_animated_position": "{ctx}: filtro qtblend con posición animada (varios keyframes)",
    "reverse.unrecognized_filter": "{ctx}: filtro no reconocido ({service}/{kid})",
    "reverse.unexpected_playlist_element": "{ctx}: elemento inesperado en playlist (<{tag}>)",
    "reverse.entry_missing_producer": "{ctx}: entry referencia un producer inexistente '{producer_id}', se omite",
    "reverse.clip_no_resource": "{ctx}: clip con producer sin 'resource' reconocible ({tag} {producer_id})",
    "reverse.timewarp_unsupported": "{ctx}: clip con cambio de velocidad (timewarp), no soportado",
    "reverse.speed_ramp_unsupported": "{ctx}: clip con rampa de velocidad (timeremap), no reconstruible en keyframes de speed_ramp:",
    "reverse.title_unsupported": "{ctx}: clip de título más complejo de lo que este generador puede representar (varios elementos, animación, elementos de imagen/rectángulo, o archivo ilegible), no soportado",
    "reverse.cross_track_transition_unsupported": "transición entre pistas ({service}) entre dos pistas de contenido (p. ej. un wipe) - no reconstruible en el modelo YAML de bajo nivel, se conserva tal cual",
    "reverse.track_not_imported": "{ctx}: pista no importada",
    "reverse.extra_sequence_not_imported": "secuencia adicional no importada (sólo se convierte la activa)",
    "reverse.guides_unreadable": "sequenceproperties.guides no es el array JSON esperado de entradas {{pos, comment, type}}, se conserva tal cual",
    "cli.build_done": "Generado {output} ({frames} fotogramas, {fps:g} fps)",
    "cli.validate_done": "OK: {n_tracks} pista(s), {n_clips} clip(s) colocados, {frames} fotogramas totales",
    "cli.import_error": "Error importando '{path}': {exc}",
    "cli.import_done": "Generado {output}",
    "cli.warning_prefix": "  aviso: {w}",
    "cli.raw_items_notice": (
        "  {n} elemento(s) no reconocido(s) conservados en 'x_kdenlive_raw:' dentro de "
        "{output} (revísalos; no se han perdido, pero tampoco se regenerarán con 'kdenlive-as-code build')."
    ),
    "cli.reconstruct_missing_extra": (
        "'reconstruct' necesita las dependencias opcionales numpy/Pillow. "
        "Instálalas con: pip install kdenlive-as-code[reconstruct]"
    ),
    "reconstruct.ffmpeg_or_ffprobe_missing": (
        "No se ha encontrado ffmpeg/ffprobe en el PATH; 'reconstruct' los "
        "necesita para decodificar frames y audio. Instala ffmpeg (trae "
        "ffprobe incluido) y asegúrate de que esté en el PATH."
    ),
    "reconstruct.ffmpeg_failed": "ffmpeg falló: {stderr}",
    "reconstruct.ffmpeg_timeout": "ffmpeg no terminó en {timeout}s (un proceso realmente colgado, o metraje demasiado largo/lento para decodificar en ese margen)",
    "reconstruct.ffprobe_failed": "ffprobe falló en '{path}': {stderr}",
    "reconstruct.ffprobe_timeout": "ffprobe no terminó en 30s en '{path}'",
    "reconstruct.no_video_stream": "'{path}' no tiene ninguna pista de vídeo",
    "reconstruct.unexpected_wav_sample_width": "ancho de muestra WAV inesperado de ffmpeg: {width}",
    "reconstruct.no_confident_match": "{where}: sin coincidencia fiable ({detail})",
    "reconstruct.best_guess_detail": "la más cercana fue '{best_guess}' con {confidence_pct}% de confianza",
    "reconstruct.no_candidate": "ningún candidato (banco de activos vacío, o sin señal en este segmento)",
    "reconstruct.progress.probing": "Analizando '{name}'...",
    "reconstruct.progress.building_visual_bank": "Escaneando activos para el emparejamiento visual...",
    "reconstruct.progress.visual_asset_image": "  - {name} (imagen)",
    "reconstruct.progress.visual_asset_video": "  - {name} (vídeo, extrayendo firmas de fotograma)",
    "reconstruct.progress.building_audio_bank": "Escaneando activos para el emparejamiento de audio...",
    "reconstruct.progress.audio_asset": "  - {name}",
    "reconstruct.progress.extracting_render_signatures": "Extrayendo firmas de fotograma de '{name}' (puede tardar en metraje largo)...",
    "reconstruct.progress.segments_found": "{n} segmento(s) de corte de escena encontrados, emparejando cada uno con el banco de activos...",
    "reconstruct.progress.matching_segment": "  [{i}/{n}] segmento {start}s-{end}s...",
    "reconstruct.progress.matching_audio": "Extrayendo y emparejando la pista de audio del render...",
    "cli.reconstruct_error": "Error reconstruyendo a partir de '{path}': {exc}",
    "cli.reconstruct_done": "Generado {output}",
    "cli.reconstruct_unmatched_notice": (
        "  {n} segmento(s) sin coincidencia fiable conservados en 'x_reconstruct_unmatched:' "
        "dentro de {output} (revísalos a mano; no se ha adivinado nada)."
    ),
    "cli.help.description": "Generador de proyectos kdenlive a partir de YAML",
    "cli.help.build": "Genera un .kdenlive a partir de un YAML",
    "cli.help.project_path": "Ruta al archivo .yaml del proyecto",
    "cli.help.build_output": "Ruta de salida (por defecto: mismo nombre con extensión .kdenlive)",
    "cli.help.validate": "Valida un YAML sin generar el .kdenlive",
    "cli.help.import": "Convierte un .kdenlive existente a YAML",
    "cli.help.kdenlive_path": "Ruta al archivo .kdenlive de origen",
    "cli.help.import_output": "Ruta de salida (por defecto: mismo nombre con extensión .yaml)",
    "cli.help.reconstruct": "Reconstruye un YAML a partir de un vídeo ya renderizado + una carpeta de assets (best-effort, matching por contenido)",
    "cli.help.reconstruct_video": "Ruta al archivo de vídeo ya renderizado",
    "cli.help.reconstruct_assets_dir": "Ruta a la carpeta de assets candidatos",
    "cli.help.reconstruct_output": "Ruta de salida (por defecto: mismo nombre con extensión .yaml)",
    "cli.render_missing_melt": (
        "No se encontró 'melt' (el motor MLT con el que renderiza el propio "
        "kdenlive) en PATH. Instala kdenlive o MLT, o genera el .kdenlive con "
        "'kdenlive-as-code build' y renderízalo tú mismo."
    ),
    "cli.render_failed": "melt falló renderizando '{path}': {stderr}",
    "cli.render_done": "Renderizado {output} ({frames} fotogramas, {fps:g} fps)",
    "cli.help.render": "Genera un .kdenlive a partir de un YAML y lo renderiza a vídeo con melt",
    "cli.help.render_output": "Ruta de salida del vídeo (por defecto: mismo nombre que el YAML, extensión .mp4)",
    "cli.help.keep_kdenlive": "Conserva el .kdenlive intermedio en vez de descartarlo (por defecto: mismo nombre que el YAML, extensión .kdenlive)",
    "cli.render_kept_kdenlive": "Conservado {path}",
    "cli.help.lang": "Idioma de salida (sobrescribe KDENLIVE_AS_CODE_LANG para esta ejecución)",
    "cli.write_failed": "Error al escribir '{path}': {exc}",
}

CATALOGS: dict[str, dict[str, str]] = {"en": _EN, "es": _ES}


def _detect_locale() -> str:
    lang = (os.environ.get("KDENLIVE_AS_CODE_LANG") or "").strip().lower()
    return lang if lang in CATALOGS else "en"


LOCALE = _detect_locale()


def set_locale(lang: str) -> None:
    """Overrides `KDENLIVE_AS_CODE_LANG` for the current process - used by the CLI's
    `--lang` flag, which needs to win over the environment variable and
    take effect before `build_parser()` builds its (already-localized)
    help text."""
    global LOCALE
    lang = (lang or "").strip().lower()
    if lang in CATALOGS:
        LOCALE = lang


def msg(key: str, **kwargs) -> str:
    template = CATALOGS.get(LOCALE, _EN).get(key) or _EN[key]
    return template.format(**kwargs)
