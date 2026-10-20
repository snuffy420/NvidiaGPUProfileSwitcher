"""Device Manager / Configuration Manager problem codes, with offline help.

Sources: Microsoft Device Manager Error Messages, individual CM_PROB pages,
and Microsoft's win32metadata SDK shared/cfg.h. Codes absent from the current
Device Manager table are explicitly labeled legacy/internal SDK codes.
The descriptions are paraphrases, not Windows' localized display messages.
"""
from dataclasses import dataclass

SOURCE = "https://learn.microsoft.com/en-us/windows-hardware/drivers/install/device-manager-error-messages"
SDK_SOURCE = "https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/shared/cfg.h"


@dataclass(frozen=True)
class Problem:
    symbol: str
    title: str
    meaning: str
    next_step: str
    legacy: bool = False

    @property
    def url(self):
        if self.symbol in {"UNKNOWN", "NO_PROBLEM"}:
            return SOURCE
        return SDK_SOURCE if self.legacy else (
            "https://learn.microsoft.com/en-us/windows-hardware/drivers/install/"
            + self.symbol.lower().replace("_", "-"))


_REINSTALL = "Install or repair a driver package that supports this GPU and Windows version, then restart and refresh."
_RESTART = "Restart Windows, then refresh. If it persists, inspect the driver installation and related devices."
_FIRMWARE = "Check the motherboard/device documentation and firmware configuration; contact the manufacturer if needed."
PROBLEMS = {
    0: Problem("NO_PROBLEM", "No PnP error", "Windows reports no Configuration Manager problem for this device.",
               "For AI use, also confirm your framework detects the GPU and completes a small workload."),
    1: Problem("CM_PROB_NOT_CONFIGURED", "Configuration incomplete", "Windows has not finished configuring this device.", _REINSTALL),
    2: Problem("CM_PROB_DEVLOADER_FAILED", "Device loader failed", "The SDK identifies a device loader/service load failure.", _REINSTALL, True),
    3: Problem("CM_PROB_OUT_OF_MEMORY", "Driver or memory problem", "The driver may be damaged, or Windows lacks memory/resources to load it.", "Close unnecessary applications and restart. If it persists, repair the driver."),
    4: Problem("CM_PROB_ENTRY_IS_WRONG_TYPE", "Incorrect configuration entry type", "The SDK identifies an entry of the wrong type; this is not a current public Device Manager message.", _REINSTALL, True),
    5: Problem("CM_PROB_LACKED_ARBITRATOR", "Resource arbitrator unavailable", "The SDK identifies a missing resource arbitrator.", _RESTART, True),
    6: Problem("CM_PROB_BOOT_CONFIG_CONFLICT", "Boot configuration conflict", "Device resources conflict in the boot configuration.", _FIRMWARE, True),
    7: Problem("CM_PROB_FAILED_FILTER", "Filter driver failed", "The SDK identifies a filter-driver failure.", _REINSTALL, True),
    8: Problem("CM_PROB_DEVLOADER_NOT_FOUND", "Device loader unavailable", "The SDK identifies a missing device loader.", _REINSTALL, True),
    9: Problem("CM_PROB_INVALID_DATA", "Invalid device identification", "Windows received invalid identification/configuration data for the hardware.", "Check the hardware and firmware; ask the hardware vendor for support."),
    10: Problem("CM_PROB_FAILED_START", "Device could not start", "The device's driver stack could not start it.", _REINSTALL),
    11: Problem("CM_PROB_LIAR", "Inconsistent device capabilities", "Legacy/internal SDK problem: the device reported inconsistent capabilities.", _RESTART, True),
    12: Problem("CM_PROB_NORMAL_CONFLICT", "Insufficient free resources", "Required hardware resources are unavailable or conflict with another device.", "Review resource conflicts and PCIe configuration with the motherboard vendor."),
    13: Problem("CM_PROB_NOT_VERIFIED", "Configuration not verified", "Legacy/internal SDK configuration-verification problem.", _REINSTALL, True),
    14: Problem("CM_PROB_NEED_RESTART", "Restart required", "The current configuration needs a Windows restart to take effect.", "Restart Windows, then refresh before judging the driver switch."),
    15: Problem("CM_PROB_REENUMERATION", "Re-enumeration required", "The SDK identifies a device re-enumeration condition.", "Wait, scan for hardware changes, then restart if necessary.", True),
    16: Problem("CM_PROB_PARTIAL_LOG_CONF", "Resources only partly configured", "Windows cannot identify all resources the device requires.", _FIRMWARE),
    17: Problem("CM_PROB_UNKNOWN_RESOURCE", "Unknown resource type", "The SDK identifies a resource type it cannot recognize.", _FIRMWARE, True),
    18: Problem("CM_PROB_REINSTALL", "Driver reinstall required", "Windows requests that this device's driver be installed again.", _REINSTALL),
    19: Problem("CM_PROB_REGISTRY", "Invalid registry configuration", "Device configuration information is incomplete or damaged.", "Repair the driver installation. Review recent driver/filter changes rather than deleting registry entries blindly."),
    20: Problem("CM_PROB_VXDLDR", "Legacy VxD loader error", "This SDK code is for Windows 95 only, not modern Windows GPU operation.", "Use the current Windows problem code and vendor diagnostics.", True),
    21: Problem("CM_PROB_WILL_BE_REMOVED", "Removal in progress", "Windows is processing removal of the device.", "Wait and refresh. Restart if removal remains stuck."),
    22: Problem("CM_PROB_DISABLED", "Device disabled", "The device is disabled through Windows device controls.", "Use Enable selected GPU if you want it active. This differs from Code 32, which concerns its driver service."),
    23: Problem("CM_PROB_DEVLOADER_NOT_READY", "Device loader not ready", "The SDK identifies a device loader that is not ready.", _RESTART, True),
    24: Problem("CM_PROB_DEVICE_NOT_THERE", "Device unavailable or incompletely installed", "The device may be absent, malfunctioning, or missing required drivers.", "Confirm presence, scan for hardware changes, and check the driver and PCIe hardware if it persists."),
    25: Problem("CM_PROB_MOVED", "Device moved", "The SDK identifies a moved-device configuration condition.", "Scan for hardware changes, then restart if needed.", True),
    26: Problem("CM_PROB_TOO_EARLY", "Device initialization too early", "The SDK identifies a device initialization timing condition.", _RESTART, True),
    27: Problem("CM_PROB_NO_VALID_LOG_CONF", "No valid resource configuration", "The SDK identifies no valid logical resource configuration.", _FIRMWARE, True),
    28: Problem("CM_PROB_FAILED_INSTALL", "Driver not installed", "A required driver has not been installed successfully.", _REINSTALL),
    29: Problem("CM_PROB_HARDWARE_DISABLED", "Disabled by hardware or firmware", "Firmware did not provide the resources required to enable this device.", _FIRMWARE),
    30: Problem("CM_PROB_CANT_SHARE_IRQ", "Interrupt cannot be shared", "The SDK identifies an interrupt-sharing conflict.", _FIRMWARE, True),
    31: Problem("CM_PROB_FAILED_ADD", "Required driver could not load", "The driver stack could not add/configure this device correctly.", _REINSTALL),
    32: Problem("CM_PROB_DISABLED_SERVICE", "Driver service disabled", "The device's driver service has its startup configuration disabled. An alternate driver may be handling the device.",
                "Repair/reinstall the compatible NVIDIA driver and restart. If it persists, investigate the specific disabled driver service. Enabling the device alone may not fix it; this app does not edit service startup settings."),
    33: Problem("CM_PROB_TRANSLATION_FAILED", "Resource translation failed", "Windows could not determine the device's required resources.", _FIRMWARE),
    34: Problem("CM_PROB_NO_SOFTCONFIG", "Manual configuration required", "Windows cannot determine this device's configuration automatically.", "Consult the hardware documentation and Device Manager's Resources tab if applicable."),
    35: Problem("CM_PROB_BIOS_TABLE", "Firmware configuration information missing", "Firmware tables lack information needed to configure this device.", _FIRMWARE),
    36: Problem("CM_PROB_IRQ_TRANSLATION_FAILED", "Interrupt configuration mismatch", "Windows could not translate the device's IRQ/interrupt configuration.", _FIRMWARE),
    37: Problem("CM_PROB_FAILED_DRIVER_ENTRY", "Driver initialization failed", "The driver failed while initializing.", _REINSTALL),
    38: Problem("CM_PROB_DRIVER_FAILED_PRIOR_UNLOAD", "Previous driver remains loaded", "An earlier instance of the driver has not unloaded from memory.", _RESTART),
    39: Problem("CM_PROB_DRIVER_FAILED_LOAD", "Driver load failed", "Windows could not load the driver; required files may be missing or damaged.", _REINSTALL),
    40: Problem("CM_PROB_DRIVER_SERVICE_KEY_INVALID", "Driver service configuration invalid", "The driver's registry service configuration is inaccessible or incorrect.", _REINSTALL),
    41: Problem("CM_PROB_LEGACY_SERVICE_NO_DEVICES", "Driver loaded but device not found", "A driver loaded without finding its corresponding hardware.", "Scan for hardware changes and check presence, then repair the matching driver if needed."),
    42: Problem("CM_PROB_DUPLICATE_DEVICE", "Duplicate device detected", "Windows found a duplicate instance conflicting with this device.", _RESTART),
    43: Problem("CM_PROB_FAILED_POST_START", "Device reported a failure", "A driver reported a device problem and Windows stopped it.", "Try a compatible driver reinstall. If it persists, investigate GPU power, hardware, and firmware."),
    44: Problem("CM_PROB_HALTED", "Stopped by software", "An application or service stopped the device.", _RESTART),
    45: Problem("CM_PROB_PHANTOM", "Device not connected", "This is a stored device instance whose hardware is not currently connected/detected.", "No action is needed for an intentionally absent device. Otherwise check hardware detection."),
    46: Problem("CM_PROB_SYSTEM_SHUTDOWN", "Windows is shutting down", "The device is unavailable while the system shuts down.", "Check again after Windows starts."),
    47: Problem("CM_PROB_HELD_FOR_EJECT", "Prepared for safe removal", "The device is offline after being prepared for removal.", "Restart Windows for an internal GPU. For removable hardware, reconnect it when safe."),
    48: Problem("CM_PROB_DRIVER_BLOCKED", "Driver blocked", "Windows blocked a driver known to have compatibility problems.", "Install a compatible updated driver from the manufacturer."),
    49: Problem("CM_PROB_REGISTRY_TOO_LARGE", "System registry hive too large", "The system configuration hive cannot accommodate the device configuration.", "Review unused hidden devices with support assistance; do not delete active GPU entries blindly."),
    50: Problem("CM_PROB_SETPROPERTIES_FAILED", "Device properties could not be applied", "Windows failed to apply one or more device configuration properties.", _REINSTALL),
    51: Problem("CM_PROB_WAITING_ON_DEPENDENCY", "Waiting for another device", "A device or service dependency has not started.", "Inspect related devices for errors and resolve the failed dependency."),
    52: Problem("CM_PROB_UNSIGNED_DRIVER", "Driver signature could not be verified", "Windows cannot verify the required driver files' digital signatures.", "Reinstall an authentic signed NVIDIA driver. Do not bypass signature enforcement as a routine fix."),
    53: Problem("CM_PROB_USED_BY_DEBUGGER", "Reserved by kernel debugger", "The device is reserved for kernel debugging in this boot session.", "End the debugging configuration if it is no longer needed, then restart."),
    54: Problem("CM_PROB_DEVICE_RESET", "Device resetting", "Windows is resetting the device after a failure.", "Wait and refresh. If it persists, restart and investigate the driver or hardware."),
    55: Problem("CM_PROB_CONSOLE_LOCKED", "Blocked by console/DMA policy", "Windows is blocking device enumeration under a DMA-protection Group Policy or MDM policy, often while the console is locked.", "Sign in to/unlock the local session and reconnect removable hardware if applicable. If it persists, have your administrator review the DMA-protection policy."),
    56: Problem("CM_PROB_NEED_CLASS_CONFIG", "Class configuration incomplete", "The device requires additional class-level configuration before it can start.", "Let driver setup finish and restart if requested. Repair the driver if configuration remains incomplete."),
    57: Problem("CM_PROB_GUEST_ASSIGNMENT_FAILED", "Virtual machine assignment failed", "Windows failed to assign the device to a guest partition.", "Review virtual machine GPU assignment and host virtualization configuration."),
}


def problem_info(code: int | None) -> Problem:
    if code is None:
        return Problem("UNKNOWN", "Problem code unavailable", "Windows did not return a numeric problem code.", "Refresh the inventory or inspect Device Manager.")
    return PROBLEMS.get(code, Problem("UNKNOWN", "Unrecognized problem code",
                        f"Windows returned Code {code}, which is not in this reference.",
                        "Inspect Device Manager and consult Microsoft's latest documentation."))


def problem_details(code: int | None) -> str:
    info = problem_info(code)
    heading = f"Code {code}" if code is not None else "Code unknown"
    category = " [legacy/internal SDK code]" if info.legacy else ""
    return f"{heading}: {info.title}{category}\n{info.meaning}\nNext step: {info.next_step}"
