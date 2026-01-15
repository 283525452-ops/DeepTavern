Option Explicit

Dim WshShell, fso, currentDir

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' 获取脚本当前所在目录
currentDir = fso.GetParentFolderName(WScript.ScriptFullName)

' 设置当前目录为脚本所在目录，确保后续相对路径正确
WshShell.CurrentDirectory = currentDir

' 使用相对路径拼接 Redis 路径
Dim redisPath
redisPath = fso.BuildPath(currentDir, "Redis\redis-server.exe")

Const REDIS_PORT = 6379

Function IsScriptRunning(scriptName)
    Dim objWMI, colProcesses, objProcess
    Set objWMI = GetObject("winmgmts:\\.\root\cimv2")
    Set colProcesses = objWMI.ExecQuery("SELECT * FROM Win32_Process WHERE Name='python.exe' OR Name='pythonw.exe'")
    
    For Each objProcess In colProcesses
        If Not IsNull(objProcess.CommandLine) Then
            If InStr(LCase(objProcess.CommandLine), LCase(scriptName)) > 0 Then
                IsScriptRunning = True
                Exit Function
            End If
        End If
    Next
    IsScriptRunning = False
End Function

Function IsProcessRunning(processName)
    Dim objWMI, colProcesses
    Set objWMI = GetObject("winmgmts:\\.\root\cimv2")
    Set colProcesses = objWMI.ExecQuery("SELECT * FROM Win32_Process WHERE Name='" & processName & "'")
    IsProcessRunning = (colProcesses.Count > 0)
End Function

Function IsPortInUse(port)
    Dim objExec, strOutput
    On Error Resume Next
    Set objExec = WshShell.Exec("cmd /c netstat -an | findstr :" & port & " | findstr LISTENING")
    strOutput = objExec.StdOut.ReadAll
    IsPortInUse = (Len(Trim(strOutput)) > 0)
    On Error GoTo 0
End Function

If Not fso.FileExists("run_backend.bat") Then
    MsgBox "run_backend.bat not found in " & currentDir, vbCritical, "Error"
    WScript.Quit 1
End If

If Not fso.FileExists("venv\Scripts\pythonw.exe") Then
    MsgBox "venv\Scripts\pythonw.exe not found", vbCritical, "Error"
    WScript.Quit 1
End If

If Not fso.FileExists("monitor.py") Then
    MsgBox "monitor.py not found", vbCritical, "Error"
    WScript.Quit 1
End If

' 检查 Redis
If IsProcessRunning("redis-server.exe") Then
    ' skip
ElseIf IsPortInUse(REDIS_PORT) Then
    ' skip
Else
    If fso.FileExists(redisPath) Then
        WshShell.Run """" & redisPath & """", 0, False
        WScript.Sleep 1500
    Else
        ' 如果找不到 Redis，可以选择报错或者静默跳过（取决于是否强制依赖 Redis）
        ' MsgBox "Redis server not found at: " & redisPath, vbExclamation, "Warning"
    End If
End If

' 启动后端
If IsScriptRunning("main.py") Then
    ' skip
Else
    WshShell.Run "run_backend.bat", 0, False
    WScript.Sleep 2000
End If

' 启动前端监控
If IsScriptRunning("monitor.py") Then
    ' skip
Else
    WshShell.Run "venv\Scripts\pythonw.exe monitor.py", 0, False
End If

Set fso = Nothing
Set WshShell = Nothing
