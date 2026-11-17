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

# ------------------------------------------------------------------
# 修复 p4a 目录的 git 元数据（本轮反复失败的真因）：
# buildozer 在 p4a 目录存在时会执行
#   git config --get remote.origin.url   和   git branch -vv
# 比对 url/branch。缓存恢复的 p4a 目录可能「有 .git 但没配 origin」，
# 或整个目录残缺 → git 命令返回码 1 → buildozer 无容错直接中断。
# 对策：
#   - 源码完整（有 pythonforandroid/ 包）→ 就地修好 git 元数据（最快，保留 SDK 缓存）
#   - 源码残缺 → 删除，让 buildozer 重新 git clone（默认 master 分支）
# ------------------------------------------------------------------
P4A_DIR="/home/user/hostcwd/.buildozer/android/platform/python-for-android"
if [ -d "$P4A_DIR" ] && [ -d "$P4A_DIR/pythonforandroid" ]; then
  echo "########## 修复 p4a 目录 git 元数据 ##########"
  cd "$P4A_DIR" || exit 1
  git rev-parse --git-dir >/dev/null 2>&1 || git init -q
  git config user.name "BuildBot"
  git config user.email "build@local"
  git remote remove origin 2>/dev/null
  git remote add origin https://github.com/kivy/python-for-android.git
  # git branch -vv 需要至少一个 commit，否则取分支名会失败
  if ! git rev-parse HEAD >/dev/null 2>&1; then
    git add -A >/dev/null 2>&1
    git commit -qm "local p4a snapshot" >/dev/null 2>&1
  fi
  git branch -M master >/dev/null 2>&1
  echo "p4a git 就绪: url=$(git config --get remote.origin.url) branch=$(git branch --show-current)"
  cd /home/user/hostcwd || exit 1
elif [ -d "$P4A_DIR" ]; then
  echo "########## p4a 源码残缺，删除以便重新完整 clone ##########"
  rm -rf "$P4A_DIR"
else
  echo "p4a 目录不存在，buildozer 会自动 clone"
fi

echo "########## [3/3] 正式编译 APK ##########"
printf 'y\n' | "$BUILDOZER" android debug
exit $?
