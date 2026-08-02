# AI Project Factory Demo

这是一个可运行的跨模型 AI Project Factory 原型。它验证的不是“某个模型会不
会记住聊天”，而是下面这条工程链路：

```text
一键创建项目
→ Codex 创建真实任务和标准首轮并立即打开
→ 可见首轮只生成快速启动卡
→ 用户回复“继续”，在可见任务中开始真实访谈
→ 同一聊天完成 Discussion
→ 将共识固化为 Contract / Context / Decisions / Active Goal
→ Goal 模式持续执行并吸收 steering
→ Handoff 支持 compact、换聊天和换 Agent
→ Codex / Claude 读取同一事实层
→ 裸 API 使用白名单 Context Bundle
```

## 直接运行

在 Windows 中双击：

[`AI Project Factory.cmd`](AI%20Project%20Factory.cmd)

启动器会先检查 Python 版本与 Tkinter，再使用 `pyw`/`pythonw` 启动；正常
情况下不会要求用户操作终端。启动失败时会弹出诊断日志位置。需要 Python
3.10+；Demo 只使用标准库和 Tkinter。

跨机器时优先使用发布目录中的 `AI-Project-Factory-Portable-v0.5.3.zip`：
解压后仍然双击同名 CMD，不需要安装 Python 包。wheel 作为需要安装到 Python
环境时的补充发布方式。

### 固定桌面入口

首次安装或源码更新后双击：

[`Install or Update Desktop Shortcut.cmd`](Install%20or%20Update%20Desktop%20Shortcut.cmd)

它会把经过 GUI smoke 验证的 payload 部署到
`%LOCALAPPDATA%\AI Project Factory\current`，并创建唯一且不带版本号的
`AI Project Factory.lnk`。快捷方式只指向固定 launcher；以后 Core、GUI、
启动器和正式图标更新时，重新运行同一部署器即可原地切换 `current`，无需重建
或更换桌面入口。部署失败会保留上一份可用版本；同名但并非 Factory 管理的
快捷方式不会被静默覆盖。

这是本地、显式的稳定更新通道，不是联网后台自动更新器。Factory 的后续变更在
交付前必须运行 `scripts/deploy_windows_desktop.py` 并复核同一快捷方式的目标、
图标和稳定目录冷启动。

GUI 提供：

- 新建项目；
- “创建并启动 Codex 讨论”，一次完成安全创建、隐藏子进程、真实任务与标准
  首轮创建，并在首轮开始后立即打开；
- Codex App Server 不可用时诚实退回“打开并预填，用户手动发送”；
- 可选填写“初始想法”，只作为访谈种子，不冒充已经批准的 Contract；
- 项目控制台自动发现默认目录中的 Factory 项目，并显示模式、Goal、Handoff
  revision 与更新时间；
- 左侧高对比导航替代容易误判的相邻标签页；
- 用普通语言查看 Discussion/Goal、Handoff 与下一动作；
- 完整验证；
- 刷新 compact/换 Agent 前的交接检查点；
- 一键复制启动、准备切换和新 Agent 接管提示，并带上所选本地项目路径；
- 选择位置导出网页/API Context Bundle；
- 打开项目目录；
- 从同一个 Skill 源安装本机 Codex 与 Claude Code 集成。

## 正确的使用流程

### 1. 创建

GUI 只询问项目名称、位置、宽泛 Profile，以及可选的“初始想法”。点击“创建并
启动 Codex 讨论”后，Factory 会先在隐藏控制台中完成原子创建与校验，再使用
本机 Codex App Server 创建真实任务和一个标准 Codex 首轮。首轮用户消息包含
完整原始输入，但启动模型被严格限定为只回复一张明确标注的 Factory 启动卡，
不读文件、不调用工具。首轮一开始，Factory 就通过官方
`codex://threads/<id>` 打开任务；用户可以在 Codex 中看到它生成，而不需要等
Factory 在后台完成整段项目分析。新项目一定从：

```text
mode: discussion
goal_status: none
```

开始。Factory 不会猜测技术栈、项目事实或验收条件。

### 2. 启动讨论

Codex 用户直接点击“启动 / 继续 Codex 任务”。Factory 会优先创建绑定到所选
目录的真实任务，把“先读 `AI_START_HERE.md`”和初始想法作为标准首轮用户消息
发送。`turn/start` 成功后任务立即打开；启动卡通常约 10–20 秒生成完成。这个
首轮是真实、可渲染的 `userMessage + agentMessage`，不会再出现只有隐藏注入项、
右侧整片空白的任务。

