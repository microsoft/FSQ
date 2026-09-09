# 快速开始

这份指南帮助新用户从安装开始，完成一次针对公开 [TodoMVC](https://todomvc.com/examples/react/dist/) 的确定性 Web Run。FSQ v0.1.0 是 Alpha 软件；生产采用前请先阅读[支持与稳定性说明](support-and-stability.md)。

## 前置条件

- Python 3.11 或更新版本。
- 已安装受支持的 Chromium 系浏览器。示例使用稳定版 Chrome。
- 一个可用作本地 FSQ Workspace 的空目录。

AI 探索和 suggestion 分析还需要 OpenAI、GitHub Copilot 或 Azure OpenAI。确定性 Case 重放不需要规划 LLM，除非已编写的 Case 包含 AI assertion。

## 安装

```bash
python -m pip install fsq-agent
fsq --help
```

基础包包含所有支持平台的 Python 依赖。FSQ 不会安装浏览器、应用、ADB、设备、Appium 服务或其他主机前置条件。

## 初始化空 Workspace

```bash
mkdir fsq-web-demo
cd fsq-web-demo
fsq init --platform web --browser-channel chrome
fsq doctor
```

如果当前目录为空，它会成为 Workspace root。如果当前目录非空，`init` 会保留它并创建一个缺失的 `<current-directory>/<workspace-name>` 子目录。其他 Workspace 命令必须在准确注册的 root 中运行；它们不会向父目录搜索。

## 运行公开确定性示例

把当前的 [`examples/web/example-domain.fsq.yaml`](../examples/web/example-domain.fsq.yaml) 下载到 Workspace 的 `cases/web/`，然后运行：

```bash
mkdir -p cases/web
curl --fail --location --output cases/web/example-domain.fsq.yaml \
  https://raw.githubusercontent.com/microsoft/FSQ/main/examples/web/example-domain.fsq.yaml
fsq case test --platform web cases/web/example-domain.fsq.yaml
fsq runs list --platform web
```

这个 Case 会启动已配置的浏览器，打开 TodoMVC，添加两个任务、完成第一个任务、筛选未完成任务、验证预期可见状态，然后关闭浏览器。证据保存在 `.fsq/runs/web/<run-id>/` 下。

## 配置 AI 探索

```bash
fsq providers configure github_copilot
fsq providers status
```

也可以运行 `fsq providers configure openai` 连接 OpenAI 官方 API，或运行 `fsq providers configure azure_openai` 连接 Azure 部署。配置保存在用户级 `~/.fsq` 下，由 CLI 和 Control Plane 共享。OpenAI 会隐藏 API Key 输入，并提供可选模型；即使只有一个模型也需要显式选择。

浏览器配置流程：运行 `fsq ui`，打开 **Settings**。

1. 点击 **Add configuration** 或 **Change provider**，选择 **OpenAI**。
2. 输入 API Key，点击 **Load models**，选择模型，再点击 **Save changes**。
3. 点击 **Test connection**，使用已保存配置发送最小请求。
4. 回到 **Home** 查看 Provider 摘要，再到 **Test Runner** 选择 Workspace、平台和目标，显式启动 Explore。

OpenAI 固定使用 `https://api.openai.com/v1/` 和 Responses API，不支持自定义端点。模型列表仅展示 GPT 主版本 5 或更高的通用模型，排除 mini、nano、Codex、embedding、audio、realtime、image、search、transcription、TTS 等变体。空列表不能保存；修改 Key 后必须重新加载并选择模型。Azure 则需要资源端点、**deployment name（部署名，不一定是 OpenAI model id）** 和 API Key。

保存只复核模型可见性，不发送推理请求；status/readiness 也不等于连接测试。API Key 以明文保存在本地，Settings 默认掩码显示，但受 loopback 限制的 Config API 会完整返回。只有一个活动 Provider，替换成功后清理其他 Provider 凭据；不从环境变量或其他 Provider 回退。

保存响应丢失时，页面会提示结果未知并重新读取配置。可以用 **Reload configuration** 重试恢复。关闭页面不等于取消服务端保存，读到当前配置也不能证明旧请求已经完成。FSQ 不会自动重提或回滚；恢复后应核对当前 Provider 和剩余草稿再继续。

## 探索与检查

```bash
fsq case create --platform web --goal "Open https://example.com and verify the Example Domain heading is visible."
fsq runs list
fsq runs show RUN_ID
fsq runs logs RUN_ID
fsq runs show RUN_ID --open
```

最后一个命令基于已保存的 Run 事实创建离线报告。它不会操作目标 UI，也不会调用 Provider。

## 分析确定性 Run

```bash
fsq case test --platform web --suggest cases/web/example-domain.fsq.yaml
```

Case 只执行一次。随后 AI 分析只消费源 Case、报告和已保存证据。建议和任何候选 Case 都只保留在对应 Run 内。

## 打开 Control Plane

```bash
fsq ui
```

安装后的前端默认在本地 `127.0.0.1:8879` 提供服务。

## 下一步

- 阅读[平台前置条件](platform-prerequisites.md)。
- 学习 [Case 格式](case-format.md)。
- 查看 [CLI reference](cli-reference.md)。
- 实现级架构与行为契约请查阅根目录及各模块的 `SPEC.md` 文件。
