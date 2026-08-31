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

使用真实模型时安装兼容适配器：

```powershell
pip install -e ".[openai]"
```

## CLI

生成动态 MAU 链，但不立即执行：

```powershell
mau-flow path\to\target.py --task "完成 TODO 并补齐测试"
```

不指定文件时，以当前目录为任务工作区：

```powershell
mau-flow --task "检查并修复这个项目"
```

生成器会根据任务复杂度决定链长；简单任务可能只需要少量 MAU，复杂任务可以拆成更多专业节点。任务、目标文件、每个 MAU 的职责和 MAU-ISA opcode 权限会保存到工作区的 `.mau-flow-chain.xml`。确认或编辑链文件后运行：

```powershell
mau-flow start
# 或指定链文件和每个 MAU 的外部轮数上限
mau-flow start --chain path\to\.mau-flow-chain.xml --max-rounds 12
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

模型与执行器之间使用 **MAU-ISA（MAU Instruction Set Architecture）**。模型输出五种 opcode 指令，不使用 `tool_call` 或模型厂商的函数调用格式：

```xml
<create>
  <path>hello.py</path>
  <content>print('hello')</content>
</create>
```

也可以通过 `PlannerDecision.execute("create", path="hello.py", content="...")` 安全构造。MAU-ISA 内置 opcode 固定为 `create`、`read`、`update`、`delete`、`shell` 五个。

```text
MAU Core -> decode instruction -> Execution Unit -> execute opcode -> result
```

模型完成当前职责时输出经过校验的 Handoff；若模型在最后一轮仍继续执行 opcode，运行时会确定性生成 `PARTIAL` Handoff，把控制权交给链中的下一个 MAU。

Agent 最大轮数不保存在 MAU 内部，而由工作流调用方逐次传入：

```python
pipeline.execute(handoff, start="coder", max_rounds_per_agent=10)
# 或 mau.run(handoff, max_rounds=10)
```

## 配置文件

可以直接在项目根目录创建不会被 Git 跟踪的 `.env`：

```dotenv
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://api.openai.com/v1
MODEL_NAME=your-model
MAX_OUTPUT_TOKENS=32768
```

也可以让项目 `.env` 只保存 `MAU_FLOW_ENV_FILE`，指向仓库外部的密钥文件。

代码中可安全加载并读取配置：

```python
from mau_flow import OpenAISettings

settings = OpenAISettings.from_environment()
# settings.api_key / settings.base_url / settings.model / settings.max_output_tokens
```

模型输出上限默认为 32K tokens，可在 `.env` 中通过 `MAX_OUTPUT_TOKENS=32768` 覆盖。该预算会同时用于动态链生成和每一轮 MAU 模型调用；对于带内部思考的兼容模型，服务端通常也会从该完成预算中计算思考内容。

`.env` 和 `.env.*` 默认被 Git 忽略，仅 `.env.example` 会进入版本控制。不要把真实 API Key 写入 README、源码或 `.env.example`。

## 当前能力

- Pydantic Handoff、状态和 Artifact 强类型契约
- 本地 Artifact Store，SHA-256 完整性校验
- 路径逃逸防护的共享无状态执行器
- `PLAN -> EXECUTE -> VALIDATE -> HANDOFF` 状态机
- 调用方传入的最大轮数限制、opcode 白名单、验证失败反馈
- 根据文件与任务复杂度动态生成、持久化和执行 MAU 链
- OpenAI 兼容模型适配器及 32K 默认输出预算
- 支持条件边的确定性工作流及环路步数保护
- 无需网络/模型即可运行的示例与单元测试

## 项目布局

```text
src/mau_flow/       框架实现
tests/              单元与集成测试
examples/           离线快速开始
```

## 安全说明

`create/read/update/delete` 会阻止路径逃逸。`shell` 以 `sandbox_dir` 作为工作目录并带有超时，但它仍是任意命令执行能力；工作目录不是安全沙箱。生产环境必须额外使用容器或操作系统隔离、权限审批、资源配额和审计。
