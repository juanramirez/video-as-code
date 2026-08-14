"""Message catalog for every string a `kdgen` user sees: validation errors,
CLI output, import warnings. English is the source of truth and the
fallback — both for locales that don't exist and for keys missing from a
locale that does. Pick a locale by setting `KDGEN_LANG` (e.g. `es`); it's
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
    "validation.project_field_required": "project.{key} is required",
    "validation.bin_entry_invalid": "Invalid 'bin:' entry (missing 'id' or 'path'): {item!r}",
    "validation.bin_type_invalid": "bin[{id}].type={type!r} is invalid, must be one of {valid}",
    "validation.track_missing_type": "timeline.tracks[{idx}] is invalid (missing 'type'): {item!r}",
    "validation.track_type_invalid": "timeline.tracks[{idx}].type={type!r} must be one of {valid}",
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
    "validation.clip_overlap": (
        "timeline.tracks[{idx}]({name}): overlap between clips '{prev}' "
        "(ends at frame {prev_end}) and '{next}' (starts at frame {next_start})"
    ),
    "validation.scene_clip_undefined": "{ctx}.clips[{idx}]: clip '{clip_id}' is not defined in 'bin:'",
    "validation.scene_clip_invalid": "{ctx}.clips[{idx}] is invalid: {raw!r}",
    "validation.scene_missing_clips": "scenes[{idx}] is invalid (missing 'clips'): {scene!r}",
    "validation.scene_clips_not_list": "{ctx}.clips must be a non-empty list",
    "validation.scene_transition_invalid": "{ctx}.transition={transition!r} must be 'none' or 'dissolve'",
    "validation.scene_pacing_invalid": "{ctx}.pacing={pacing!r} must be one of {valid}",
    "scenes.track_a": "Scenes A",
    "scenes.track_b": "Scenes B",
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
    "reverse.track_not_imported": "{ctx}: track not imported",
    "reverse.extra_sequence_not_imported": "additional sequence not imported (only the active one is converted)",
    "cli.build_done": "Generated {output} ({frames} frames, {fps:g} fps)",
    "cli.validate_done": "OK: {n_tracks} track(s), {n_clips} clip(s) placed, {frames} total frames",
    "cli.import_error": "Error importing '{path}': {exc}",
    "cli.import_done": "Generated {output}",
    "cli.warning_prefix": "  warning: {w}",
    "cli.raw_items_notice": (
        "  {n} unrecognized item(s) kept in 'x_kdenlive_raw:' inside {output} "
        "(review them; nothing was lost, but they won't be regenerated by 'kdgen build')."
    ),
    "cli.reconstruct_missing_extra": (
        "'reconstruct' needs the optional numpy/Pillow dependencies. "
        "Install them with: pip install kdenlive-generator[reconstruct]"
    ),
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
    "validation.project_field_required": "project.{key} es obligatorio",
    "validation.bin_entry_invalid": "Entrada de 'bin:' inválida (falta 'id' o 'path'): {item!r}",
    "validation.bin_type_invalid": "bin[{id}].type={type!r} inválido, debe ser uno de {valid}",
    "validation.track_missing_type": "timeline.tracks[{idx}] inválida (falta 'type'): {item!r}",
    "validation.track_type_invalid": "timeline.tracks[{idx}].type={type!r} debe ser uno de {valid}",
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
    "validation.clip_overlap": (
        "timeline.tracks[{idx}]({name}): solape entre clips '{prev}' "
        "(termina en fotograma {prev_end}) y '{next}' (empieza en fotograma {next_start})"
    ),
    "validation.scene_clip_undefined": "{ctx}.clips[{idx}]: clip '{clip_id}' no está definido en 'bin:'",
    "validation.scene_clip_invalid": "{ctx}.clips[{idx}] inválido: {raw!r}",
    "validation.scene_missing_clips": "scenes[{idx}] inválida (falta 'clips'): {scene!r}",
    "validation.scene_clips_not_list": "{ctx}.clips debe ser una lista no vacía",
    "validation.scene_transition_invalid": "{ctx}.transition={transition!r} debe ser 'none' o 'dissolve'",
    "validation.scene_pacing_invalid": "{ctx}.pacing={pacing!r} debe ser uno de {valid}",
    "scenes.track_a": "Escenas A",
    "scenes.track_b": "Escenas B",
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
    "reverse.track_not_imported": "{ctx}: pista no importada",
    "reverse.extra_sequence_not_imported": "secuencia adicional no importada (sólo se convierte la activa)",
    "cli.build_done": "Generado {output} ({frames} fotogramas, {fps:g} fps)",
    "cli.validate_done": "OK: {n_tracks} pista(s), {n_clips} clip(s) colocados, {frames} fotogramas totales",
    "cli.import_error": "Error importando '{path}': {exc}",
    "cli.import_done": "Generado {output}",
    "cli.warning_prefix": "  aviso: {w}",
    "cli.raw_items_notice": (
        "  {n} elemento(s) no reconocido(s) conservados en 'x_kdenlive_raw:' dentro de "
        "{output} (revísalos; no se han perdido, pero tampoco se regenerarán con 'kdgen build')."
    ),
    "cli.reconstruct_missing_extra": (
        "'reconstruct' necesita las dependencias opcionales numpy/Pillow. "
        "Instálalas con: pip install kdenlive-generator[reconstruct]"
    ),
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
}

CATALOGS: dict[str, dict[str, str]] = {"en": _EN, "es": _ES}


def _detect_locale() -> str:
    lang = (os.environ.get("KDGEN_LANG") or "").strip().lower()
    return lang if lang in CATALOGS else "en"


LOCALE = _detect_locale()


def msg(key: str, **kwargs) -> str:
    template = CATALOGS.get(LOCALE, _EN).get(key) or _EN[key]
    return template.format(**kwargs)
