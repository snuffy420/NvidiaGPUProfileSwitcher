# NVIDIA GPU Driver Profile Switcher v1.0

A Windows utility for making older NVIDIA GPUs easier to use alongside newer
NVIDIA GPUs. New driver releases eventually drop support for older cards, so a
mixed-generation system may need an older shared driver for multi-GPU work and
a newer driver for applications using the newer cards.

The program saves those driver packages and GPU enabled/disabled states as
profiles, making it easier to switch between the two configurations. This is an
independent community project, not an NVIDIA product.

## Purpose

The primary purpose is managing **older and newer NVIDIA cards in the same
computer** when they no longer share support in the latest driver releases.

- Keep a compatible older driver with the required GPUs enabled for local AI
  and other multi-GPU workloads.
- Switch to a newer driver for supported GPUs, with excluded older cards saved
  as disabled in that profile.
- Return to the shared-driver profile and re-enable its required GPUs without
  repeatedly removing hardware or downloading the same packages.

The program is not restricted to particular NVIDIA models. A shared-driver
profile requires a driver version that actually supports all its target cards;
profiles do not add hardware support that NVIDIA's driver does not provide.

Switching is a Windows driver installation and may require a restart. Profiles
do not create independent active NVIDIA driver stacks for different cards or
applications. Preserve the version tested with your actual AI environment.

## Start

1. Install **64-bit Python 3.10+** with Tkinter on Windows 10 version 2004+ or
   Windows 11 x64. No pip packages are required.
2. Extract the complete project into a writable folder. Keep all Python modules,
   `assets`, and `tools` together.
3. Double-click **NVIDIA GPU Driver Profile Switcher.cmd**.
4. Choose **Appearance → Dark / Light**. Your preference is saved locally.
5. Use **Restart as administrator** when ready to change GPU/driver state.

Launching the application, selecting a profile, checking for updates, and
caching an installer do not change devices. New users start with an empty
library: no other person's GPU IDs or driver packages are included.

## Save your working AI profile

With all required GPUs working on the same driver, choose **Driver library →
Manage profiles → Capture working drivers**. Then choose **More cache options →
Cache saved profiles** to copy the matching complete package from Windows.
The original package is retained.

Profiles record the exact GPU instance IDs and expected version. Code 0 and
matching display-driver versions do not establish CUDA/framework compatibility;
test a small workload after changing your AI configuration.

Profiles also save device states. **Capture working drivers** records healthy
GPUs as driver targets and currently disabled GPUs as **Keep disabled**. Under
**Manage profiles → Edit selected**, choose **Use profile driver**, **Keep
disabled**, or **Leave unchanged** for each GPU. Confirmed switching applies
those states; selecting a profile alone does not change devices.

## Cache a fresh NVIDIA EXE

Click **Cache NVIDIA installer (.exe)…**, choose a downloaded offline driver
installer, and select its target GPU(s). If a newer package excludes an older
adapter, leave that adapter outside the profile and disabled when switching.

The app checks the NVIDIA Authenticode signature, extracts only the display
package using the bundled portable 7-Zip tool, detects the version from INF
metadata, and asks Windows to check each target GPU's compatibility. It creates
a separate cached profile. It does not execute the downloaded EXE or
`setup.exe`, stage a package, or enable a GPU.

The full EXE remains an optional fallback attached to the profile. Keep that
original file if you want to use the full installer later.

Under **More cache options**:

- **Cache installed version** saves a currently working installed version.
- **Cache saved profiles** finds saved versions already available in Windows.
- **Import extracted INF** accepts a complete extracted `Display.Driver`
  folder with its catalog and payload files. An INF alone is insufficient.

## Check for updates

**Check NVIDIA updates…** queries NVIDIA's official public catalogue for each
present NVIDIA GPU, including disabled ones. It resolves exact product names
through NVIDIA's live product-family, series, and product catalogue rather than
a hardcoded model list. Desktop, notebook, professional, and data-center products
use the same lookup flow. If a name cannot be resolved unambiguously, manual
EXE/INF import remains available and Windows checks hardware compatibility.

Only compatible non-beta WHQL Windows x64 packages are offered. Choose a
single-GPU update or, when the catalogue lists the same newer package for every
target, a common update for both/all GPUs. Different latest branches are never
combined into a simultaneous mixed-driver configuration. A single-card update
is not treated as a shared multi-GPU upgrade; update choices require selection.

**Download, cache and review switch** downloads from NVIDIA's HTTPS servers and
performs signature, version, and INF checks. With administrator rights, a
separate switch confirmation follows. Your tested AI profile and older cache
copies remain available. Nothing is installed automatically in the background.

A failed request means unknown availability, not “up to date.” The website
service may change; manual EXE import remains available.

## Switch profiles

Close games, AI jobs, and GPU monitoring tools first.

- **AI:** select the tested profile requiring both GPUs. Leave **Re-enable this
  profile's GPUs** checked when returning from gaming mode.
- **Gaming:** connect monitors to the main GPU and save excluded adapters as
  **Keep disabled** in the gaming profile. A confirmed switch disables those
  adapters before applying the target driver. Re-enabling only applies to GPUs
  marked **Use profile driver** inside that selected profile.

Click **Switch using cached package** and review the exact target IDs. The app
checks every cache file's SHA-256, validates compatibility, preserves previous
packages/inventories, stages the signed package, then uses Windows'
`DiInstallDevice` API to select the driver on each target device. It does not
delete newer packages or rely on PnPUtil ranking to downgrade.

An active outside-profile NVIDIA GPU on a different version blocks a switch.
Afterward, outside-profile versions and disabled states are compared with the
original inventory. Unexpected changes prevent a successful result.

Displays may briefly blank. Restart if Windows requests it, then **Verify
loaded profile**. Every required GPU must be present, have Code 0, and match the
version. A partial failure stops further changes and is logged; earlier device
changes may have completed. The app never automatically rolls back or reboots.

Cached mode changes the display-driver package. It does not update NVIDIA App,
PhysX, HD Audio, or every full-installer component. **Use full installer** runs
the signed NVIDIA installer interactively; that installer controls its own
hardware handling.

## Device controls and error codes

The **GPUs** tab shows present/hidden adapters, exact IDs, versions, INF names,
device errors and known desktop connections. Disabling leaves a GPU installed,
unlike uninstalling and rediscovering it.

The app blocks disabling a GPU known to host a desktop and blocks disabling the
last present healthy adapter. An unknown connection does not establish that
disconnecting it is safe; check monitor cabling.

Before disabling, the app saves an inventory and `ENABLE-THIS-GPU.cmd` in
`recovery`. Run that helper as administrator to re-enable the exact GPU if
necessary. A working display or remote session is still required.

**Error reference** includes codes **0–57**, with legacy/internal entries marked
and Microsoft links. **Code 22** is a disabled device; **Code 32** is a disabled
driver service. Service startup values are not modified automatically.
