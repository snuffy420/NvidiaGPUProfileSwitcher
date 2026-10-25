<!-- Save this file as README.md at the root of the GitHub repository. -->
# NVIDIA GPU Driver Profile Switcher

**Version 1.0 · Windows 10/11 x64 · Portable EXE or Python source**

A Windows desktop utility for making **older NVIDIA GPUs easier to use alongside
newer NVIDIA GPUs**. New NVIDIA driver releases eventually drop support for
older cards. This program helps mixed-generation systems keep a compatible
older driver for shared workloads and switch to a newer driver when using only
the GPUs it supports.

It saves driver packages and the GPUs that should be enabled or disabled as
profiles, so you can switch configurations without repeatedly removing hardware
or downloading the same installers.

This is an independent community project, not an NVIDIA product.

## Why use it?

The main problem this program addresses is **older and newer NVIDIA cards in
the same computer losing access to a common latest driver**. You may still want
to use the older cards for local AI or another multi-GPU workload, while needing
new driver releases for games or applications on newer cards.

Use one profile for a compatible shared driver with the required cards enabled,
and another for a newer driver with unsupported older adapters disabled. Return
to the shared profile when you need those cards again. The program is designed
for NVIDIA GPUs across generations and product families, not a specific pair.

A shared profile still requires a driver that supports every target GPU. The
program manages installation and device states; it cannot add support for a GPU
that NVIDIA's selected driver does not support.

The program performs a real Windows driver installation when switching.
Profiles do not create separate, simultaneously active NVIDIA driver stacks.

## Screenshots

<img width="576" alt="image" src="https://github.com/user-attachments/assets/0aa6e3ff-f4da-4d77-8975-0881b42d3b25" />
<img width="576" alt="image" src="https://github.com/user-attachments/assets/b52fa819-aec7-4728-a9d0-5d6b5244535e" />
<img width="576" alt="image" src="https://github.com/user-attachments/assets/cc787a1f-3acd-4963-9bc3-63a3b6d77666" />
<img width="576" alt="image" src="https://github.com/user-attachments/assets/a6d8cb8c-1bda-4029-9b0d-8a15e4525d72" />
<!-- Add your pictures here. GitHub also lets you drag images into its Markdown editor. -->
<!-- Example: ![Dark theme](docs/images/dark-theme.png) -->
<!-- Example: ![Light theme](docs/images/light-theme.png) -->
<!-- Example: ![GPU device controls](docs/images/gpu-controls.png) -->
<!-- Example: ![NVIDIA update checker](docs/images/update-checker.png) -->

## Features

- **Saved driver profiles:** record the expected driver version and the exact
  GPUs that must work with it, including adapters that must stay disabled.
- **Cached switching:** retain multiple display-driver packages and install the
  selected version on the profile's GPUs.
- **Fresh EXE import:** select an official NVIDIA offline installer and extract
  its display driver into the cache without running NVIDIA setup.
- **Installed-driver capture:** save a working version already on the computer.
- **Per-GPU update checks:** check the official NVIDIA catalogue for recognized
  GPUs and choose a compatible update.
- **Device controls:** identify, enable, or disable individual GPUs.
- **Verification and recovery:** check signatures, cache integrity, compatibility,
  device state, and driver versions; preserve history and recovery files.
- **Error reference:** explanations and Microsoft links for Device Manager codes
  0–57, with legacy/internal codes marked.
- **Dark/light appearance:** saved theme preference.

## Requirements

- Windows 10 version 2004 or newer, or Windows 11, **x64**.
- For the source version only: **64-bit Python 3.10+** with Tcl/Tk.
- Administrator rights when changing drivers or enabling/disabling GPUs.
- Free disk space for extracted packages and cached versions.
- Internet access for update checks and downloads; existing caches can be used
  without a new download.

The Windows EXE includes Python, Tcl/Tk, and the required resources. Neither
version needs pip packages to run. The portable extraction tool is included.

