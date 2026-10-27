# Changelog

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
