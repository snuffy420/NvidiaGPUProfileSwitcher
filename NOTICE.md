# Third-party notices

This is an independent community utility, not an NVIDIA product. NVIDIA,
GeForce, and the NVIDIA logo are trademarks of NVIDIA Corporation. No
affiliation, sponsorship, or endorsement is claimed.

The application icon (`assets/app-icon.svg`, PNG, and ICO variants) is original
project artwork showing a GPU card and switching arrows. It contains no NVIDIA
logo and is covered by the project's MIT license.

The NVIDIA logo in `assets` was obtained from NVIDIA's official
[logo and brand guidelines](https://www.nvidia.com/en-us/about-nvidia/legal-info/logo-brand-usage/).
The transparent PNG variants render the mark with
theme-appropriate black or white lettering.
It identifies the supported hardware vendor. The logo is not covered by this
project's MIT license. NVIDIA's published guidelines require written
authorization for logo use; inclusion here does not grant trademark rights
or permission to redistribute it. Review the logo's use before public release.

The portable `tools/7zip/7zr.exe` comes from the official 7-Zip 26.04 release
by Igor Pavlov. Its console banner identifies the reduced executable as
public domain. `tools/7zip/SOURCE.txt` records the original download, source
links and SHA-256, and `tools/7zip/License.txt` preserves upstream general
7-Zip license information. The extractor is unmodified.

No NVIDIA GPU driver packages are distributed with this source project.

The application uses PyQt6, Qt, and PyQt6-sip, distributed under their applicable
licenses. The binary packages include the license texts supplied by the installed
upstream distributions in `licenses/`. The project's MIT license does not replace
those dependency licenses.
The standalone Windows EXE also bundles CPython. Its license and this project's
notices are included in the executable's `licenses` resources; the 7-Zip notices
are under `tools/7zip`. PyInstaller builds the executable using its bootloader
distribution exception.