## Installation

### Option 1: Portable Windows EXE

1. Download **NVIDIA-GPU-Driver-Profile-Switcher-v1.0-Portable-Windows-x64.7z** from
   the repository's Releases page.
2. Extract the complete folder to a writable location using 7-Zip or another
   compatible extractor. Open that folder and double-click the EXE.
   Do not run it directly from inside the archive viewer.

Python, Tcl/Tk, and runtime resources are bundled. No Python installation, pip
commands, setup wizard, or startup downloads are needed. Driver updates are
downloaded only when you explicitly choose to download them.

**Why is the EXE inside another folder?** It creates settings/profile files and
folders beside itself as features are used: `driver_cache`, `downloads`,
`driver_history`, `recovery`, and temporary `.driver_imports` work files. The
containing folder keeps those files together instead of scattering them across
your Desktop or Downloads folder. Keep the EXE inside that folder, which
must be writable. If you move the program, move its whole folder to keep
your saved profiles and cached packages together. Avoid running the EXE loose
on the Desktop or directly in a shared Downloads folder.

### Option 2: Python source

1. Install **64-bit Python 3.10+** from [python.org](https://www.python.org/downloads/windows/),
   including the **Tcl/Tk and IDLE** optional component.
2. Run `python -m tkinter` to verify that the GUI toolkit is installed.
   If it is missing, rerun the Python installer, choose **Modify**, and enable
   **Tcl/Tk and IDLE**. Do not use `pip install tkinter`: Tkinter comes from
   the Python installation. No pip packages are required by this application.
3. Download **NVIDIA-GPU-Driver-Profile-Switcher-v1.0-Source.7z** and extract it
   into a writable folder using 7-Zip or another compatible extractor. Keep the Python files,
   `assets`, and `tools` together.
4. Run **NVIDIA GPU Driver Profile Switcher.cmd**.

Both versions support **Appearance → Dark / Light** and **Restart as
administrator** when ready to make a device or driver change. Profiles,
settings, caches, and recovery files are stored beside the EXE or source files.

New users start with an empty driver library. No NVIDIA driver packages or
another user's GPU configuration are supplied.

## Set up your profiles

### Save the working AI configuration

1. Make sure all required GPUs work on the shared driver used by your AI tools.
2. Open **Driver library → Manage profiles → Capture working drivers**.
3. Give the profile a recognizable name.
4. Choose **More cache options → Cache saved profiles**.

This preserves a copy of the compatible package without removing the original
from Windows. Keep this tested profile when adding newer versions.

Capturing a configuration with a disabled card saves that card as **Keep
disabled**. You can also set each GPU's role under **Manage profiles → Edit
selected**:

- **Use profile driver:** must be enabled, healthy, and use the profile version.
- **Keep disabled:** disable this adapter when switching and retain its bound
  package.
- **Leave unchanged:** do not request a device-state or driver change for it.

Selecting a profile does not apply these changes. They are applied only after
you confirm the switch, with desktop and last-healthy-GPU guards still enforced.

### Add a newer downloaded driver

1. Download the official NVIDIA offline driver `.exe` for your GPU.
2. Click **Cache NVIDIA installer (.exe)…** and select that file.
3. Select the GPUs supported by the new package. Leave unsupported or
   intentionally disabled adapters outside that profile.
4. Wait for signature, extraction, version, and compatibility checks to finish.

Caching does not install the driver, execute NVIDIA setup, or enable a GPU.
The detected version comes from the package contents rather than its filename.

**More cache options** also provides **Cache installed version** and
**Import extracted INF**. INF import requires a complete extracted display
package with its catalog and payload files, not an INF copied by itself.

## Switch between AI and gaming

Close games, AI jobs, and GPU monitoring tools first.

| Use | Configuration |
| --- | --- |
| Multi-GPU AI | Select the tested shared-driver profile requiring both GPUs. Enable **Re-enable this profile's GPUs** when returning from a setup where a required GPU was disabled. |
| Gaming on the newer GPU | Connect monitors to the main GPU, select the newer main-GPU profile, and save excluded adapters as **Keep disabled**. The confirmed switch applies those disabled states before installing the target package. |

Click **Switch using cached package…**, review the exact targets, and confirm.
The re-enable option only affects GPUs inside the selected profile.

The application preserves previous package references and inventories, checks
the cached files, and uses Windows' exact-device installation API to select
the driver. An active outside-profile NVIDIA GPU using a different version
blocks the switch. Its disabled state and bound version are compared afterward
so unexpected changes are reported.

An outside-profile GPU explicitly saved as **Keep disabled** is disabled as part
of the confirmed switch. A recovery helper is saved first. If that state cannot
be reached, target-driver installation stops. Saved disabled states are included
in profile verification.

Displays may briefly blank. A restart is not always required, but restart when
Windows requests it, then click **Verify loaded profile**. The application never
restarts Windows automatically. If a multi-GPU switch fails partway through,
earlier changes may have completed; the result and history identify the issue.

## Driver updates

Click **Check NVIDIA updates…** to query the official catalogue for each
recognized present GPU, including disabled cards.

The checker resolves exact product names through NVIDIA's live product-family,
series, and product catalogue. There is no fixed list of supported GPU models.
The lookup covers NVIDIA's desktop, notebook, professional, and data-center
catalogue entries. Both standard and DCH Windows package searches are checked.

If an exact product name cannot be resolved, or desktop/notebook variants have
conflicting packages, the app reports that uncertainty instead of guessing.
Manual EXE/INF import remains available, and Windows must confirm compatibility
for the selected hardware before a package can be installed. A suitable NVIDIA
driver must exist for your GPU and Windows version.

The checker offers compatible non-beta WHQL Windows x64 packages. A both-GPU
update is offered only when the same newer package is returned for every
target. Separate latest driver branches are not combined. A single-card update
does not replace a tested shared-driver configuration, and no update choice is
selected automatically.

**Download, cache and review switch…** downloads the selected package, verifies
it, and prepares a profile before a separate installation confirmation. Older
cached versions remain available. Failed catalogue requests are reported as
unknown availability, not as “up to date.”

## Verification and recovery

- Every profile GPU must be present, have Device Manager Code 0, and match the
  expected driver version for verification to pass.
- A matching driver does not prove CUDA, framework, or AI workload compatibility.
  Run a small workload in your actual environment after switching.
- Before disabling a GPU, the program saves an inventory and an
  `ENABLE-THIS-GPU.cmd` recovery helper. Run the helper as administrator if needed.
- The app blocks disabling a known desktop adapter or the last present healthy
  GPU. If the desktop connection is unknown, check monitor cabling yourself.
- **Code 22** means the device is disabled. **Code 32** means its driver service
  is disabled. The program does not automatically change service startup values.

## What cached switching includes

Cached mode changes the **display-driver package**. It does not update every
component of the full NVIDIA installer, including NVIDIA App, PhysX, or HD Audio.
Use **Use full installer…** when those components are needed. The full NVIDIA
installer controls its own hardware handling.

## Local files

Profiles, downloaded installers, cached drivers, theme settings, switch history,
and recovery files are created locally and excluded by `.gitignore`.
Do not upload those machine-specific files to the repository.

Cache manifests use absolute paths. If you move the application, recache or
reimport the affected packages. `python gpu_manager.py --list` provides a
read-only inventory; **Export diagnosis** saves the currently displayed devices.

## License and third-party assets

This project is licensed under the [GNU General Public License v3.0](LICENSE).

NVIDIA trademarks, logo assets, and the bundled extractor have separate terms described in [NOTICE.md](NOTICE.md).


## USE AT YOUR OWN RISK!!

I will not be held responsible for anything that happens with this tool, it is still in testing and it could leave you with no drivers/display if used incorrectly!
