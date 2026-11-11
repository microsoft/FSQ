# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Produces persistent, self-contained result and screenshot evidence for WAA Pipeline operators.
import base64
import html
import json
import math
import zipfile
from datetime import datetime
from pathlib import Path


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=True), encoding="utf-8")
    temporary.replace(path)


def summarize(rows):
    evaluated = [row for row in rows if verdict(row)[0] in {"pass", "fail"}]
    fsq_success = [row for row in rows if row.get("fsq_status") == "success"]
    return {
        "selected": len(rows),
        "executed": sum(row.get("fsq_status") is not None for row in rows),
        "valid_evaluations": len(evaluated),
        "benchmark_passed": sum(row["benchmark_score"] == 1 for row in evaluated),
        "benchmark_failed": sum(row["benchmark_score"] < 1 for row in evaluated),
        "benchmark_pass_rate": sum(row["benchmark_score"] == 1 for row in evaluated) / len(evaluated) if evaluated else None,
        "fsq_reported_success": len(fsq_success),
        "false_positives": sum(row.get("evaluation_status") == "valid" and row.get("benchmark_score") == 0 for row in fsq_success),
        "unscored": len(rows) - len(evaluated),
    }


def verdict(row):
    status = row.get("evaluation_status", "not_run")
    score = row.get("benchmark_score")
    if status == "valid" and type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1:
        return ("pass", "通过") if score == 1 else ("fail", "不通过")
    return {
        "blocked": ("warning", "环境阻塞"),
        "error": ("warning", "评测异常"),
        "unknown": ("warning", "结果未知"),
        "not_run": ("neutral", "尚未判分" if row.get("fsq_status") else "未执行"),
    }.get(status, ("warning", "未获有效结论"))


CASE_TITLES = {
    "profile_name_from_edge": "修改 Profile 名称",
    "edge_font_size": "调整默认字体大小",
    "edge_default_download_folder": "修改下载目录",
    "enable_do_not_track_from_edge": "开启 Do Not Track",
    "favorites": "添加收藏栏书签",
    "history_for_edge": "删除 YouTube 浏览历史",
    "data_delete_automacally_from_edge": "设置退出时清理数据",
    "cookie_data_for_edge": "删除 Amazon Cookie",
    "default_search_engine_from_edge": "修改默认搜索引擎",
    "enable_enhanced_safety_browsing_from_edge": "开启指定安全设置",
    "validate_pwa_installed": "安装 PWA",
    "url_shortcuts_on_desktop": "创建桌面快捷方式",
    "edge_home_page": "修改主页",
}


def escape(value):
    return html.escape(str(value))


def shown(value):
    return "未采集" if value is None else str(value)


def expected_text(row):
    rules = row.get("expected")
    if not isinstance(rules, dict):
        return "未取得规则"
    if "expected" in rules:
        return f"精确等于 {rules['expected']}"
    if rules.get("type") == "range":
        return f"{rules.get('min')} ≤ 实际值 < {rules.get('max')}"
    return json.dumps(rules, ensure_ascii=False)


def duration_text(row):
    seconds = row.get("cli", {}).get("seconds")
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
        return "未记录"
    return format_seconds(seconds)


def format_seconds(seconds):
    if seconds < 60:
        return f"{seconds:.1f} 秒"
    whole = round(seconds)
    if whole >= 3600:
        return f"{whole // 3600} 时 {(whole % 3600) // 60} 分"
    return f"{whole // 60} 分 {whole % 60} 秒"


