# projects/

Personal projects made with `video-as-code`, each in its own
subdirectory. This directory lives inside the tool's own repo for
convenience, but its contents (aside from this README) are excluded via
`.gitignore` — it isn't part of `video-as-code`'s own code.

## Organization

Each subdirectory under `projects/` is:

- Its own independent git repository (`git init` inside it), with its
  own remote - a private one: a project's YAML names its shared albums
  and links, which must stay out of public view.
- Owner of its own `.gitignore` to exclude raw footage (source video/
  audio): large binary files not worth versioning. Only the project's
  "code" gets versioned — `project.yaml` and the like — not the assets.

Example: `projects/summer-vacation-2026/`.
