"""常量与数据结构定义。"""

from dataclasses import dataclass
from typing import Optional

# 三种词库（membership 多对多，一个单词可同时属于多个库）
LIST_MASTERED = "mastered"   # 已背诵
LIST_HARD = "hard"           # 重点 / 易错
LIST_NAMES = {LIST_MASTERED: "已背诵词库", LIST_HARD: "重点易错词库"}

# 背诵视图范围
SCOPE_INBOX = "inbox"        # 未背诵（去掉已背过的）
SCOPE_MASTERED = "mastered"
SCOPE_HARD = "hard"
SCOPE_ALL = "all"

# 背诵显示模式：逐层叠加信息
MODE_PLAIN = 0        # 只有单词
MODE_PHONETIC = 1     # 单词 + 音标
MODE_TRANSLATION = 2  # 单词 + 音标 + 翻译
MODE_EXAMPLE = 3      # 单词 + 音标 + 翻译 + 例句
MODE_LABELS = {
    MODE_PLAIN: "纯单词",
    MODE_PHONETIC: "带音标",
    MODE_TRANSLATION: "音标 + 翻译",
    MODE_EXAMPLE: "音标 + 翻译 + 例句",
}

# 新的背诵模式（闪卡测验）
RECITE_SOUND = "sound"       # 读音背诵：听读音 → 输入单词
RECITE_CHINESE = "chinese"   # 中文背诵：看中文 → 输入单词
RECITE_VIEW = "view"         # 查看模式：显示全部内容
RECITE_MODE_LABELS = {
    RECITE_SOUND: "读音背诵",
    RECITE_CHINESE: "看中文背诵",
    RECITE_VIEW: "查看模式",
}


@dataclass
class WordRow:
    """一条单词记录（UI 层只读这个结构，方便换成 Kivy）。"""

    id: int
    word: str
    phonetic: str = ""
    translation: str = ""
    example: str = ""
    info_source: str = ""
    familiarity: int = 0
    mastered_on: Optional[str] = None
    hard_on: Optional[str] = None
    batch_names: str = ""

    @property
    def familiarity_label(self) -> str:
        if self.familiarity >= 4:
            return "熟练"
        if self.familiarity >= 2:
            return "一般"
        if self.familiarity > 0:
            return "有点印象"
        return "陌生"

    @classmethod
    def from_dict(cls, d: dict) -> "WordRow":
        keys = cls.__dataclass_fields__.keys()
        return cls(**{k: d.get(k) for k in keys})
