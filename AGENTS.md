# Repository instructions

- Give all new or modified Python variables explicit types, including instance attributes, containers, callbacks, and loop variables. Annotate function parameters and return values.
- Use precise application-owned types. Use `Any` only at genuinely dynamic Blender API boundaries.
- Fix diagnostics without weakening editor settings or adding blanket suppressions.
- Run applicable formatting, lint, syntax, and type checks before finishing. Reproduce the editor's actual import paths when checking its context; report validation limits honestly.
- Keep comments short and descriptive. Preserve behavior during typing, commenting, and formatting fixes.
- This is the standalone Blender application. Work in `app/drawing_app`, its launch scripts, download page, and `wizard`. Do not add Omniverse/Kit dependencies.
- Keep engine binaries, runtime profiles, development caches, and generated application packages out of Git. The downloadable setup executable in `wizard/dist` is intentionally committed; rebuild it after wizard changes.
