# mau-flow

`mau-flow` 是一个把 Minimal Agent Unit（MAU）落成 Python 工程的轻量框架。它坚持三个边界：

- 控制流代码化：工作流、分支和循环由确定性 Python 代码控制。
- 认知流局部化：每个 MAU 只拥有自己的短期上下文。
- 数据交换契约化：单元之间只传递经过 Pydantic 校验的 Handoff 和 Artifact 引用。

## 快速开始

```powershell
cd mau-flow
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
python examples/quickstart.py
```

## 核心概念

```text
Pipeline (deterministic routing)
  -> MAU (local planner loop)
       -> SharedExecutor (controlled side effects)
       -> Validators (deterministic quality gates)
  -> typed BaseHandoff (bounded context)
       -> ArtifactRef (large payload stays outside prompts)
```

Planner 通过 `PlannerProtocol` 注入，因此框架不绑定任何模型厂商。示例使用完全离线的脚本化 Planner；接入 OpenAI、私有模型或测试替身时，只需实现 `plan(messages) -> PlannerDecision`。

## 配置文件

项目根目录的 `.env` 只保存 `MAU_FLOW_ENV_FILE` 指针，真实密钥仍保存在外部文件，不会被复制进仓库。当前已指向：

```text
C:\Users\MLoong\Desktop\.env
```

代码中可安全加载并读取配置：

```python
from mau_flow import OpenAISettings

settings = OpenAISettings.from_environment()
# settings.api_key / settings.base_url / settings.model
```

`.env` 和 `.env.*` 默认被 Git 忽略，仅 `.env.example` 会进入版本控制。若要启用官方 OpenAI SDK 适配器，可安装可选依赖：`pip install -e ".[openai]"`。

## 当前能力

- Pydantic Handoff、状态和 Artifact 强类型契约
- 本地 Artifact Store，SHA-256 完整性校验
- 路径逃逸防护的共享无状态执行器
- `PLAN -> EXECUTE -> VALIDATE -> HANDOFF` 状态机
- 最大步数限制、工具白名单、验证失败反馈
- 支持条件边的确定性工作流及环路步数保护
- 无需网络/模型即可运行的示例与单元测试

## 项目布局

```text
src/mau_flow/       框架实现
tests/              单元与集成测试
examples/           离线快速开始
```

## 安全说明

内置执行器有意不提供任意 shell 命令执行。生产环境应将新工具显式注册并进行参数校验、权限审批、超时、资源配额和审计。`sandbox_dir` 只限制内置文件工具，不等价于操作系统级容器隔离。
