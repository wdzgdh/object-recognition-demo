@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0object_recognition"

set "PY="

rem 1) py launcher（官方安装器默认带）
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=py -3"

rem 2) PATH 里的 python
if not defined PY (
  python -c "import sys" >nul 2>nul
  if not errorlevel 1 set "PY=python"
)

rem 3) 常见 conda / 自定义安装路径
if not defined PY (
  for %%P in (
    "%USERPROFILE%\anaconda3\python.exe"
    "%USERPROFILE%\miniconda3\python.exe"
    "%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe"
    "%USERPROFILE%\AppData\Local\Programs\Python\Python311\python.exe"
    "C:\ProgramData\anaconda3\python.exe"
    "D:\anacoda\python.exe"
    "D:\anaconda3\python.exe"
    "E:\Python\python.exe"
  ) do (
    if not defined PY if exist %%P set "PY=%%P"
  )
)

if not defined PY (
  echo [错误] 没有找到可用的 Python 解释器。
  echo.
  echo   请任选其一：
  echo     1. 安装 Python 3.8+，安装时勾选 "Add Python to PATH"
  echo     2. 用编辑器打开本文件，把下面这行改成你自己的 python.exe 完整路径
  echo        set "PY=C:\你的\python.exe"
  echo.
  pause
  exit /b 1
)

echo 使用解释器：!PY!
!PY! -c "import sys; print('Python', sys.version.split()[0])"

echo 正在启动物体识别 Demo…
!PY! main.py
if errorlevel 1 (
  echo.
  echo [提示] 程序异常退出。常见原因：
  echo   1. 缺依赖 —— 在上面的解释器下执行：pip install -r requirements.txt
  echo   2. 首次运行需要联网下载 ResNet18 权重（约 45 MB）
  echo   3. 语音不可用 —— 检查麦克风权限或改用键盘输入
  echo.
  pause
)

endlocal