启动卡明确说明它不是项目研究结论，并且本轮没有读取文件或调用工具。用户回复
“继续”后，正常 Codex 宿主必须具体复述初始想法，不能再泛化询问“你想做
什么”；真实访谈、Token Bridge、登录、连接器及必要审批也在这个可见任务中
发生。
如果本机 App Server 不可用，Factory 才退回官方预填草稿入口，并把提示写入
剪贴板，界面会明确要求用户手动发送，不再把草稿说成已创建聊天。

Claude Code 或其他本地 Agent 仍然打开同一生成目录，再使用“复制：启动 /
继续”。复制内容会带上当前选中的本地项目路径，降低粘贴到错误任务或错误目录
的风险。讨论阶段可以研究、pushback、比较路线和做可逆原型。

### 3. Discussion Commit

用户明确批准“开始做”后，Agent依次更新：

- `PROJECT_CONTRACT.md`
- `PROJECT_CONTEXT.md`
- `DECISIONS.md`
- `ACTIVE_GOAL.md`
- `HANDOFF.md`

然后由 Agent 运行（普通用户不需要打开终端）：

```text
python .ai/project_runtime.py commit-discussion --updated-by <agent>
```

只有全部文件通过校验后，状态才最后切到 `goal / active`。验证失败时仍保持
Discussion，避免半套契约。

### 4. Goal 与 steering

Goal 中普通新消息默认是 steering，Agent吸收后继续，不询问“是否继续”。
只有改变未来工作的 steering 才需要持久记录；下面的命令同样由 Agent 执行：

```text
python .ai/project_runtime.py steer "新增一份 HTML 输出" --updated-by <agent>
```

如果 steering 同时改变交付物、硬约束或验收条件，先把
`PROJECT_CONTRACT.md` revision 加一并记录理由，再运行 `steer`；Core 会把
新的 Contract revision 与 Goal revision 一起纳入检查点，但仍保持
`goal / active`。

暂停、契约失效、真实阻塞和完成分别使用 `pause`、`invalidate`、`block`、
`complete`。完成后回到 `discussion / completed` 并停止。

### 5. compact 与迁移

Agent 先把当前事实、证据、风险和下一步写入短 `HANDOFF.md`，再自行运行：

```text
python .ai/project_runtime.py checkpoint --updated-by <agent>
python .ai/project_runtime.py doctor
```

这里的“需要更新”指实质事件：模式切换、Contract/Goal 改变、可验证里程碑、
真实阻塞、准备 compact 或准备换 Agent。普通尝试、局部优化和每轮聊天不写
Handoff，避免让维护记忆反过来占用主要注意力。

GUI 的“刷新交接检查点”只刷新 revision 和 fingerprints，不会假装理解聊天中
尚未落盘的语义。先点击“复制：准备切换”，让当前 Agent 更新语义 Handoff。

Codex 与本机 Claude Code 之间切换时，直接打开同一个完整项目目录，不需要
导出文件。裸网页/API 模型无法读取本地目录，此时才由 Agent 使用：

```text
python .ai/project_runtime.py export
```

导出包只包含项目级白名单文件，不会赋予 API 模型本地文件或工具能力。

## 生成项目中的事实层

| 文件 | 角色 |
|---|---|
| `CONSTITUTION.md` | 创建时固定版本的跨项目原则 |
| `PROJECT_CONTRACT.md` | 项目结果、范围、硬约束、验收与授权 |
| `PROJECT_CONTEXT.md` | 稳定且已验证的背景 |
| `DECISIONS.md` | 追加式持久决策与被否决方案 |
| `ACTIVE_GOAL.md` | 当前中短期目标及持久 steering |
| `HANDOFF.md` | 面向冷启动的短状态收据 |
| `AI_PROJECT.json` | 模式、状态、版本和 revision，不复制语义正文 |
| `ARTIFACTS.md` | 重要二进制、忽略或外部成果的版本与哈希 |
| `AGENTS.md` / `CLAUDE.md` | 指向共同入口的薄适配器 |
| `.ai/project_runtime.py` | Discussion/Goal 状态机和 checkpoint |
| `.ai/project_memory.py` | 指纹、深度检查、密钥扫描和白名单导出 |

## 唯一核心与薄 Skill

Factory Core 位于 `src/ai_project_factory/`。GUI、命令接口和
`src/ai_project_factory/resources/agent-skills/ai-project-factory/` 中随包发布的
Skill 都调用这个核心，不各自保存模板或状态机。

同步适配器时，Factory 将同一 Skill 源原子部署到运行时目录，并在很薄的
bridge 中记录 Core 位置。后续模板和状态机更新只发生在 Core；Skill 不需要
复制模板或状态机。

