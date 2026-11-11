# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Presents deployment readiness separately from Case scores for Dev Box setup operators.
import html
import json
from pathlib import Path

CHECKS = (
    ("machine", "目标机器与账户"),
    ("package", "完整部署包与测试集"),
    ("source", "远程分支与提交"),
    ("runtime", "Python 与 FSQ 运行环境"),
    ("provider", "Provider 实际连接"),
    ("desktop", "桌面与截图"),
    ("window", "独立 Edge 窗口生命周期"),
    ("record", "准备记录与评测入口"),
)


def new_checks():
    return [{"id": key, "label": label, "status": "pending", "detail": "尚未检查"} for key, label in CHECKS]


def set_check(report, key, status, detail):
    row = next(item for item in report["setup_checks"] if item["id"] == key)
    row.update(status=status, detail=detail)
    print(f"Setup [{key}] {status}: {detail}", flush=True)


def setup_ready(report):
    rows = report.get("setup_checks", [])
    return len(rows) == len(CHECKS) and {item["id"] for item in rows} == {key for key, _label in CHECKS} and all(item["status"] == "passed" for item in rows)


def render_setup_report(root, report):
    def escape(value):
        return html.escape(str(value))

    labels = {"pending": "未检查", "running": "检查中", "passed": "通过", "blocked": "阻塞", "failed": "失败"}
    ready = report.get("status") == "setup_ready" and setup_ready(report)
    passed = sum(item["status"] == "passed" for item in report["setup_checks"])
    rows = "".join(
        f'<tr><td data-label="项目"><strong>{escape(item["label"])}</strong></td>'
        f'<td data-label="状态"><span class="badge {"pass" if item["status"] == "passed" else "warning"}">'
        f"{escape(labels.get(item['status'], item['status']))}</span></td>"
        f'<td data-label="详情">{escape(item["detail"])}</td></tr>'
        for item in report["setup_checks"]
    )
    source = report.get("git_source", {})
    runtime = report.get("deployment", {})
    provider = report.get("provider", {})
    images = ""
    for name, label in (("setup-before.png", "独立测试窗口"), ("setup-after-driver-close.png", "驱动断开后窗口仍由 Runner 管理")):
        path = root / name
        if path.exists():
            import base64

            data = base64.b64encode(path.read_bytes()).decode("ascii")
            images += f'<figure><figcaption>{label}</figcaption><img alt="{label}" src="data:image/png;base64,{data}"></figure>'
    smoke = report.get("smoke", {"status": "not_requested"})
    smoke_status = {
        "not_requested": "未运行\uff08默认只验收环境\uff09",
        "pending": "等待环境就绪后执行",
        "passed": "通过",
        "failed": "不通过",
        "incomplete": "未获完整结论",
        "blocked": "环境阻塞",
    }.get(smoke["status"], smoke["status"])
    css = Path(__file__).with_name("report.css").read_text(encoding="utf-8")
    title = "可执行评测" if ready else "尚未完成环境验收"
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>FSQ Dev Box 接入报告</title><style>{css}"
        ".setup-table th:nth-child(1){width:25%}.setup-table th:nth-child(2){width:15%}"
        ".setup-table th:nth-child(3){width:60%}</style></head><body><main>"
        f'<header class="page-header"><div><p class="eyebrow">DEV BOX SETUP · FSQ</p>'
        f"<h1>FSQ Dev Box 接入报告</h1><p>执行主机\uff1a{escape(report.get('execution_machine', '未确认'))}"
        f" · 执行账户\uff1a{escape(report.get('execution_account', '未确认'))}</p></div>"
        f'<span class="badge {"pass" if ready else "warning"}">{title}</span></header>'
        f'<section class="panel"><h2>环境验收\uff1a{passed} / {len(report["setup_checks"])} 项通过</h2>'
        "<p>这是接入检查\uff0c不是测试集成功率。授权和桌面条件仍会在每次评测前重新检查。</p>"
        '<table class="setup-table"><thead><tr><th>项目</th><th>状态</th><th>详情</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></section>"
        '<section class="panel"><h2>运行来源与可复用路径</h2>'
        f"<p>源码\uff1a{escape(source.get('url', '未确认'))}</p>"
        f"<p>分支\uff1a{escape(source.get('branch', '未确认'))}</p><p>提交\uff1a<code>{escape(source.get('commit', '未确认'))}</code></p>"
        f"<p>FSQ 版本\uff1a{escape(runtime.get('version', '未确认'))}</p>"
        f"<p>解释器\uff1a<code>{escape(runtime.get('python', '未确认'))}</code></p>"
        f"<p>Provider\uff1a{escape(provider.get('provider', '未确认'))} · 模型\uff1a{escape(provider.get('model', '未确认'))}</p>"
        "<p>后续在同一 Machine 选择 FSQ WAA Remote Eval。其他 Machine 需要各自的安装、授权和桌面检查。</p></section>"
        f'<section class="panel"><h2>可选真实 Case 验收</h2><p>{escape(smoke_status)}</p>'
        "<p>环境就绪与 Case 得分分开记录\uff0cCase 不通过不会被伪装成安装失败或成功。</p></section>"
        f'<section class="panel"><h2>窗口生命周期证据</h2><div class="screenshots">{images}</div></section>'
        '<details class="panel"><summary>完整准备记录与诊断</summary>'
        f"<pre>{escape(json.dumps(report, indent=2, ensure_ascii=False))}</pre></details>"
        "</main></body></html>"
    )
