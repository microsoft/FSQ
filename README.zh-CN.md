<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo-light.svg">
    <img alt="FSQ - Fully Self Quality" src="docs/assets/logo-light.svg" width="320">
  </picture>
</p>

<h3 align="center">证据优先、可检查、可重放、可验证的 AI UI 自动化。</h3>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="#五分钟快速开始">快速开始</a> ·
  <a href="#fsq-如何工作">工作方式</a> ·
  <a href="#支持平台">支持平台</a> ·
  <a href="docs/getting-started.zh-CN.md">中文文档</a>
</p>

> [!IMPORTANT]
> FSQ v0.1.0 是 Alpha 版本，适合评估、试用和早期贡献。公开 API 与 Case 编写细节在 1.0 前仍可能调整。请先阅读[支持与稳定性说明](docs/support-and-stability.md)。

FSQ 会把一个自然语言 UI 目标变成可观察的自动化 Run，保存截图、UI Snapshot、事件和报告作为证据，并可把成功执行的真实动作转换为可审查的 Case，用于确定性重放。FSQ 使用 Playwright、uiautomator2、pywinauto 和 Appium 作为平台后端；它不会替代或自动安装这些后端所需的主机前置条件。

## 无需 LLM，运行已有 Case

已有 Case 后，可以把 FSQ 当作测试工具使用。查看 Run 历史和生成离线报告不需要配置 LLM Provider；严格重放也不需要 Provider，除非编写的 Case 包含 AI assertion。

```bash
python -m pip install fsq-agent
fsq init --platform web --browser-channel chrome
# 将审查后的 Case 保存在仓库中，例如：cases/web/*.fsq.yaml
fsq case test --platform web cases/web/YOUR_CASE.fsq.yaml
fsq runs show RUN_ID --open
```

## 使用 AI 创建 Case

配置 LLM Provider 后，可以使用 `fsq case create --platform web --goal "..."`，让 FSQ 操作真实 UI，并根据成功的 Run 创建可审查的 `.fsq.yaml` Case。Coding agent 应提供目标和上下文；FSQ 通过真实执行和证据验证路径。

## 查看 FSQ 实际运行

观看 FSQ 如何把自然语言目标转化为真实 UI 执行、证据捕获、可审查 Case 和确定性重放。

https://github.com/user-attachments/assets/aa9d0a12-2f93-4894-8349-52a013424939

<p align="center">
  <a href="https://youtu.be/QqCahxGDdS0">在 YouTube 观看完整演示</a>
</p>

<p align="center">
  <img src="docs/assets/fsq-workflow.svg" alt="FSQ 工作流：描述目标、执行一次、捕获证据、验证、审查 Case、确定性重放" width="880">
</p>

## 为什么是 FSQ

- **检查事实。** 每次 Run 都把截图、标准化 UI Snapshot、有序事件、元数据和报告保存在一起。
- **区分探索与回归。** AI 可以探索目标；经过审查的 YAML Case 可以确定性重放已编写动作。
- **跨 UI 平面统一工作流。** Web、Android、Windows 和 macOS 共享同一套 Case、证据、Run 和 readiness 概念。
- **默认本地优先。** Workspace、证据、Provider 配置和 Control Plane 默认都在本地。

FSQ 补充而不是替代平台自动化库。Playwright、uiautomator2、pywinauto 和 Appium 负责实际平台交互；FSQ 在其上提供目标驱动执行、统一 Case 格式、证据捕获、验证、Run 历史和本地 Control Plane。

## 五分钟快速开始

