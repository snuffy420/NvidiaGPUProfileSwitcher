# Changelog

## v2.2.0 — 2026-11-14

- Remove the redundant administrator-status line from the main window.
- Simplify profile creation: select one cached driver and the GPUs to target;
  automatically choose the matching display INF.
- Keep driver caching separate from profile creation, retaining all display-INF
  variants to support later GPU selection.
- Search a wider NVIDIA catalogue range for the newest package confirmed for all
  GPUs, label per-GPU downgrades, and allow slower catalogue responses.

## v2.1.10 — 2026-11-14

- Remove the redundant administrator-status line from the main window.

## v2.1.9 — 2026-11-14

- Show one cached driver entry in profile creation and select its compatible
  display INF automatically from the chosen GPUs.

## v2.1.8 — 2026-11-14

- Increase the timeout for slow NVIDIA catalogue requests from 30 to 90 seconds.

## v2.1.7 — 2026-11-14

- Search up to 100 NVIDIA catalogue results per GPU when finding a common
  supported package.
- Offer the newest NVIDIA-confirmed package that supports all detected GPUs,
  with explicit per-GPU upgrade/downgrade labels.

## v2.1.6 — 2026-11-14

- Separate driver caching from saved profile creation for downloaded and
  imported NVIDIA installers.
- Cache all NVIDIA display INF variants together and let each profile choose
  the required INF and GPUs.
- Default new profiles to no GPUs selected and name incompatible GPUs on save.

## v2.1.5 — 2026-11-14

- Route the download/cache/install action through the verified cached-INF
  switch workflow instead of launching NVIDIA Setup.
- Clarify the cache-only message and the later cached-package install action.

## v2.1.4 — 2026-11-14

- Pass the detected Windows form factor through to NVIDIA catalogue matching.

## v2.1.3 — 2026-11-14

- Use Windows desktop/notebook classification to choose NVIDIA's matching
  catalogue branch when exact GPU names exist in multiple variants.
- Preserve the ambiguity error when hardware type is unknown or NVIDIA does
  not label the variants clearly.

## v2.1.2 — 2026-11-14

- Upgrade NVIDIA catalogue links on official NVIDIA hosts to HTTPS and report
  insecure-protocol errors separately from unrecognized hosts.

## v2.1.1 — 2026-11-14

- Check GitHub for stable updates at startup and offer an in-app install.
- Verify release installers against GitHub's published SHA-256 digest.
- Run Setup from the elevated app context and reopen the app after updating.

## v2.1 — 2026-11-14

### Changed

- Install the application for all users under Program Files and request
  administrator approval on every launch.
- Migrate profiles, settings, caches, and recovery data from the previous
  per-user install when the new version first starts.
- Let users delete a cached package after warning when profiles or recovery
  history refer to it; profile references are detached when needed.
- Download and cache confirmed NVIDIA catalogue updates, then offer the signed
  full installer from the same update flow.
- Expand NVIDIA catalogue URL handling for encoded links and official NVIDIA
  download hosts.
- Remove application-generated data during uninstall, including migrated data
  left by the older per-user installation.

## v2.0 — 2026-10-27

## v2.0 — 2026-10-27

### Added

- Qt interface with compact profile cards, green selection highlights, dark/light
  themes, a draggable custom header, and resizable information panels.
- Configurable driver-cache location with detection of existing app-cached
  packages, without moving or deleting their files.
- Cache manager showing versions, locations, saved-profile references, and
  recovery references. In-use packages are protected from deletion.
- Custom dark Inno Setup installer with green project artwork, per-user
  installation, Start menu entry, and optional desktop shortcut.
- Original GPU/profile-switching icon for the application, portable EXE,
  installer, shortcuts, README, and contact page. The in-app NVIDIA logo remains.
- Updated v2.0 screenshots and a project-themed contact form using the existing
  destination, with responsive layout and clear success/error feedback.

### Changed

- Source installations use PyQt6 instead of Tkinter. Binary packages bundle
  Python and Qt; no separate dependency installation or startup download is needed.
- Profiles can select existing cached packages with the actual driver version
  supplied from metadata and integrity/compatibility checks before saving.
- Profile selection uses clickable cards; the header holds the administrator
  button, and full driver details use the available window space.
- Current and previously selected cache folders remain available to profiles
  and the package picker. Changing folders does not move existing packages.
- Public packages include dependency notices, build scripts, and SHA-256 files;
  machine-specific settings, drivers, history, and internal tests are excluded.

### Fixed

- Isolated DLL discovery prevents unrelated tools' runtime files entering the
  release, resolving the packaged `QtWidgets` DLL import error.
- The multi-resolution application icon is embedded in executables and bundled
  for Qt window/taskbar use; installer shortcuts use the icon explicitly.
- Installer upgrades and uninstall retain user-created profiles, settings,
  caches, downloads, history, and recovery files.

### Retained

- Cached switching, NVIDIA EXE import, installed-driver capture, update checks,
  per-GPU saved states, verification, recovery, and Device Manager code reference.
- Actual GPU/driver changes require administrator rights and confirmation.
  Selecting, saving, importing, or caching profiles does not switch drivers.

## v1.0

- Cached NVIDIA display-driver profiles for older shared AI drivers and newer
  single-GPU gaming drivers.
- Persisted enabled/disabled GPU roles, applied on confirmed switching and checked
  during verification, with backward-compatible loading of existing profiles.
- Direct import of fresh NVIDIA offline EXE downloads without running setup.
- Dynamic NVIDIA catalogue matching across GPU product families, with
  single-GPU or compatible common-package selection and no fixed model list.
- Dark/light themes with saved preferences and transparent NVIDIA logo variants.
- Exact-device enable/disable controls, recovery helpers, cache integrity checks,
  switch history, verification and Device Manager code reference.
- Windows tests and repository exclusions for machine-local data.
