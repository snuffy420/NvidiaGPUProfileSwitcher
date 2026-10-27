Option Explicit
Dim shell, fso, folder, scriptPath, versions, version, command, result
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = folder & "\gpu_manager.py"
shell.CurrentDirectory = folder
command = "python"
result = shell.Run("cmd.exe /d /c " & command & " -c ""import sys; assert sys.version_info >= (3,10) and sys.maxsize > 2**32; import PyQt6.QtWidgets""", 0, True)
If result <> 0 Then
    command = ""
    versions = Array("3", "3.14", "3.13", "3.12", "3.11", "3.10")
    For Each version In versions
        result = shell.Run("cmd.exe /d /c py -" & version & " -c ""import sys; assert sys.version_info >= (3,10) and sys.maxsize > 2**32; import PyQt6.QtWidgets""", 0, True)
        If result = 0 Then
            command = "py -" & version
            Exit For
        End If
    Next
End If
If command = "" Then
    MsgBox "Could not find 64-bit Python 3.10 or newer with PyQt6 installed.", vbExclamation, "GPU Driver Profile Switcher"
    WScript.Quit 1
End If
result = shell.Run(command & " """ & scriptPath & """", 0, True)
If result <> 0 Then
    MsgBox "The application could not start. Run the CMD launcher to see the error details.", vbExclamation, "GPU Driver Profile Switcher"
End If
WScript.Quit result