def wall_duration(report):
    try:
        start = datetime.fromisoformat(report["started_at"])
        end = datetime.fromisoformat(report["finished_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if start.tzinfo is None or end.tzinfo is None:
        return None
    seconds = (end - start).total_seconds()
    return seconds if seconds >= 0 else None


def donut_chart(label, parts, center, caption):
    total = sum(count for _name, count, _color in parts)
    offset = 0.0
    segments, legend = [], []
    for name, count, color in parts:
        if count:
            percentage = count * 100 / total
            segments.append(
                f'<circle class="donut-segment" cx="50" cy="50" r="40" pathLength="100" '
                f'style="stroke:var(--chart-{color})" stroke-width="12" fill="none" '
                f'stroke-dasharray="{percentage:.6f} {100 - percentage:.6f}" '
                f'stroke-dashoffset="{-offset:.6f}" transform="rotate(-90 50 50)" '
                f'data-count="{count}" data-label="{escape(name)}"/>'
            )
            offset += percentage
        legend.append(f'<li><span class="dot dot-{color}" aria-hidden="true"></span><span>{escape(name)}</span><strong>{count}</strong></li>')
    description = label + "\uff1a" + "\uff0c".join(f"{name} {count}" for name, count, _color in parts)
    return (
        '<div class="distribution"><div class="donut">'
        f'<svg viewBox="0 0 100 100" role="img" aria-label="{escape(description)}">'
        '<circle cx="50" cy="50" r="40" fill="none" stroke="var(--chart-track)" stroke-width="12"/>' + "".join(segments) + "</svg>"
        f'<div class="donut-center" aria-hidden="true"><strong>{escape(center)}</strong>'
        f'<small>{escape(caption)}</small></div></div><ul class="legend">' + "".join(legend) + "</ul></div>"
    )


def dashboard_charts(rows, inventory, by_id, summary):
    total = len(inventory)
    invoked = summary["executed"]
    coverage = f"{invoked / total:.1%}" if total else "未记录"
    coverage_donut = (
        donut_chart(
            "测试集执行覆盖",
            [("已调用 FSQ", invoked, "coverage"), ("未调用", max(0, total - invoked), "neutral")],
            coverage,
            "执行覆盖",
        )
        if total
        else '<p class="muted">未保存完整测试集清单\uff0c无法计算执行覆盖。</p>'
    )
    timings = [row.get("cli", {}).get("seconds") for row in rows]
    known = [value for value in timings if type(value) in (int, float) and math.isfinite(value) and value >= 0]
    maximum = max(known, default=0) or 1
    bars = []
    for index, row in enumerate(rows, 1):
        getter = row.get("getter") or by_id.get(row["id"], {}).get("getter")
        title = CASE_TITLES.get(getter, row.get("instruction") or row["id"])
        seconds = row.get("cli", {}).get("seconds")
        valid = type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0
        width = seconds / maximum * 100 if valid else 0
        bars.append(
            f'<div class="timing-row"><a href="#case-{index}" class="timing-name">{index}. {escape(title)}</a>'
            f'<span class="timing-value">{escape(duration_text(row))}</span>'
            f'<div class="bar-track" role="img" aria-label="{escape(title)}\uff1a{escape(duration_text(row))}">'
            f'<div class="bar-fill" style="width:{width:.4f}%" data-seconds="{seconds if valid else ""}"></div>'
            "</div></div>"
        )
    return (
        '<div class="chart-grid"><section class="panel chart-panel"><div class="section-title">'
        '<h2>测试集执行覆盖</h2><span class="muted">不是通过率</span></div>'
        + coverage_donut
        + (f'<p class="chart-note">已调用 FSQ {invoked} 条 / 测试集 {total} 条\uff1b本轮选中 {summary["selected"]} 条。</p>' if total else "")
        + '</section><section class="panel chart-panel"><div class="section-title"><h2>逐 Case 执行耗时</h2>'
        '<span class="muted">FSQ CLI</span></div>'
        + ("".join(bars) if bars else '<p class="muted">尚无 Case 执行耗时。</p>')
        + '<p class="chart-note">条形长度按本轮最长 CLI 耗时归一化\uff0c不是超时预算或历史对比。</p>'
        + "</section></div>"
    )


def command_details(directory):
    entries = []
    for path in directory.glob("*.command.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise ValueError("调用记录不是 JSON 对象")  # noqa: TRY004, TRY301 - Preserve malformed-record diagnostics, not a passing fallback.
        except (OSError, ValueError, UnicodeDecodeError) as error:
            entries.append(("", f'<p class="diagnostic">无法读取调用记录 {escape(path.name)}\uff1a{escape(error)}</p>'))
            continue
        state = {
            "prepared": "参数已保存\uff0c执行结束状态未确认",
            "completed": "完成",
            "failed": "失败",
            "timed_out": "超时",
            "execution_error": "进程执行异常",
            "protocol_error": "返回协议异常",
            "interrupted": "已中断",
        }.get(record.get("state"), "未知")
        process = record.get("process", {})
        entries.append(
            (
                str(record.get("recorded_at", "")),
                (
                    '<div class="invocation-record">'
                    f"<h3>{escape(record.get('operation', path.name))} · {state}</h3>"
                    f"<p><strong>记录文件\uff1a</strong><code>{escape(path.name)}</code></p>"
                    f"<p><strong>工作目录\uff1a</strong><code>{escape(record.get('working_directory', '未记录'))}</code></p>"
                    f"<p><strong>解释器\uff1a</strong><code>{escape(record.get('python_executable', '未记录'))}</code></p>"
                    f"<p>FSQ {escape(record.get('fsq_version', '未记录'))}\uff1b"
                    f"超时 {escape(record.get('timeout_seconds', '未记录'))} 秒\uff1b"
                    f"退出码 {escape(process.get('exit_code', '未确认'))}。</p>"
                    "<p><strong>CLI 等价命令展示</strong>\uff08Windows 命令行格式\uff0c完整参数以 JSON 数组为准\uff09\uff1a</p>"
                    f'<pre class="cli-command">{escape(record.get("cli_display_windows", "未记录"))}</pre>'
                    "<details><summary>完整调用记录、实际 wrapper 参数与复现配置</summary>"
                    f"<pre>{escape(json.dumps(record, indent=2, ensure_ascii=False))}</pre></details></div>"
                ),
            )
        )
    if not entries:
        return '<details class="cli-records"><summary>FSQ CLI 调用记录</summary><p class="muted">此历史运行未保存 CLI 参数记录\uff1b未根据源码推测或补造命令。</p></details>'
    return (
        f'<details class="cli-records"><summary>FSQ CLI 调用记录\uff08{len(entries)} 条\uff09</summary>'
        '<p class="chart-note">参数在启动前保存\uff0c结束后补充状态。下方等价命令不包含 PID 绑定适配\uff0c'
        "不能直接视为同等环境的重放。旧 PID 已失效\uff1b复现时用记录中的 reproduction 配置"
        "启动新的 Pipeline Task\uff0ccase-ids 选择本条 Case\uff0c重新创建独立 Profile 和目标进程。</p>"
        + "".join(content for _timestamp, content in sorted(entries, key=lambda entry: entry[0]))
        + "</details>"
    )


def render_report(root, report):
    rows = report["tasks"]
    remote = report.get("git_source", {}).get("role") == "installed_runtime_source"
    report_title = "FSQ 远程评测报告" if remote else "FSQ 本地评测报告"
    rate_label = "本轮远程成功率" if remote else "本轮本地成功率"
    summary = summarize(rows)
    inventory = report.get("inventory", [])
    by_id = {item["id"]: item for item in inventory}
    selected_ids = {row["id"] for row in rows}
    unselected = [item for item in inventory if item["id"] not in selected_ids]
    rate = summary["benchmark_pass_rate"]
    rate_text = "暂无法计算" if rate is None else f"{rate:.1%}".replace(".0%", "%")
    overall = {
        "preparing": "正在检查环境",
        "ready": "环境检查完成\uff0c尚未执行",
        "running": "评测进行中",
        "passed": "本轮选中的 Case 全部通过",
        "failed": "本轮存在不通过的 Case",
        "blocked": "评测被环境问题阻塞",
        "incomplete": "评测未完整完成",
    }.get(report.get("status"), "评测状态未知")
    tone = "pass" if report.get("status") == "passed" and rows and summary["benchmark_passed"] == len(rows) else "warning"
    case_rows, sections = [], []
    for index, row in enumerate(rows, 1):
        kind, label = verdict(row)
        getter = row.get("getter") or by_id.get(row["id"], {}).get("getter")
        title = CASE_TITLES.get(getter, row.get("instruction") or row["id"])
        fsq = {"success": "成功", "failed": "失败", "cancelled": "已取消", "error": "调用异常", "unknown": "未知", "inconclusive": "无结论"}.get(row.get("fsq_status"), "未执行")
        score = row.get("benchmark_score") if kind in {"pass", "fail"} else None
        evidence = f'<span class="value">{escape(shown(row.get("observed")))}</span><small>预期\uff1a{escape(expected_text(row))}</small>'
        case_rows.append(
            f'<tr><td class="case-cell" data-label="Case"><strong>{index}. {escape(title)}</strong>'
            f"<code>{escape(row['id'])}</code></td>"
            f'<td data-label="独立判分"><span class="badge {kind}">{label}</span>'
            f"<small>得分\uff1a{escape('未计分' if score is None else f'{score:g}')}</small></td>"
            f'<td data-label="实际结果">{evidence}</td>'
            f'<td data-label="FSQ 自报">{fsq}</td>'
            f'<td data-label="CLI 耗时">{escape(duration_text(row))}</td>'
            f'<td data-label="证据"><a href="#case-{index}">查看截图与明细</a></td></tr>'
        )
        images = ""
        for image_key, image_label in (("before", "执行前"), ("after", "执行后")):
            path = root / row["id"] / f"{image_key}.png"
            if path.exists():
                image = base64.b64encode(path.read_bytes()).decode("ascii")
                images += f'<figure><figcaption>{image_label}</figcaption><img loading="lazy" alt="{escape(title)}\uff1a{image_label}的测试窗口" src="data:image/png;base64,{image}"></figure>'
            else:
                images += f'<p class="muted">{image_label}截图未保存。</p>'
        diagnostics = row.get("error") or row.get("fsq_error") or row.get("evidence_errors")
        detail = f'<p class="diagnostic">诊断\uff1a{escape(json.dumps(diagnostics, ensure_ascii=False))}</p>' if diagnostics else ""
        sections.append(
            f'<details id="case-{index}" class="case-detail"><summary>{index}. {escape(title)}'
            f' <span class="badge {kind}">{label}</span></summary>'
            f"<p><strong>原始任务\uff1a</strong>{escape(row.get('instruction', ''))}</p>"
            f"<p><strong>Case ID\uff1a</strong><code>{escape(row['id'])}</code></p>"
            f"<p><strong>Run ID\uff1a</strong><code>{escape(shown(row.get('run_id')))}</code></p>"
            f"<p>实际值\uff1a{escape(shown(row.get('observed')))}\uff1b预期\uff1a{escape(expected_text(row))}。</p>"
            f'{detail}{command_details(root / row["id"])}<div class="screenshots">{images}</div>'
            f"<details><summary>本条原始结果 JSON</summary><pre>{escape(json.dumps(row, indent=2, ensure_ascii=False))}</pre></details>"
            "</details>"
        )
    if not rows:
        case_rows.append('<tr><td colspan="6">尚无选中的 Case。请查看下方环境诊断\uff0c确认测试集和选择参数。</td></tr>')
    unselected_rows = "".join(
        f"<li><strong>{escape(CASE_TITLES.get(item.get('getter'), item['id']))}</strong>"
        f"<code>{escape(item['id'])}</code><span>未执行"
        f"{chr(0xFF1B)}{'当前尚未适配' if not item.get('supported') else '本轮未选择'}</span></li>"
        for item in unselected
    )
    scope = f"测试集共 {len(inventory)} 条\uff1b本轮选中 {len(rows)} 条\uff0c另外 {len(unselected)} 条未执行。" if inventory else f"本轮选中 {len(rows)} 条\uff1b未保存完整测试集清单。"
    provider = report.get("provider", {})
    errors = report.get("errors", [])
    diagnostic = (
        '<section class="notice warning"><h2>环境或执行诊断</h2>'
        f"<pre>{escape(json.dumps(errors, indent=2, ensure_ascii=False))}</pre>"
        "<p>这些问题不能当作正常的 Case 失败。保留证据\uff0c修正对应环境或配置后再启动新的 Task。</p></section>"
        if errors
        else ""
    )
    duration = wall_duration(report)
    result_donut = donut_chart(
        "本轮结果分布",
        [("通过", summary["benchmark_passed"], "pass"), ("不通过", summary["benchmark_failed"], "fail"), ("未获结论", summary["unscored"], "neutral")],
        summary["selected"],
        "本轮 Case",
    )
    charts = dashboard_charts(rows, inventory, by_id, summary)
    source = report.get("git_source")
    source_panel = (
        (
            '<details class="panel"><summary>远程 Git 代码来源与提交</summary>'
            f"<p><strong>URL\uff1a</strong>{escape(source.get('url'))}</p>"
            f"<p><strong>分支\uff1a</strong>{escape(source.get('branch'))}</p>"
            f"<p><strong>本次获取的提交\uff1a</strong><code>{escape(source.get('commit'))}</code></p>"
            f"<p><strong>专用缓存\uff1a</strong><code>{escape(source.get('checkout_directory'))}</code></p>"
            + (
                "<p>本次 FSQ 从此提交的归档安装到远端独立虚拟环境\uff0c运行来源和解释器信息保存在 runtime.json。</p>"
                if source.get("role") == "installed_runtime_source"
                else "<p>此处记录远程代码上下文\uff0c不表示已安装或执行该源码。真正的 FSQ 运行版本由 fsq-python 决定\uff0c上方显示其版本。</p>"
            )
            + "</details>"
        )
        if isinstance(source, dict)
        else ""
    )
    styles = Path(__file__).with_name("report.css").read_text(encoding="utf-8")
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta name="color-scheme" content="light dark"><title>{report_title}</title>'
        f"<style>{styles}</style></head><body><main>"
        '<header class="page-header"><div><p class="eyebrow">WINDOWS UI · FSQ / WAA</p>'
        f"<h1>{report_title}</h1>"
        f'<p class="metadata">FSQ {escape(report.get("fsq_version", "未记录"))} · Edge {escape(report.get("edge_version", "未记录"))}'
        f" · Provider {escape(provider.get('provider', '未记录'))} · 模型 {escape(provider.get('model', '未记录'))}"
        f" · 执行主机 {escape(report.get('execution_machine', '未记录'))}</p></div>"
        f'<span class="badge {tone}" role="status">{escape(overall)}</span>'
        "</header>" + diagnostic + '<section class="panel summary-panel"><div class="section-title"><h2>本轮概览</h2>'
        '<span class="muted">独立 Evaluator 判分</span></div>'
        f'<div class="summary-status">已调用 FSQ {summary["executed"]} 条'
        f" <span>·</span> 通过 {summary['benchmark_passed']} 条"
        f" <span>·</span> 不通过 {summary['benchmark_failed']} 条"
        f" <span>·</span> 未获结论 {summary['unscored']} 条</div>"
        '<div class="summary-grid">'
        f'<div class="kpi"><strong>{summary["selected"]}</strong><span>本轮 Case</span>'
        f"<small>有效判分 {summary['valid_evaluations']} 条</small></div>"
        f'<div class="result-chart">{result_donut}</div>'
        f'<div class="kpi" id="local-rate"><strong>{rate_text}</strong><span>{rate_label}</span>'
        f"<small>{summary['benchmark_passed']} 个通过 / {summary['valid_evaluations']} 个有效判分</small></div>"
        f'<div class="kpi" id="wall-duration"><strong>{escape(format_seconds(duration) if duration is not None else "未记录")}</strong>'
        "<span>本轮总耗时</span><small>含准备、执行与判分</small></div>"
        f'<div class="kpi"><strong>{len(unselected) if inventory else "未记录"}</strong>'
        "<span>测试集未执行</span><small>不计为通过或失败</small></div></div>"
        f'<p class="scope">{escape(scope)}</p>'
        '<p class="footnote">成功率 = 独立判分通过数 ÷ 有效判分数。未执行或无有效结论的 Case 不计为通过\uff0c'
        "也不伪装成正常失败。该比例不是完整测试集的成功率\uff0c也不代表多轮稳定性。</p></section>"
        + charts
        + source_panel
        + '<section class="panel"><div class="section-title"><h2>本轮执行的 Case 与结果</h2>'
        '<span class="muted">原始任务 · 实际状态校验</span></div>'
        '<table aria-label="本轮 Case 执行结果"><thead><tr><th scope="col">Case</th>'
        '<th scope="col">独立判分</th><th scope="col">实际结果 / 预期</th><th scope="col">FSQ 自报</th>'
        '<th scope="col">CLI 耗时</th><th scope="col">证据</th></tr></thead><tbody>'
        + "".join(case_rows)
        + "</tbody></table></section>"
        + '<section class="panel"><h2>逐 Case 截图与证据</h2>'
        + "".join(sections)
        + "</section>"
        + (f'<details class="panel"><summary>本轮未执行的 {len(unselected)} 条 Case</summary><ul>{unselected_rows}</ul></details>' if unselected else "")
        + '<details class="panel"><summary>环境、完整测试集清单与原始结果 JSON</summary><pre>'
        + escape(json.dumps(report, indent=2, ensure_ascii=False))
        + "</pre></details>"
        + '<p class="muted">环境边界\uff1a独立 Edge Profile\uff0c使用进程绑定的 Windows UI 适配\uff1b'
        "不是原版 WAA VM 快照。未自动重试或修复 Case。本报告不通过网络获取数据。</p>" + "</main></body></html>"
    )


def write_report(root, report):
    report["summary"] = summarize(report["tasks"])
    save_json(root / "results.json", report)
    if "setup_checks" in report:
        from setup_report import render_setup_report

        page = render_setup_report(root, report)
    else:
        page = render_report(root, report)
    (root / "report.html").write_text(page, encoding="utf-8")
    archive_path = root / "evidence.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink() and path != archive_path and path.suffix != ".tmp":
                archive.write(path, str(path.relative_to(root)))
