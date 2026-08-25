' Hidden launcher for the one-shot BF6 pipeline watchdog.
'
' Wait=True is deliberate: Task Scheduler records the watchdog's real exit
' status and MultipleInstances=IgnoreNew prevents overlapping observations.
command = "cmd /c ""C:\work\commandcenter\hearth\etc\watchdog-bf6-pipeline.cmd"""
code = CreateObject("WScript.Shell").Run(command, 0, True)
WScript.Quit code
