"""六爻固定表；爻位按初爻到上爻，最低位表示初爻。来源见 references/sources.md。"""

STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
BRANCH_ELEMENTS = dict(zip(BRANCHES, "水土木木土火火土金金土水"))
PRODUCES = dict(zip("木火土金水", "火土金水木"))
CONTROLS = dict(zip("木土水火金", "土水火金木"))
KIN = ("父母", "兄弟", "妻财", "官鬼", "子孙")
SPIRITS = ("青龙", "朱雀", "勾陈", "螣蛇", "白虎", "玄武")
SPIRIT_START = dict(zip(STEMS, (0, 0, 1, 1, 2, 3, 4, 4, 5, 5)))
COMBINES = {frozenset(pair) for pair in ("子丑", "寅亥", "卯戌", "辰酉", "巳申", "午未")}
CLASHES = {frozenset(pair) for pair in ("子午", "丑未", "寅申", "卯酉", "辰戌", "巳亥")}
TRIGRAMS = {
    "乾": {"bits": 7, "element": "金", "image": "天", "inner": "甲子 甲寅 甲辰", "outer": "壬午 壬申 壬戌"},
    "兑": {"bits": 3, "element": "金", "image": "泽", "inner": "丁巳 丁卯 丁丑", "outer": "丁亥 丁酉 丁未"},
    "离": {"bits": 5, "element": "火", "image": "火", "inner": "己卯 己丑 己亥", "outer": "己酉 己未 己巳"},
    "震": {"bits": 1, "element": "木", "image": "雷", "inner": "庚子 庚寅 庚辰", "outer": "庚午 庚申 庚戌"},
    "巽": {"bits": 6, "element": "木", "image": "风", "inner": "辛丑 辛亥 辛酉", "outer": "辛未 辛巳 辛卯"},
    "坎": {"bits": 2, "element": "水", "image": "水", "inner": "戊寅 戊辰 戊午", "outer": "戊申 戊戌 戊子"},
    "艮": {"bits": 4, "element": "土", "image": "山", "inner": "丙辰 丙午 丙申", "outer": "丙戌 丙子 丙寅"},
    "坤": {"bits": 0, "element": "土", "image": "地", "inner": "乙未 乙巳 乙卯", "outer": "癸丑 癸亥 癸酉"},
}
TRIGRAM_BY_BITS = {v["bits"]: k for k, v in TRIGRAMS.items()}
PALACE_NAMES = {
    "乾": "乾为天 天风姤 天山遁 天地否 风地观 山地剥 火地晋 火天大有".split(),
    "兑": "兑为泽 泽水困 泽地萃 泽山咸 水山蹇 地山谦 雷山小过 雷泽归妹".split(),
    "离": "离为火 火山旅 火风鼎 火水未济 山水蒙 风水涣 天水讼 天火同人".split(),
    "震": "震为雷 雷地豫 雷水解 雷风恒 地风升 水风井 泽风大过 泽雷随".split(),
    "巽": "巽为风 风天小畜 风火家人 风雷益 天雷无妄 火雷噬嗑 山雷颐 山风蛊".split(),
    "坎": "坎为水 水泽节 水雷屯 水火既济 泽火革 雷火丰 地火明夷 地水师".split(),
    "艮": "艮为山 山火贲 山天大畜 山泽损 火泽睽 天泽履 风泽中孚 风山渐".split(),
    "坤": "坤为地 地雷复 地泽临 地天泰 雷天大壮 泽天夬 水天需 水地比".split(),
}
PALACE_MASKS = (0, 1, 3, 7, 15, 31, 23, 16)
PALACE_STAGES = ("本宫", "一世", "二世", "三世", "四世", "五世", "游魂", "归魂")
SELF_POSITIONS = (6, 1, 2, 3, 4, 5, 4, 3)
HEXAGRAMS = {}
for palace, names in PALACE_NAMES.items():
    pure = TRIGRAMS[palace]["bits"] * 9
    for stage, name in enumerate(names):
        bits = pure ^ PALACE_MASKS[stage]
        self_position = SELF_POSITIONS[stage]
        HEXAGRAMS[bits] = {
            "name": name, "palace": palace, "palace_element": TRIGRAMS[palace]["element"],
            "stage": PALACE_STAGES[stage], "self_position": self_position,
            "other_position": (self_position + 2) % 6 + 1,
            "lower": TRIGRAM_BY_BITS[bits & 7], "upper": TRIGRAM_BY_BITS[bits >> 3],
            "bits_bottom_up": [(bits >> i) & 1 for i in range(6)],
        }


def kin_for(reference_element, line_element):
    if reference_element == line_element:
        return "兄弟"
    if PRODUCES[line_element] == reference_element:
        return "父母"
    if PRODUCES[reference_element] == line_element:
        return "子孙"
    if CONTROLS[reference_element] == line_element:
        return "妻财"
    return "官鬼"


def najia(bits):
    lower = TRIGRAMS[TRIGRAM_BY_BITS[bits & 7]]["inner"]
    upper = TRIGRAMS[TRIGRAM_BY_BITS[bits >> 3]]["outer"]
    return (lower + " " + upper).split()


def relation(source_branch, target_branch):
    """方向为 source -> target；生克与冲合同时记录，尚未判定其有效性。"""
    a, b = BRANCH_ELEMENTS[source_branch], BRANCH_ELEMENTS[target_branch]
    if a == b:
        element_relation = "比和"
    elif PRODUCES[a] == b:
        element_relation = "生"
    elif CONTROLS[a] == b:
        element_relation = "克"
    elif PRODUCES[b] == a:
        element_relation = "被生"
    else:
        element_relation = "被克"
    pair = frozenset((source_branch, target_branch))
    return {"source_branch": source_branch, "target_branch": target_branch,
            "element_relation": element_relation,
            "same_branch": source_branch == target_branch,
            "combines": pair in COMBINES, "clashes": pair in CLASHES}