Codex/Claude 双目标同步使用 OS 文件锁和跨目录恢复日志。同步前记录原版本与
预期哈希；若进程在两个目录替换之间被强制结束，下一次同步会在没有人工修改时
整体回滚后重试。若崩溃后有人编辑过目标，恢复会停止并保留现场。这里验证的是
进程被终止后的恢复，不承诺机器突然断电时所有文件系统缓存都已经落盘。

生命周期命令由项目级跨进程锁串行化。开始修改前会写恢复日志；进程崩溃后，
下一条命令只在文件仍是事务已知版本时自动回滚。若崩溃后有人手工编辑过文件，
恢复会停下来保留现场，而不是静默覆盖新编辑。

Demo 默认不会修改用户的全局 Codex/Claude 配置。只有用户在 GUI 中明确点击
“安装本机 Agent 集成”，才会写入 `~/.agents/skills` 与
`~/.claude/skills`；同名但不是 Factory 管理的 Skill 不会被覆盖。该操作只对
本机运行时生效，不会把 Skill 传到 Claude Cloud/Cowork 或其他云端会话。

勾选“初始化 Git”只会建立本地仓库，不会冒充用户创建提交。第一次 Discussion
Commit 后应由 Agent 提醒建立 Git 基线；在此之前，迁移时要复制完整项目目录，
不能只依赖 `git clone`。

## 自动验证

```text
python -m unittest discover -s tests -v
python run_factory.py gui --smoke-test
python <skill-creator>/scripts/quick_validate.py src/ai_project_factory/resources/agent-skills/ai-project-factory
```

当前 82 项测试覆盖安全创建、拒绝覆盖、Discussion 门禁、Goal steering、
暂停/恢复/完成、Handoff 新鲜度、compact 检查点、进程硬退出与原子写残留恢复、
并发锁、Git 可执行位与冲突索引指纹、迁移包一致快照、密钥拦截，以及两个
运行时的同源 Skill 事务同步。故障注入还覆盖 phase 副本不齐后再次崩溃、临时
快照清理一半、符号链接/Windows junction no-follow、人工编辑保护和畸形日志；
GUI 失败回调与紧凑窗口也有独立回归。从便携 ZIP 解压后的 GUI、创建和 doctor
冷启动同样经过验证；正式 H2 矢量资产的确定性构建、多尺寸 ICO 光学校准、
内容寻址图标更新，以及 wheel 在全新离线环境中的安装与 bridge 冷启动也有
独立回归。固定 `launch.vbs` 还会在每次部署前由真实 Windows Script Host
编译并执行一个带空格路径的无害 stub，避免 GUI smoke 绕过桌面启动链路。
侧边导航的明确选中态、创建成功后自动进入项目控制台、忙碌期间禁止关闭、
最近项目发现、初始想法的“未批准输入”标记，以及真实 App Server 的
initialize → thread/start → thread/name/set → turn/start → completed 流程均有
独立回归。首轮启动失败会删除尚未形成有效对话的空任务，避免再次留下
`No chats` 验收残留；深链草稿仍覆盖中文/空格路径编码和协议缺失错误。

## Demo 的诚实边界

- 不承诺所有运行时都提供可靠的 pre-compact hook，因此平时的关键事件检查点和
  显式“准备 compact”仍然必要。
- GUI 无法读取或总结 Codex/Claude 的聊天；语义 Handoff 由 Agent 负责，
  Core 负责原子状态、revision、fingerprint 和验证。
- 当前 Codex 路径使用本机 App Server 创建并发送真实首轮任务，不使用键盘
  模拟。若当前 Codex 版本、认证或 App Server 不可用，会退回预填草稿并明确
  要求手动发送；不会把退化路径伪装成聊天已创建。
- App Server 启动确认有 45 秒上限；超时会中断并删除未完成的启动任务，再诚实
  退回预填草稿，避免留下空白任务或让 Factory 永久挂起。
- 裸 DeepSeek/Kimi API 只能接收导出包。要让它们直接修改本地文件，后续仍需
  本地 Agent Host 或 MCP 工具层。
- 本机安装的 Skill bridge 会记录当前 Factory Core 与 Python 路径。移动、
  删除 Factory 或切换 Python 环境后，应从新位置再次点击“安装本机 Agent
  集成”；它不是脱离 Core 独立运行的云端插件。
- Codex 与 Claude 对“同名的用户级和项目级 Skill”采用不同优先/展示行为；
  Demo 当前只安装用户级 Skill，不承诺与未来手工创建的同名项目级 Skill 合并。
- Demo 没有实现云同步、复杂任务数据库、并发 Handoff 合并和自动升级迁移；
  这些都不影响本轮对核心架构的验证。
