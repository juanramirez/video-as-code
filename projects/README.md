# projects/

Personal projects made with `kdenlive-as-code`, each in its own
subdirectory. This directory lives inside the tool's own repo for
convenience, but its contents (aside from this README) are excluded via
`.gitignore` — it isn't part of `kdenlive-as-code`'s own code.

## Organization

Each subdirectory under `projects/` is:

- Its own independent git repository (`git init` inside it), local
  only — no remote configured, no push.
- Owner of its own `.gitignore` to exclude raw footage (source video/
  audio): large binary files not worth versioning. Only the project's
  "code" gets versioned — `project.yaml` and the like — not the assets.

Example: `projects/summer-vacation-2026/`.