这个公开 Web 示例使用 [TodoMVC](https://todomvc.com/examples/react/dist/)，需要已安装 Chromium 系浏览器，并且只在本地写入项目数据。AI 驱动命令还需要配置 Provider。

### 1. 安装

```bash
python -m pip install fsq-agent
```

基础包包含四个平台支持所需的 Python 依赖。浏览器、应用、设备、ADB 和 Appium 服务仍是系统前置条件。`fsq init` 不会动态安装这些内容。

### 2. 创建空 Workspace

```bash
mkdir fsq-web-demo
cd fsq-web-demo
fsq init --platform web --browser-channel chrome
fsq doctor
```

Workspace Root Strategy 是精确规则：

- 如果当前目录为空，`fsq init` 会把当前目录作为 Workspace root。
- 如果当前目录非空，`fsq init` 会创建缺失的 `<当前目录>/<workspace-name>` 子目录。可用 `--name NAME` 指定名称，然后进入该子目录执行 Workspace 命令。
- 其他 CLI 命令不会向父目录搜索；请在准确注册的 Workspace root 中运行。

### 3. 不依赖规划 LLM，重放公开示例

把当前的 [`examples/web/example-domain.fsq.yaml`](examples/web/example-domain.fsq.yaml) 下载到 Workspace 的 `cases/web/`，然后运行：

```bash
mkdir -p cases/web
curl --fail --location --output cases/web/example-domain.fsq.yaml \
  https://raw.githubusercontent.com/microsoft/FSQ/main/examples/web/example-domain.fsq.yaml
fsq case test --platform web cases/web/example-domain.fsq.yaml
fsq runs list --platform web
```

### 4. 使用 AI 探索

在任意目录配置一个用户级 Provider：

```bash
fsq providers configure github_copilot
fsq providers status
```

使用 OpenAI 官方 API 时，运行 `fsq providers configure openai`，在隐藏输入提示中输入 API Key，再选择账户可用的 GPT-5 或更高版本通用模型。Azure 部署使用 `fsq providers configure azure_openai`。浏览器 Settings 页面也提供这三种 Provider，详见[配置指南](docs/getting-started.zh-CN.md#配置-ai-探索)。配置保存在 `~/.fsq`，由 CLI 和 Control Plane 共享；同一时间只有一个活动 Provider。

然后回到 Workspace：

```bash
fsq case create --platform web \
  --goal "Open https://example.com and verify the Example Domain heading is visible."

fsq case test --platform web --suggest cases/web/example-domain.fsq.yaml
fsq runs show RUN_ID --open
```

`--suggest` 会让源 Case 只执行一次，然后让 AI 只基于已保存的 Case、报告和证据进行分析。建议和可选候选 Case 只保存在对应 Run 内，不会修改源 Case。

### 5. 打开本地 Control Plane

```bash
fsq ui
```

安装后的 wheel 已包含编译好的前端。默认监听 `127.0.0.1:8879`，运行时不需要 Node.js。

## FSQ 如何工作

```text
目标 -> AI 探索 -> 证据 -> 验证 -> 可审查 Case
                                  |
已审查 Case -> 确定性重放 -> 新证据
```

动态执行和确定性重放共享平台 Harness 与证据契约。原始执行结果是不可变的；后续 suggestion 分析不能改写它，也不能再次操作 UI。

## 支持平台

| 平台 | 交互后端 | 主机前置条件 |
|---|---|---|
| Web | Playwright | 已安装受支持的 Chromium 系浏览器 |
| Android | uiautomator2 | ADB 和在线授权设备 |
| Windows | pywinauto | Windows 和已有应用 |
| macOS | Appium Mac2 | macOS、已有应用和可访问的 Appium 服务 |

所有 Python 后端包都会随 `fsq-agent` 安装；平台应用和主机服务不会自动安装。请在准确的 Workspace root 中运行 `fsq doctor` 查看可执行的 readiness 结果。

## 文档

| 资源 | 用途 |
|---|---|
| [English README](README.md) | 英文项目首页 |
| [中文快速开始](docs/getting-started.zh-CN.md) | 安装、Workspace 规则和首次 Web Run |
| [Getting started](docs/getting-started.md) | 英文快速开始 |
| [CLI reference](docs/cli-reference.md) | 当前公开命令 |
| [FSQ Case format](docs/case-format.md) | Case 结构和公开示例 |
| [Platform prerequisites](docs/platform-prerequisites.md) | 平台前置条件边界 |
| [Support and stability](docs/support-and-stability.md) | Alpha 范围、兼容性、隐私和支持预期 |

## License

[MIT](LICENSE) - Copyright (c) Microsoft Corporation.
