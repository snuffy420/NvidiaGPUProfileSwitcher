# NVIDIA GPU Driver Profile Switcher v1.0

An independent Windows utility for saving and switching NVIDIA display-driver
packages. Includes dark/light themes, GPU device controls, an EXE-to-cache
importer, and per-GPU driver update checks. Not affiliated with NVIDIA.

## Purpose

A GTX 1070 and RTX 3060 can share an older compatible driver for local AI tools,
while a newer gaming driver may support only the 3060. Save both packages:
use the older one with both GPUs enabled, or disable the 1070 and switch the
3060 to the newer one.

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

## Cache a fresh NVIDIA EXE

Click **Cache NVIDIA installer (.exe)…**, choose a downloaded offline driver
installer, and select its target GPU(s). For a newer 3060 gaming package,
select **only the RTX 3060**.

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
recognized present GPU, including disabled ones. The current automatic matcher
recognizes desktop **GTX 1070 (1B81)** and **RTX 3060 (2503/2504)**. Other cards
can use manual EXE/INF import and profiles; an unrecognized PCI ID is reported.

Only compatible non-beta WHQL Windows x64 packages are offered. Choose a
single-GPU update or, when the catalogue lists the same newer package for every
target, a common update for both/all GPUs. Different latest branches are never
combined into a simultaneous mixed-driver configuration. A 1070-only security
update is not preselected as an AI upgrade.

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
- **Gaming:** connect monitors to the main GPU, disable the older GPU on the
  **GPUs** tab, then select the newer main-GPU-only profile. Re-enabling only
  applies to GPUs inside that selected profile.

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

## Local data and repository sharing

`driver_profiles.json`, `ui_settings.json`, `driver_cache`, `downloads`,
`driver_history`, and `recovery` are machine-local and excluded by
`.gitignore`. Do not commit them. No NVIDIA driver packages are distributed
with this source project.

Cache manifests use absolute paths. After moving the application, recache or
reimport affected packages.

`python gpu_manager.py --list` prints a read-only inventory. **Export diagnosis**
saves the currently displayed inventory.

Runtime dependencies are the Python standard library and bundled portable
extractor. Image-rendering libraries are not needed to run the application.

Code: [MIT license](LICENSE). Third-party assets: [notices](NOTICE.md).

## References

- [NVIDIA driver downloads](https://www.nvidia.com/en-us/drivers/)
- [NVIDIA Pascal support plan](https://nvidia.custhelp.com/app/answers/detail/a_id/5676)
- [Microsoft PnPUtil](https://learn.microsoft.com/en-us/windows-hardware/drivers/devtest/pnputil-command-syntax)
- [Microsoft DiInstallDevice](https://learn.microsoft.com/en-us/windows/win32/api/newdev/nf-newdev-diinstalldevice)
- [Microsoft Device Manager error codes](https://learn.microsoft.com/en-us/windows-hardware/drivers/install/device-manager-error-messages)
- [7-Zip official downloads](https://www.7-zip.org/download.html)

