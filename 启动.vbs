Dim ws, dir, script
Set ws = CreateObject("WScript.Shell")
dir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
script = dir & "transcribe_gui.py"

' pyw -3.11：无控制台窗口、指定 Python 3.11（tkinterdnd2 装在此版本）
' pyw.exe 是 Windows 内置的 Python 无窗口启动器，在 C:\Windows 下
ws.Run "pyw -3.11 """ & script & """", 0, False
