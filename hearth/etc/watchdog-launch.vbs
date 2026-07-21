' HearthGatewayWatchdog hidden launcher (ADR-0024).
'
' The watchdog task runs every 3 minutes. Registered in the interactive user
' session (which needs no elevation), a plain `cmd /c watchdog-gateway.cmd`
' action flashes a console window on the desktop every tick. This wrapper runs
' the same tick with window style 0 (hidden) so nothing appears.
'
' This is the no-elevation fix. The cleaner posture -- run in session 0 with no
' desktop at all, and keep watching across logoff -- is to convert the task to
' S4U (LogonType "run whether user is logged on or not"), which matches
' HearthGatewayBoot/Restart but requires an elevated one-time registration.
' See ADR-0024 and the DECISIONS-PENDING follow-up.
CreateObject("WScript.Shell").Run "cmd /c ""C:\work\commandcenter\hearth\etc\watchdog-gateway.cmd""", 0, False
