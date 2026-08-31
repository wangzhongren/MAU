# mau-flow

把一个任务拆成一条由多个小型 Agent 组成的执行链，并让这条链可以先查看、再运行。

例如，你想完成一个还没写完的 Python 文件：

```powershell
mau-flow app.py --task "完成接口实现并补齐测试"
```

`mau-flow` 会先阅读任务和目标文件，再根据实际复杂度生成一条链：

```text
inspect -> implement -> test -> security_review -> verify
```

简单任务可能只需要两个或三个节点，复杂任务可以有五个或更多。生成阶段不会修改代码；链会保存为 `.mau-flow-chain.xml`，你可以先检查或编辑它，再决定是否执行：

```powershell
mau-flow start
```

## MAU 是什么？

MAU 是 **Minimal Agent Unit**，即“最小 Agent 单元”。

一个 MAU 不负责包办整个任务，只负责一个边界清楚的环节。例如：

- `inspect` 只检查现状并整理问题；
- `implement` 只完成修改；
- `test` 只补充和运行测试；
- `verify` 独立复核结果。

每个 MAU 都有独立的短期上下文、允许执行的指令和最大运行轮数。完成后，它只把经过 Pydantic 校验的 Handoff 交给下一个 MAU，而不是把整段对话无限传递下去。

这种拆分让模型的职责更聚焦，也让流程、权限和失败位置更容易检查。

## 安装

要求 Python 3.10 或更高版本。

```powershell
git clone https://github.com/wangzhongren/MAU.git
cd MAU
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev,openai]"
pytest
```

不接入模型也可以运行离线示例：

```powershell
python examples/quickstart.py
```

## 配置模型

项目支持 OpenAI Chat Completions 兼容接口。在根目录创建 `.env`：

```dotenv
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://api.openai.com/v1
MODEL_NAME=your-model
MAX_OUTPUT_TOKENS=32768
```

也可以让项目 `.env` 只保存一个外部配置文件指针：

```dotenv
MAU_FLOW_ENV_FILE=C:\path\outside\the\repository\.env
```

`.env` 和 `.env.*` 默认被 Git 忽略，只有 `.env.example` 会进入版本控制。

## 使用 CLI

### 为一个文件生成执行链

文件可以已经存在，也可以是准备创建的新文件：

```powershell
mau-flow src\report.py --task "实现 CSV 汇总并添加异常输入测试"
```

默认会在目标文件所在目录生成 `.mau-flow-chain.xml`。

### 为当前目录生成执行链

```powershell
mau-flow --task "检查这个项目并修复测试失败"
```

### 控制链的规模

模型会动态决定实际需要多少个 MAU，`--max-agents` 只设置外部上限：

```powershell
mau-flow app.py --task "完成并审查这个模块" --max-agents 5
```

### 运行已经生成的链

```powershell
mau-flow start
```

也可以指定链文件，并限制每个 MAU 最多运行多少轮：

```powershell
mau-flow start --chain path\to\.mau-flow-chain.xml --max-rounds 12
```

生成和执行是两个独立阶段，因此链文件可以被审阅、修改、保存或纳入其他工作流。

## MAU-ISA

MAU 与执行器之间使用 **MAU-ISA（MAU Instruction Set Architecture）**。

它借用了处理器指令集的概念：MAU 像一个执行特定职责的 Core，执行器负责解码并运行 opcode。目前只有五条基础指令：

```text
create  read  update  delete  shell
```

模型直接输出对应指令，不使用厂商专用的 `tool_call` 格式：

```xml
<read>
  <path>src/app.py</path>
</read>
```

```xml
<update>
  <path>src/app.py</path>
  <content><![CDATA[
print("updated")
]]></content>
</update>
```

完成当前职责时，MAU 输出 Handoff：

```xml
<handoff status="SUCCESS">
  <summary>实现完成，测试已通过</summary>
</handoff>
```

```text
MAU Core -> decode instruction -> Execution Unit -> execute opcode -> result
```

每个 MAU 只能使用链文件授予的 opcode。如果最后一轮仍未主动提交 Handoff，运行时会生成 `PARTIAL` Handoff，将控制权交给下一个节点，而不会让整条链无限运行。

## Python API

Planner 通过 `PlannerProtocol` 注入，因此框架不绑定某个模型厂商。实现 `plan(messages) -> PlannerDecision` 即可接入其他模型或测试替身。

最大轮数由调用方在每次运行时传入，不保存在 MAU 内部：

```python
result = mau.run(handoff, max_rounds=10)

result = pipeline.execute(
    handoff,
    start="inspect",
    max_rounds_per_agent=10,
)
```

模型配置也可以从 Python 中读取：

```python
from mau_flow import OpenAISettings

settings = OpenAISettings.from_environment()
```

## 运行流程

```text
task/file
   |
   v
DynamicChainGenerator
   |
   v
.mau-flow-chain.xml
   |
   v
MAU -> typed Handoff -> MAU -> ... -> final Handoff
   |                         |
   +---- MAU-ISA ------------+
             |
             v
       SharedExecutor
```

框架负责以下确定性边界：

- Pydantic Handoff、状态和 Artifact 契约；
- 动态链的保存、加载和顺序执行；
- 每个 MAU 的 opcode 白名单；
- 调用方提供的轮数上限；
- 验证失败反馈和 `PARTIAL` 兜底；
- Pipeline 环路保护；
- Artifact SHA-256 完整性校验；
- 文件指令的路径逃逸防护。

## 项目结构

```text
src/mau_flow/       框架、CLI、MAU-ISA 和模型适配器
tests/              单元与集成测试
examples/           离线及真实模型示例
```

## 安全边界

`create`、`read`、`update` 和 `delete` 会阻止文件路径逃出工作区。

`shell` 以工作区作为当前目录并带有超时，但当前目录并不是操作系统级沙箱。面向不可信任务或生产环境时，仍应增加容器隔离、权限审批、命令策略、资源配额和审计。
