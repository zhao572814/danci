@echo off
chcp 936 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title 词库本 - 一键上传源码

echo.
echo ============================================================
echo   词库本 - 一键上传源码
echo   上传后 GitHub 会自动免费编译出安卓 APK
echo ============================================================
echo.

REM ---------- 1. 查找 git（系统PATH → 常见安装位置 → WorkBuddy内置） ----------
where git >nul 2>nul
if not errorlevel 1 goto git_ok

if exist "%ProgramFiles%\Git\cmd\git.exe" (
    set "PATH=%ProgramFiles%\Git\cmd;%PATH%"
    goto git_ok
)
if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" (
    set "PATH=%LOCALAPPDATA%\Programs\Git\cmd;%PATH%"
    goto git_ok
)
REM 最后找 WorkBuddy 自带的便携版 git（本机就是这个）
if exist "%ProgramFiles%\WorkBuddy\resources\vendor\PortableGit\cmd\git.exe" (
    set "PATH=%ProgramFiles%\WorkBuddy\resources\vendor\PortableGit\cmd;%PATH%"
    set "PATH=%ProgramFiles%\WorkBuddy\resources\vendor\PortableGit\mingw64\bin;%PATH%"
    goto git_ok
)
echo [错误] 没有找到 git。请先安装 Git for Windows：
echo        https://git-scm.com/download/win
echo       安装时一路下一步即可，装完重新双击本文件。
echo.
pause
exit /b 1

:git_ok
echo       已找到 git：
git --version

REM ---------- 2. 初始化仓库 ----------
if not exist ".git" (
    echo [1/5] 首次运行，正在初始化本地仓库...
    git init -b main
) else (
    echo [1/5] 本地仓库已存在，跳过初始化。
)

REM 配置提交身份（仅本仓库级别，不影响你的其它项目）
git config user.name "VocabUser"
git config user.email "vocab@local.device"

REM ---------- 3. 设置远程仓库 ----------
set "REPO_URL=https://github.com/zhao572814/danci.git"
git remote get-url origin >nul 2>nul
if errorlevel 1 (
    echo [2/5] 正在关联远程仓库...
    git remote add origin "%REPO_URL%"
) else (
    echo [2/5] 远程仓库已关联。
)

REM ---------- 4. 提交代码 ----------
REM 清理上一轮失败留下的上传日志：取消追踪并删除，避免误传到公开仓库
git rm --cached "upload_result.log" >nul 2>nul
del "upload_result.log" >nul 2>nul
echo [3/5] 正在保存改动...
git add -A
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "Update mobile source"
    if errorlevel 1 (
        echo.
        echo [错误] 提交失败，可能是没配置 git 用户名/邮箱。
        echo 请在命令行先运行下面两条命令：
        echo   git config --global user.name "YourName"
        echo   git config --global user.email "you@example.com"
        echo.
        pause
        exit /b 1
    )
) else (
    echo       没有新的改动，无需提交。
)

REM ---------- 5. 推送（带重试，网络不好时多试几次） ----------
git branch -M main
echo [4/5] 正在上传到 GitHub（本次含 1.7 万个发音约 160MB，上传明显变慢，请耐心等，会自动重试 3 次）...
set "PUSH_OK=0"
for /L %%i in (1,1,3) do (
    if "!PUSH_OK!"=="0" (
        echo       第 %%i 次尝试...
        git -c http.version=HTTP/1.1 push -u origin main > "upload_result.log" 2>&1
        if not errorlevel 1 set "PUSH_OK=1"
        if "!PUSH_OK!"=="0" timeout /t 3 /nobreak >nul
    )
)

if "!PUSH_OK!"=="1" (
    del "upload_result.log" >nul 2>nul
    echo [5/5] 上传成功！
    echo.
    echo ============================================================
    echo   下一步：等 GitHub 自动编译 APK（首次约 15-25 分钟）
    echo   1. 打开：https://github.com/zhao572814/danci/actions
    echo   2. 点进最上面那次任务，等它变成绿色对勾
    echo   3. 页面往下拉，找到 Artifacts，下载「词库本-APK」
    echo   4. 解压得到 .apk，传到手机安装
    echo   详细中文步骤见本目录的「手机APK-使用说明.md」
    echo ============================================================
    echo.
    start "" "https://github.com/zhao572814/danci/actions"
) else (
    echo.
    echo [上传失败] 常见原因：
    echo   1. 网络不通 / GitHub 时好时坏 —— 稍等再双击本文件重试
    echo   2. 没登录 GitHub —— 首次推送会弹登录窗，用浏览器完成登录
    echo   3. 仓库不属于该账号 —— 确认仓库地址正确
    echo.
    echo   详细报错已保存到 upload_result.log，可发给我看。
    echo   ------------- 报错内容 -------------
    type "upload_result.log" 2>nul
    echo   ------------------------------------
)

echo.
pause
