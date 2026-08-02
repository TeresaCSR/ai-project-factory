# AI Project Factory v0.5.3 Validation

验证日期：2026-08-02  
验证环境：Windows、Python 3.13、Codex CLI 0.146.0；项目声明支持
Python 3.10+。

## 结论

v0.5.3 同时修复第三次 `元妙宇宙` 真实试用暴露的两个缺陷：

1. v0.5.2 使用 `thread/inject_items` 写入原始输入和启动卡，却没有创建标准
   Codex turn。程序注入项存在于模型历史，但 Codex Desktop 的 `turns=[]`，
   因而用户打开任务时右侧整片空白。
2. `CREATE_NO_WINDOW` 没有覆盖 Codex 及其内部 Git 的全部控制台创建路径，
   用户仍会看到一个大黑框及多次闪烁。

当前实现改为一个严格受限的真实启动轮：标准 `userMessage` 保存完整项目输入，
模型本轮只原样回复启动卡，不读取文件、不调用工具。`turn/start` 成功后任务
立即在 Codex Desktop 打开，用户能看到生成过程；正常实测约 10 秒完成。回复
“继续”后才开始真实访谈和工具调用。

Windows 辅助进程统一使用 `CREATE_NEW_CONSOLE + STARTF_USESHOWWINDOW/SW_HIDE`，
确保控制台从第一帧起隐藏。20 ms 频率的全进程树与顶层窗口采样覆盖项目创建、
Git、Codex App Server 及模型启动，记录到的可见窗口数为 0。

## 真实问题取证

| 观察 | 当前证据 | 判断 |
|---|---|---|
| v0.5.2 任务打开后右侧空白 | 保存的 session 含注入 user/assistant item，但 App 读取为 `turns=[]` | `thread/inject_items` 不能替代 Desktop 可渲染的真实 turn |
| 同名任务似乎存在却无法交流 | 任务状态 idle、无标准首轮 | Factory 把“历史已注入”误报为“任务已就绪” |
| 仍出现大黑框及多次闪烁 | `CREATE_NO_WINDOW` 进程追踪仍出现 Codex/Git 的 conhost | 旧隐藏策略不覆盖完整子进程树 |
| 当前真实 `元妙宇宙` 项目仍存在并在独立推进 | 最终只读复核仍为 `discussion / none`，Handoff 已由另一任务推进 | 项目本体有效且未被本次修复删除、覆盖或重建 |

## v0.5.3 修复

| 缺陷 | 修复 | 自动化或实机证据 |
|---|---|---|
| 注入历史不渲染、任务空白 | 使用标准 `turn/start`；真实 user/agent 两项形成一个 turn | 持久内部任务读回 `turn_count=1`、`item_count=2`，类型为 `userMessage` / `agentMessage`；随后自动删除 |
| 重新引入长时间隐藏推理 | 启动轮只输出固定卡，不读文件、不用工具；创建 turn 后立即打开 | 实机端到端 10.058 s；用户可在 Codex 中观看 |
| 控制台黑框/闪烁 | Factory 自有 helper 从创建起使用隐藏新控制台；App Server 不再额外运行 `codex mcp list` | 20 ms 进程树采样 `visible_windows=[]` |
| 临时宿主加载用户工具 | 禁用 plugins、apps、shell、code-mode、in-app-browser，以及两个桌面内建 MCP | 实机首轮无外部工具审批，启动卡严格按预期输出 |
| 启动失败留下垃圾任务 | 超时或非 completed 状态先中断，再删除未完成任务并退回预填草稿 | 故障注入回归 |
| 用户等待任务出现在 Recents | `turn/start` 返回后马上打开深链，不等待模型完成 | 打开顺序回归：create → title → turn/start → open → completed |

## 自动化与实机验证

| 项目 | 结果 | 证据 |
|---|---|---|
| 全量单元与故障注入 | PASS | `python -X utf8 -B -m unittest discover -s tests -v`，82/82，133.929 s |
| 启动链与 GUI 专项 | PASS | 23/23；真实 turn、打开时序、隐藏控制台、失败清理、GUI 解锁 |
| 真实 App Server ephemeral | PASS | `completed`；10.058 s；临时目录已删除 |
| 标准首轮读回 | PASS | 1 turn、2 items；agent 启动卡内容与预期一致；内部任务随后删除 |
| 进程子树与窗口追踪 | PASS | 20 ms 采样；Codex/Git/conhost 均被覆盖；0 个可见窗口 |
| GUI smoke | PASS | source 与已安装 `current` 均 exit 0 |
| Skill 结构 | PASS | `quick_validate.py` 返回 `Skill is valid!` |
| Python 3.10 grammar | PASS | 22 个 Python/PYW 候选文件，0 failure |
| wheel / ZIP 回归 | PASS | 确定性构建、manifest、全新 venv 与解压冷启动 |
| 桌面部署 | PASS | 连续部署两次；48 个文件；同一个稳定快捷方式与 H2 图标 |

## 正式发行

- 版本：`0.5.3`
- wheel：`ai_project_factory_demo-0.5.3-py3-none-any.whl`
- portable：`AI-Project-Factory-Portable-v0.5.3.zip`
- 桌面通道：`%LOCALAPPDATA%\AI Project Factory\current`
- 快捷方式：同一个未版本化的 `Desktop\AI Project Factory.lnk`
- wheel：68,674 bytes，
  SHA-256 `56081d34e8bdced3a5e38908bcc1c6af991d9c0927ae8b67435454445453652c`
- ZIP：177,054 bytes，
  SHA-256 `102f0cd3e0d25c36039c0df5eff3f082d07d92b83249ba4c9871dbd4e3f22239`

## 当前真实项目与边界

1. 用户当前的 `Documents\AI Projects\元妙宇宙` 没有被拿来做修复测试；最终
   只读复核确认它仍存在，并已由用户的另一任务继续推进。v0.5.2 最初的注入项
   不会被自动补成可见历史气泡，但不影响后续正常轮次继续工作。
2. v0.5.3 只保证今后新建或重新启动的任务生成标准可见首轮，不回写既有任务。
3. 启动卡仍需要一次短模型生成，实测约 10 秒；与 v0.5.1 的约 153 秒完整项目
   分析不同，这一轮只做交接确认，并且任务已打开、过程可见。
4. 当前完整矩阵运行于 Python 3.13；Python 3.10 通过语法检查，但未运行完整矩阵。
5. 仓库仍为 `UNBORN`；首次提交前跨机器迁移必须复制完整目录。
