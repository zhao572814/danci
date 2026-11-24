"""生成 IPA 音标专用小字体（从 cambria 提取词库所需的音标字符）。

背景：simhei 中文正常但不含国际音标（ə ʊ θ ð…），音标显示成豆腐块 □。
方案：主力字体仍用 simhei 显示中文；把 cambria 里的 IPA 字符做成小字体
      注册为 IPA，查看模式用 Kivy 富文本 [font=IPA]音标[/font] 单独渲染。

开发期一次性运行（本机）：
    python tools/make_font.py
"""
import json
import os

from fontTools import subset
from fontTools.ttLib import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
ASSETS = os.path.join(APP, "assets")
CAMBRIA = r"C:\Windows\Fonts\cambria.ttc"
OUT = os.path.join(ASSETS, "fonts", "ipa.ttf")


def needed_chars() -> set[str]:
    chars = set()
    d = json.load(open(os.path.join(ASSETS, "builtin_dict.json"), encoding="utf-8"))
    for v in d.values():
        chars.update(v.get("phonetic", ""))
    # 音标分隔符 / [ ] . 也带上
    chars.update("/[]·' ")
    return {c for c in chars if ord(c) > 127}


def main():
    need = needed_chars()
    cam = TTFont(CAMBRIA, fontNumber=0)
    cmap = cam.getBestCmap()
    have = {c for c in need if ord(c) in cmap}
    miss = need - have
    print(f"音标字符 {len(need)}，cambria 覆盖 {len(have)}，缺 {len(miss)}: {''.join(sorted(miss))}")

    opts = subset.Options()
    opts.name_IDs = ["*"]
    opts.name_legacy = True
    opts.notdef_outline = True
    opts.recalc_bounds = True
    sub = subset.Subsetter(options=opts)
    sub.populate(text="".join(have) + "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    sub.subset(cam)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    cam.save(OUT)
    print(f"生成 {OUT} ({os.path.getsize(OUT)//1024} KB)")

    # 复验
    chk = TTFont(OUT)
    chk_cmap = chk.getBestCmap()
    still = [c for c in need if ord(c) not in chk_cmap]
    print(f"复验：音标字符缺失 {len(still)}: {''.join(still)}")


if __name__ == "__main__":
    main()
