#!/usr/bin/env python3
"""离线六爻排盘。只输出计算事实，不用评分或关键词代替断卦。"""

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.dont_write_bytecode = True
SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "vendor"))
from tables import (STEMS, BRANCHES, BRANCH_ELEMENTS, PRODUCES, CONTROLS, KIN,
                    SPIRITS, SPIRIT_START, HEXAGRAMS, TRIGRAMS, najia, kin_for, relation)


class InputError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InputError(message)


def check_keys(obj, allowed, label):
    require(isinstance(obj, dict), f"{label} 必须是对象。")
    extra = set(obj) - set(allowed)
    require(not extra, f"{label} 含未支持字段：{', '.join(sorted(extra))}。")


def sexagenary_index(day):
    require(isinstance(day, str) and len(day) == 2 and day[0] in STEMS and day[1] in BRANCHES,
            "日干支必须是两个字，例如甲午。")
    for i in range(60):
        if STEMS[i % 10] + BRANCHES[i % 12] == day:
            return i
    raise InputError(f"{day} 不是六十甲子中的有效干支。")


def void_branches(day):
    index = sexagenary_index(day)
    first_branch_index = (index // 10 * 10) % 12
    return [BRANCHES[(first_branch_index + i) % 12] for i in (10, 11)]


def normalize_tosses(data):
    require("tosses" in data, "请提供六次投币结果。")
    tosses = data["tosses"]
    require(isinstance(tosses, list) and len(tosses) == 6, "必须恰好提供六次结果，不能补掷或删掉多余值。")
    require(all(type(x) is int for x in tosses), "每次结果必须为整数；不接受布尔值、文字或小数。")
    mode = data.get("input_mode")
    require(mode in ("back_count", "front_count", "line_values"),
            "input_mode 必须明确为 back_count、front_count 或 line_values。")
    order = data.get("order", "first_to_sixth")
    require(order in ("first_to_sixth", "top_to_bottom"), "order 必须是 first_to_sixth 或 top_to_bottom。")
    require(all((6 <= x <= 9) if mode == "line_values" else (0 <= x <= 3) for x in tosses),
            "背面数/正面数只能为 0—3；爻值只能为 6—9。")
    values = list(tosses)
    if order == "top_to_bottom":
        values.reverse()
    if mode == "back_count":
        values = [6 + x for x in values]
    elif mode == "front_count":
        values = [9 - x for x in values]
    return values


def make_calendar(spec):
    if spec is None:
        spec = {"kind": "unknown"}
    check_keys(spec, ("kind", "at", "timezone", "day_boundary", "month_branch", "day_ganzhi", "source"), "time")
    kind = spec.get("kind", "unknown")
    require(kind in ("unknown", "manual", "civil", "now"), "time.kind 必须为 unknown、manual、civil 或 now。")
    fields = {
        "unknown": {"kind"},
        "manual": {"kind", "month_branch", "day_ganzhi", "source"},
        "civil": {"kind", "at", "timezone", "day_boundary"},
        "now": {"kind", "timezone", "day_boundary"},
    }
    require(not (set(spec) - fields[kind]), f"time.kind={kind} 与所给时间字段不匹配。")
    if kind == "unknown":
        return {"kind": kind, "status": "missing", "month_branch": None, "day_ganzhi": None,
                "void": [], "notes": ["起卦时间未提供：六神、旬空、日月关系暂缺；不按查询时间代填。"]}
    if kind == "manual":
        month = spec.get("month_branch")
        day = spec.get("day_ganzhi")
        source = spec.get("source")
        require(isinstance(month, str) and len(month) == 1 and month in BRANCHES, "请提供有效月支。")
        sexagenary_index(day)
        require(isinstance(source, str) and bool(source.strip()), "手动干支需注明来源，例如用户提供的排盘。")
        return {"kind": kind, "status": "user_supplied", "month_branch": month,
                "day_ganzhi": day, "void": void_branches(day), "source": source,
                "notes": ["日月干支按所注明来源录入，未反推公历日期。"]}
    zone_name = spec.get("timezone", "Asia/Shanghai")
    require(isinstance(zone_name, str), "timezone 必须为 IANA 时区名称。")
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise InputError("无法识别时区，请使用 Asia/Shanghai 等 IANA 名称并确保系统有时区数据。") from e
    boundary = spec.get("day_boundary", "midnight")
    require(boundary in ("midnight", "zi23"), "day_boundary 仅支持 midnight 或 zi23。")
    if kind == "now":
        local = datetime.now(zone).replace(microsecond=0)
    else:
        raw = spec.get("at")
        require(isinstance(raw, str) and re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", raw),
                "请提供含时分的公历时间，例如 2026-09-13T09:30:00；仅有日期时不能虚构时刻。")
        try:
            local = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as e:
            raise InputError("公历时间格式或日期无效。") from e
        if local.tzinfo is None:
            first = local.replace(tzinfo=zone, fold=0)
            second = local.replace(tzinfo=zone, fold=1)
            require(first.utcoffset() == second.utcoffset(), "该当地时间处在夏令时重复或跳过区间，请补充 UTC 偏移。")
            require(first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == local,
                    "该当地时间不存在，请核对夏令时。")
            local = first
        else:
            local = local.astimezone(zone)
    require(1900 <= local.year <= 2100, "第一版公历计算范围为 1900—2100 年；其他年代可录入已核验的月支、日干支。")
    try:
        from lunar_python import Solar
    except ImportError as e:
        raise InputError("缺少随 skill 附带的 lunar_python 1.4.8，请恢复 vendor；不要凭记忆填干支。") from e

    def lunar_at(dt):
        return Solar.fromYmdHms(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second).getLunar()

    local_lunar = lunar_at(local)
    # 库的节气时刻使用 UTC+8，须先转换真实时刻再比较，不能直接送入海外墙上时间。
    beijing = local.astimezone(timezone(timedelta(hours=8)))
    terms_lunar = lunar_at(beijing)
    day = local_lunar.getDayInGanZhiExact2() if boundary == "midnight" else local_lunar.getDayInGanZhiExact()
    # 本 skill 约定时干与所选换日后的日干一致；不混用库的另一套晚子时日干。
    hour_branch_index = ((local.hour + 1) // 2) % 12
    hour = STEMS[(STEMS.index(day[0]) % 5 * 2 + hour_branch_index) % 10] + BRANCHES[hour_branch_index]
    notes = ["按立春交接时刻换年、节令交接时刻换月；日柱按所选当地换日规则；未作真太阳时校正。"]
    if "timezone" not in spec:
        notes.append("未另指定时区，采用 Asia/Shanghai。")
    if kind == "now":
        notes.append("本次使用执行时刻；仅适用于用户明确表示刚刚投掷或要求按现在起卦的情形。")
    return {"kind": kind, "status": "calculated", "at": local.isoformat(timespec="seconds"),
            "timezone": zone_name, "day_boundary": boundary,
            "year_ganzhi": terms_lunar.getYearInGanZhiExact(),
            "month_ganzhi": terms_lunar.getMonthInGanZhiExact(),
            "month_branch": terms_lunar.getMonthZhiExact(), "day_ganzhi": day,
            "hour_ganzhi": hour, "void": void_branches(day), "engine": "lunar_python 1.4.8", "notes": notes}


def line_record(ganzhi, yin_yang, reference_element, calendar):
    branch = ganzhi[1]
    result = {"ganzhi": ganzhi, "branch": branch, "element": BRANCH_ELEMENTS[branch],
              "kin": kin_for(reference_element, BRANCH_ELEMENTS[branch]), "yang": bool(yin_yang)}
    flags = []
    month = calendar.get("month_branch")
    day = calendar.get("day_ganzhi")
    if month:
        result["month_relation"] = relation(month, branch)
        if month == branch:
            flags.append("临月建")
        if result["month_relation"]["clashes"]:
            flags.append("月冲（月破标记，是否有用另判）")
    if day:
        result["day_relation"] = relation(day[1], branch)
        if branch == day[1]:
            flags.append("临日辰")
        if result["day_relation"]["clashes"]:
            flags.append("日冲（暗动或日破须另判）")
        if branch in calendar["void"]:
            flags.append("旬空（是否有用另判）")
    result["flags"] = flags
    return result


def build_chart(data):
    check_keys(data, ("question", "context", "tosses", "input_mode", "order", "time", "focus"), "输入")
    require(isinstance(data.get("question"), str) and bool(data["question"].strip()), "请提供所问问题。")
    require("context" not in data or isinstance(data["context"], str), "context 必须为文字。")
    focus = data.get("focus")
    require(focus is None or (isinstance(focus, str) and focus in KIN + ("世爻", "应爻")), "focus 只能为五类六亲、世爻或应爻。")
    values = normalize_tosses(data)
    calendar = make_calendar(data.get("time"))
    bits = sum((x % 2) << i for i, x in enumerate(values))
    moving = [i + 1 for i, x in enumerate(values) if x in (6, 9)]
    changed_bits = bits ^ sum(1 << (i - 1) for i in moving)
    base = copy.deepcopy(HEXAGRAMS[bits])
    reference = base["palace_element"]
    base_najia, changed_najia = najia(bits), najia(changed_bits)
    rows = []
    for i, (value, ganzhi) in enumerate(zip(values, base_najia)):
        row = line_record(ganzhi, value % 2, reference, calendar)
        row.update({"position": i + 1, "value": value, "state": {6: "老阴", 7: "少阳", 8: "少阴", 9: "老阳"}[value],
                    "moving": i + 1 in moving, "self": i + 1 == base["self_position"],
                    "other": i + 1 == base["other_position"], "spirit": None, "hidden": [], "change": None})
        if calendar.get("day_ganzhi"):
            row["spirit"] = SPIRITS[(SPIRIT_START[calendar["day_ganzhi"][0]] + i) % 6]
        if row["moving"]:
            row["change"] = line_record(changed_najia[i], (changed_bits >> i) & 1, reference, calendar)
            row["change"]["return_relation"] = relation(changed_najia[i][1], row["branch"])
        rows.append(row)
    missing = set(KIN) - {r["kin"] for r in rows}
    pure_bits = TRIGRAMS[base["palace"]]["bits"] * 9
    for i, ganzhi in enumerate(najia(pure_bits)):
        hidden = line_record(ganzhi, (pure_bits >> i) & 1, reference, calendar)
        if hidden["kin"] in missing:
            hidden["position"] = i + 1
            hidden["source"] = "本宫卦同位；本卦缺此六亲"
            rows[i]["hidden"].append(hidden)
    changed = None
    if moving:
        changed = copy.deepcopy(HEXAGRAMS[changed_bits])
        changed["kin_reference"] = "变爻六亲沿用本卦卦宫；变卦自身宫属只作卦名资料"
        changed["layout_bottom_up"] = [line_record(gz, (changed_bits >> i) & 1, reference, calendar)
                                        for i, gz in enumerate(changed_najia)]
    edges = []
    for source in rows:
        if not source["moving"]:
            continue
        for target in rows:
            if target is source:
                continue
            edges.append({"source": f"本卦{source['position']}爻", "target": f"本卦{target['position']}爻",
                          "relation": relation(source["branch"], target["branch"]),
                          "status": "名义关系；是否有效须依条件判断"})
        edges.append({"source": f"{source['position']}爻化出", "target": f"本卦{source['position']}爻",
                      "relation": source["change"]["return_relation"], "status": "回头关系；是否有效须依条件判断"})
    candidates = []
    if focus:
        for r in rows:
            match = r["self"] if focus == "世爻" else r["other"] if focus == "应爻" else r["kin"] == focus
            if match:
                candidates.append({"position": r["position"], "location": "本卦", "kin": r["kin"],
                                   "branch": r["branch"], "element": r["element"], "moving": r["moving"]})
        if not candidates and focus in KIN:
            for r in rows:
                for h in r["hidden"]:
                    if h["kin"] == focus:
                        candidates.append({"position": r["position"], "location": "伏神", "kin": h["kin"],
                                           "branch": h["branch"], "element": h["element"], "moving": False})
        for c in candidates:
            element = c["element"]
            yuan = next(e for e in PRODUCES if PRODUCES[e] == element)
            ji = next(e for e in CONTROLS if CONTROLS[e] == element)
            chou = next(e for e in CONTROLS if CONTROLS[e] == yuan and PRODUCES[e] == ji)
            c["role_elements"] = {"用神": element, "元神": yuan, "忌神": ji, "仇神": chou}
    result = {"schema_version": "1.0", "input": copy.deepcopy(data), "calendar": calendar,
              "normalized_values_bottom_up": values, "original": base, "changed": changed,
              "moving_positions": moving, "lines_bottom_up": rows, "focus": focus,
              "focus_candidates": candidates, "nominal_edges": edges,
              "analysis_status": "待模型按 references/interpretation.md 结合所问分析；本程序没有计算吉凶或概率",
              "rule_profile": "增删入门：固定纳甲；本宫缺亲补伏神；日干起六神",
              "source_ids": ["S1", "S2", "S3", "E1", "E2", "E3", "E4"]}
    signature = {"values": values, "question": data["question"], "calendar": calendar, "focus": focus}
    result["chart_id"] = hashlib.sha256(json.dumps(signature, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    result["text_diagram"] = render_text_diagram(result)
    return result


def render_text_diagram(chart):
    """画六行爻象；世应属于本卦，箭头只标原动爻。"""
    import unicodedata

    def pad(text, width):
        display_width = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
        return text + " " * max(0, width - display_width)

    def stroke(yang):
        return "━━━━━━━━━" if yang else "━━━   ━━━"

    changed = chart["changed"]
    title = "本卦：" + chart["original"]["name"]
    lines = ["        " + (pad(title, 20) + "   变卦：" + changed["name"] if changed else title + "（静卦）"), ""]
    labels = ("初爻", "二爻", "三爻", "四爻", "五爻", "上爻")
    for row in reversed(chart["lines_bottom_up"]):
        marker = ("○" if row["yang"] else "×") if row["moving"] else " "
        role = "世" if row["self"] else "应" if row["other"] else ""
        left = stroke(row["yang"]) + " " + marker + " " + role
        line = labels[row["position"] - 1] + "    "
        if changed:
            right = changed["layout_bottom_up"][row["position"] - 1]
            line += pad(left, 20) + (" → " if row["moving"] else "   ") + stroke(right["yang"])
        else:
            line += left.rstrip()
        lines.append(line)
        if row["position"] == 4:
            lines.append("")
    lines += ["", "实线为阳，断线为阴；从下往上对应第 1—6 次投掷。"]
    if changed:
        lines.append("○ 老阳变阴；× 老阴变阳；箭头标动爻，世／应标在本卦。")
    else:
        lines.append("六爻皆静，无变卦；世／应标在本卦。")
    return "\n".join(lines)


def cell(value):
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")


def glyph(yang):
    return "⚊" if yang else "⚋"


def describe_line(row):
    return f"{row['kin']}{row['ganzhi']}{row['element']}"


def render_markdown(chart):
    cal, base = chart["calendar"], chart["original"]
    text = ["# 六爻排盘", "", "所问：" + cell(chart["input"]["question"]), "",
            f"本卦：**{base['name']}**；{base['palace']}宫属{base['palace_element']}；{base['stage']}。"]
    if chart["changed"]:
        text.append(f"变卦：**{chart['changed']['name']}**；动爻：" + "、".join(map(str, chart["moving_positions"])) + "爻。")
    else:
        text.append("静卦：无动爻，不另列变卦。")
    text += ["", "输入约定：" + {"back_count": "背面数 0/1/2/3 → 老阴/少阳/少阴/老阳",
                               "front_count": "正面数按三枚互补换算为背面数",
                               "line_values": "6/7/8/9 → 老阴/少阳/少阴/老阳"}[chart["input"]["input_mode"]]]
    if cal.get("at"):
        text += [f"起卦时刻：{cal['at']}（{cal['timezone']}）；" + ("零点换日" if cal['day_boundary'] == 'midnight' else "23 点换日") + "。",
                 f"干支：{cal['year_ganzhi']}年 {cal['month_ganzhi']}月 {cal['day_ganzhi']}日 {cal['hour_ganzhi']}时。"]
    elif cal.get("day_ganzhi"):
        text.append(f"日月：{cal['month_branch']}月 {cal['day_ganzhi']}日；来源：{cell(cal['source'])}。")
    if cal.get("day_ganzhi"):
        text.append("日旬空：" + "、".join(cal["void"]) + "。")
    text += ["", "```text", render_text_diagram(chart), "```", "",
             "卦图由上爻向初爻显示；数据和投掷次序从初爻向上记录。", "",
             "| 爻位 | 六神 | 伏神 | 本卦六亲纳甲 | 世应 | 本卦爻象 | 动静 | 动爻化出 | 时间标记 |",
             "|---|---|---|---|---|---|---|---|---|"]
    labels = ("初爻", "二爻", "三爻", "四爻", "五爻", "上爻")
    for row in reversed(chart["lines_bottom_up"]):
        change = row["change"]
        cols = [labels[row["position"] - 1], row["spirit"] or "待补", "；".join(describe_line(h) for h in row["hidden"]) or "—",
                describe_line(row), "世" if row["self"] else "应" if row["other"] else "—", glyph(row["yang"]),
                row["state"], (glyph(change["yang"]) + " " + describe_line(change)) if change else "—",
                "；".join(row["flags"]) or "—"]
        text.append("| " + " | ".join(cell(c) for c in cols) + " |")
    if chart["changed"]:
        text += ["", "变卦完整爻象（上→下）：" + "　".join(glyph(r["yang"]) for r in reversed(chart["changed"]["layout_bottom_up"])) + "。",
                 "化出列只列原动爻；静爻在变卦排布中的干支变化不作其独立发动。"]
    text += ["", "## 用神候选", ""]
    if chart["focus_candidates"]:
        for c in chart["focus_candidates"]:
            text.append(f"- {c['location']}{c['position']}爻：{c['kin']}{c['branch']}{c['element']}。")
        text.append("\n候选的最终取舍须结合所问和规则；程序未按旺衰自动选择。")
    else:
        text.append("尚未指定用神，需根据所问对象选取。")
    text += ["", "## 计算说明", ""] + ["- " + cell(note) for note in cal["notes"]]
    text += ["- 旬空、月冲、日冲为排盘标记；是否有效作用、是否构成暗动须另判。",
             "- 此文件是计算底稿；最终答复还需按 skill 的分析规则说明倾向、依据与限制。", "",
             "排盘标识：" + chart["chart_id"], ""]
    return "\n".join(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="输入 JSON 文件；- 表示标准输入")
    parser.add_argument("--output-dir", help="新输出目录，保存 input.json、chart.json、chart.md、diagram.txt；已存在时拒绝覆盖")
    args = parser.parse_args()
    try:
        raw = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
        data = json.loads(raw)
        chart = build_chart(data)
        if args.output_dir:
            out = Path(args.output_dir).expanduser().resolve()
            out.mkdir(parents=True, exist_ok=False)
            (out / "input.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (out / "chart.json").write_text(json.dumps(chart, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (out / "chart.md").write_text(render_markdown(chart), encoding="utf-8")
            (out / "diagram.txt").write_text(chart["text_diagram"] + "\n", encoding="utf-8")
            print(json.dumps({"ok": True, "chart_id": chart["chart_id"], "output_dir": str(out)}, ensure_ascii=False))
        else:
            print(json.dumps(chart, ensure_ascii=False, indent=2))
    except (InputError, json.JSONDecodeError, OSError, OverflowError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
