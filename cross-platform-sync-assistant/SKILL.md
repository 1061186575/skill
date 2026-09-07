---
name: cross-platform-sync-assistant
description: 自动把源项目当前分支的业务改动跨端同步到目标项目。默认按每个源分支与目标项目的上次成功同步 hash 做增量 diff，也支持用户主动要求全量 diff；用脚本统一完成目标项目同名分支管理、diff 汇聚、临时目录 checkpoint 状态读写。适用于 PC 到 H5、小程序等跨端功能复刻。
version: 2.0.0
author: zhoupeng
tags: [跨端开发, 增量同步, 代码同步, git差异分析, 自动化开发]
---

# Role

你是跨端需求同步专家。任务是从源项目当前分支读取业务改动，并在目标项目中按目标端技术栈复刻同等业务功能。

硬性原则：
- 只读源项目，不修改源项目代码。
- 只在目标项目中开发。
- 复刻业务逻辑、接口语义、校验规则和交互结果，不直接复制源项目代码。
- 遵循目标项目已有技术栈、目录结构、组件库、请求封装和代码风格。

# Trigger

用户表达以下意图时使用本 Skill：
- 跨端同步、同步当前分支到 H5/小程序。
- 把 PC 改动同步到 `home-h5` 或其他目标项目。
- PC 开发完成，要求实现目标端对应功能。
- 对比 master 差异并复刻到跨端项目。
- 明确要求“增量同步”“只同步上次之后的 diff”“全量同步当前分支 diff”。

# Inputs

- 源项目：当前工作目录。
- 目标项目：优先使用用户指定路径；未指定时默认 `../home-h5`。
- diff 模式：
  - 默认：增量 diff。首次增量或 checkpoint 不可用时保持全量行为，汇聚 `origin/master...HEAD`。
  - 后续增量：只汇聚上次成功同步 hash 之后当前分支 first-parent 主线上的非 merge commit，并排除 `origin/master` 已可达提交，避免携带 master 新合入代码。
  - 用户明确说“全量 / 重新全量 / 从 master 全部同步 / 忽略 checkpoint”时：全量 diff，汇聚 `origin/master...HEAD`。

# Required Script

先使用脚本完成准备工作，不要手动逐条执行分支管理和 diff 命令。

脚本路径：
```bash
scripts/prepare_cross_platform_sync.py
```

路径解析规则：脚本路径必须按本 `SKILL.md` 所在目录解析，记为 `SKILL_DIR`。

执行前先根据当前加载的 `SKILL.md` 绝对路径设置：
```bash
SKILL_DIR="$(cd "$(dirname "<SKILL.md绝对路径>")" && pwd)"
```

默认增量准备：
```bash
python3 "$SKILL_DIR/scripts/prepare_cross_platform_sync.py" prepare --target ../home-h5
```

主动全量准备：
```bash
python3 "$SKILL_DIR/scripts/prepare_cross_platform_sync.py" prepare --target ../home-h5 --mode full
```

脚本职责：
- 获取源项目当前分支。
- 在目标项目中切换或创建严格同名分支。
- 对已有目标分支执行 `git pull --ff-only`；无同名分支时基于目标项目 `master` 创建。
- 默认读取系统临时目录中的 checkpoint；首次增量保持全量 diff 行为，后续增量按当前分支自身 commit 生成 diff bundle，排除 master 合入代码。
- 全量模式忽略 checkpoint，汇聚 `origin/master...HEAD`。
- 将 diff bundle 写入系统临时目录。
- 将“本次已准备 diff 到哪个 hash”写入临时目录状态文件。
- 输出 JSON，包含 diff 路径、变更文件、目标分支操作、checkpoint、后续 `mark-success` 命令。

状态文件位置由脚本决定，默认为：
```text
$HOME/cross_platform_sync_assistant/diff_checkpoints.json
```

重要：只有目标端实现完成并验证后，才能执行输出 JSON 中的 `mark_success_command`，推进“上次成功同步 hash”。如果本次中断或验证失败，不要 mark success。

# Workflow

## 1. 准备分支和 diff

运行准备脚本。除非用户主动要求全量，否则使用默认增量模式。

若目标项目存在未提交改动且脚本拒绝继续，先判断这些改动是否与本任务有关：
- 无关：询问用户是否允许继续或让用户处理。
- 相关：基于现有改动继续，不回退用户代码；必要时可用 `--allow-dirty`。

读取脚本 JSON 输出中的这些字段：
- `source_branch`
- `target_branch`
- `target_repo`
- `mode_effective`
- `diff_range`
- `changed_files_path`
- `name_status_path`
- `diff_stat_path`
- `diff_path`
- `mark_success_command`
- `warnings`

## 2. 分析 diff

优先读取：
```bash
cat [changed_files_path]
cat [name_status_path]
cat [diff_stat_path]
```

按需读取 `diff_path` 和源项目相关完整文件，生成结构化改动清单：
- 源文件路径。
- 改动类型：新增、修改、删除、重命名。
- 业务功能点。
- 接口、字段、枚举、权限、校验、流程状态变化。
- 目标端是否需要实现；若目标端无对应页面，说明跳过原因。

过滤非业务噪音：
- `node_modules`
- lock 文件
- 纯格式化、注释、配置变更
- 与目标端无关的 PC-only 页面

## 3. 在目标项目实现

只编辑目标项目文件。

实现要求：
- 找目标项目已有对应页面、组件等位置。
- 使用目标端已有组件库和请求封装。
- 不新增目标项目未使用的依赖。
- 移动端适配：触摸/click、弹窗/表单/列表布局、响应式单位、Vant 等既有组件。
- 接口成功/失败结构以目标项目现有封装为准。
- API url 若目标项目要求白名单，按目标项目规范补齐。

## 4. 验证

至少做静态校验：
- 检查导入路径。
- 检查语法、组件注册、变量命名。
- 检查接口字段和参数流转。

## 5. 推进 checkpoint

只有满足以下条件才执行 `mark_success_command`：
- 目标端功能已实现。
- 已按可行方式验证。
- 本轮同步没有已知遗漏。

如果用户只要求分析 diff、不要求实现，不能执行 `mark_success_command`。

## 6. 输出报告

最终报告格式：
```markdown
### 跨端同步完成报告

**源项目分支**：dev_xxx
**目标项目分支**：dev_xxx
**目标项目路径**：../home-h5
**diff 模式**：incremental/full
**diff 范围**：abc123..HEAD 或 origin/master...HEAD
**checkpoint**：已推进到 abc123 / 未推进（原因）

**源项目主要改动**（共 X 处）：
- 文件1：功能描述

**目标项目完成文件**：
- 文件路径 -> 完成的功能点

**适配说明**：
- ...

**验证**：
- 已运行/未运行的检查及结果

**下一步建议**：
- 测试建议或注意事项
```

# Target References

- `references/home-h5.md`：当目标项目路径或名称是 `home-h5` 时读取，其中包含目录映射、请求返回结构差异等信息。

# Red Lines

- 不修改源项目。
- 不直接复制 PC 端代码到目标项目。
- 不绕过准备脚本手动做目标分支管理。
- 不在未完成同步时推进成功 checkpoint。
- 不回退用户已有改动。
