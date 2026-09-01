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

执行时默认给每个 MAU 创建独立的事务快照：

```powershell
mau-flow start --sandbox snapshot
```

MAU 的文件和 Shell 操作只影响自己的临时副本。只有 Handoff 为 `SUCCESS`，且最终
Diff 符合该 MAU 的 `create`、`update`、`delete` 权限时，变更才会合并回主工作区。
`PARTIAL`、`FAILED`、越权删除、敏感文件修改和并发冲突都会丢弃整个临时副本。

如需兼容旧行为，可以显式关闭快照隔离：

```powershell
mau-flow start --sandbox host
```

`shell` 指令默认逐条请求批准。非交互环境会拒绝未预授权的命令；如果要完全禁用
交互式批准，可以使用：

```powershell
mau-flow start --approval-mode never
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

`shell` 使用结构化参数，不会把模型输出交给系统 Shell 解释：

```xml
<shell>
  <program>python</program>
  <arg>-m</arg>
  <arg>pytest</arg>
  <cwd>.</cwd>
  <timeout_seconds>60</timeout_seconds>
  <reason>运行测试套件</reason>
</shell>
```

管道、重定向、分号和命令替换不会被解释。CLI 会在执行前显示解析后的可执行文件、
参数、工作目录、原因和超时，并要求用户明确批准。

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

Python 调用方可以提供审批回调，或者使用参数前缀规则预授权命令：

```python
from mau_flow import CommandRule, SharedExecutor

executor = SharedExecutor(
    workspace,
    shell_rules=[CommandRule("python", ("-m", "pytest"))],
)
```

没有匹配规则或审批回调时，`SharedExecutor` 默认拒绝所有 `shell` 指令。不要使用
`CommandRule("python")` 这类过宽规则，因为它也会允许 `python -c` 执行任意代码。

需要每个 MAU 独立修改副本时，可以直接使用事务执行器：

```python
from mau_flow import TransactionalExecutor

executor = TransactionalExecutor(
    workspace,
    allowed_tools={"create", "read", "update", "shell"},
    shell_approver=approve_shell,
)
```

运行结束后，`finalize(Status.SUCCESS)` 会检查并合并 Diff；其他状态只返回 Diff 元数据并
回滚。Pipeline 会自动完成这个生命周期，无需 Python 调用方手工 finalize。

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
- `shell=False` 的结构化进程执行和逐条授权；
- 子进程最小环境变量、输出上限和进程组超时终止。
- 每个 MAU 独立的 copy-on-write 工作区快照；
- 按 opcode 能力审核的 Diff Gate、失败回滚和并发修改检测。

## 项目结构

```text
src/mau_flow/       框架、CLI、MAU-ISA 和模型适配器
tests/              单元与集成测试
examples/           离线及真实模型示例
```

## 安全边界

`create`、`read`、`update` 和 `delete` 会阻止文件路径逃出工作区。

`shell` 默认拒绝执行；CLI 采用逐条人工批准，Python API 可以配置审批回调或
`CommandRule`。命令通过结构化 argv 运行，不会调用系统 Shell，并且工作目录不能逃出
工作区。子进程只继承最小环境变量，输出有大小限制，超时会终止整个进程组。

事务快照保护主工作区，不等同于操作系统级沙箱。用户批准的程序（包括 `pytest`、
`npm test` 等）仍在宿主机运行，可能读取工作区外的文件；但其工作区内修改只有通过
Diff Gate 才会合并。当前模式适合本地、可信仓库和人工审批场景。面向不可信代码或
无人值守生产环境时，仍应在外层增加操作系统隔离、网络控制和资源配额。
