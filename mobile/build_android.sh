#!/bin/bash
# 词库本 APK 构建脚本（在 kivy/buildozer 容器内以 root 运行）
#
# 背景：新版 python-for-android 用实验性的 prerequisites.py 准备 Android SDK，
# 它只下载命令行工具、不安装对应 API 的 platform，导致：
#   [INFO] Available Android APIs are ()
#   [ERROR] Requested API target N is not available
# 因此这里在编译前用 sdkmanager 显式补装 platform，再正式编译。

set +e
cd /home/user/hostcwd || exit 1

export PATH="/home/user/.venv/bin:$PATH"
BUILDOZER="/home/user/.venv/bin/buildozer"
SDK="/home/user/.buildozer/android/platform/android-sdk"
API=34

echo "########## [1/3] 首次运行 buildozer：准备 SDK / NDK ##########"
# 第一次会因缺 platform 失败，但会把 SDK 命令行工具和 NDK 下载好（供下一步使用）
printf 'y\n' | "$BUILDOZER" android debug > /tmp/prep.log 2>&1
echo "准备阶段退出码=$?（非 0 属正常，目的是把工具下载齐）"
grep -iE "error|missing|download|available" /tmp/prep.log | tail -10

echo "########## [2/3] 显式安装 Android platform $API ##########"
SDKMGR="$(find "$SDK" -type f -name sdkmanager 2>/dev/null | head -n1)"
echo "sdkmanager=$SDKMGR"
if [ -n "$SDKMGR" ]; then
  echo "接受 SDK licenses..."
  yes | "$SDKMGR" --sdk_root="$SDK" --licenses >/dev/null 2>&1
  echo "安装 platforms;android-$API 与 build-tools..."
  yes | "$SDKMGR" --sdk_root="$SDK" "platforms;android-$API" "build-tools;${API}.0.0" > /tmp/sdk.log 2>&1
  echo "安装退出码=$?"
  tail -12 /tmp/sdk.log
else
  echo "!! 未找到 sdkmanager"
fi

# 兜底：若老版 sdkmanager 装不上新 API，用新版 commandlinetools 再装一次
if [ ! -d "$SDK/platforms/android-$API" ]; then
  echo "老工具未装上 platform，尝试新版 commandlinetools..."
  cd /tmp || exit 1
  python3 -c "import urllib.request; urllib.request.urlretrieve('https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip','/tmp/cmdtools.zip')" 2>/dev/null
  if [ -f /tmp/cmdtools.zip ]; then
    rm -rf /tmp/cmdtools && mkdir -p /tmp/cmdtools
    unzip -q -o /tmp/cmdtools.zip -d /tmp/cmdtools
    NEWMGR="$(find /tmp/cmdtools -type f -name sdkmanager 2>/dev/null | head -n1)"
    echo "new sdkmanager=$NEWMGR"
    if [ -n "$NEWMGR" ]; then
      yes | "$NEWMGR" --sdk_root="$SDK" --licenses >/dev/null 2>&1
      yes | "$NEWMGR" --sdk_root="$SDK" "platforms;android-$API" "build-tools;${API}.0.0" > /tmp/sdk2.log 2>&1
      echo "新版安装退出码=$?"
      tail -12 /tmp/sdk2.log
    fi
  else
    echo "!! 新版命令行工具下载失败"
  fi
  cd /home/user/hostcwd || exit 1
fi

echo "当前已安装平台："
ls -1 "$SDK/platforms" 2>/dev/null || echo "  (仍为空)"

echo "########## [3/3] 正式编译 APK ##########"
printf 'y\n' | "$BUILDOZER" android debug
exit $?
