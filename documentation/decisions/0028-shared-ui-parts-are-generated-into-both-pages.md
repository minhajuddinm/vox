# 0028. Shared UI parts are generated into both pages

Status: Accepted
Date: 2026-09-30

## Context

The Windows window (pywebview) and the Android WebView each have one large `index.html` with its own copy of the palette, the card, button, chip and switch styles and a few helper functions (`$`, `esc`, `toast`, dictionary parsing, the About-you counter). The copies had started to drift, which is the same problem the cleanup rules have between Python and Java ([0007](0007-shared-golden-file.md)). The two pages load from different places (a folder next to the exe, the APK's assets) and the Android build is plain SDK tools with no bundler ([0012](0012-no-gradle-android-build.md)).

## Decision

- The shared parts live in `ui-shared/` (`tokens.css`, `components.css`, `common.js`). `python tools/sync_ui.py` writes them into both pages, inline, between marker comments (`/* ui-shared:css begin */`, `// ui-shared:js begin`). The pages stay single self-contained files, so packaging does not change.
- Only declarations that were identical in both pages moved. Each page keeps its own sizes, spacing, radius and bridge code next to the shared block. The dark palette is written once (`@dark { }`); the tool turns it into a `prefers-color-scheme` media query for Windows and a `.dark` class rule for Android.
- `sync_ui.py --check` and `tests/test_ui_shared.py` fail when a generated block was edited by hand or the sources changed without regenerating. The comment markers are CSS and JS comments, not HTML comments, because HTML comments inside `<style>` and `<script>` are fragile.

## Consequences

- A colour or component change is made once. Edit `ui-shared/`, run the tool, commit the sources and both pages.
- The pages are bigger in git diffs (the generated block is in each), and a reader who edits the block in a page gets a failing test, not a silent change.
- Computed styles of the shared classes were compared before and after in a browser (0 differences); JavaScript-rendered elements were not compared.
- A new page-level id lookup still needs the static checks' allowlist when it is computed at run time ([specs/p8b-design-refresh.md](../specs/p8b-design-refresh.md)).

## Alternatives considered

- Load the shared files at run time (`<link>`, `<script src>`): needs both packagers to ship extra files and a path that works in both WebViews, and breaks the single-file pages.
- A bundler (npm, Vite): adds a toolchain that neither build has.
- Keep two copies and a test that compares them: finds the drift after it happened, and someone still has to merge by hand.
