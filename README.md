```markdown
# astrbot_plugin_group_context_bridge

一个低侵入的 AstrBot 群聊上下文桥接插件。  
支持将一个或多个来源群的最近消息以**只读参考上下文**的形式注入目标群对话，帮助目标群中的 AI 在回答时参考其他群聊内容。

> 这是“运行时上下文注入模拟桥接”，**不是**底层真实 session / conversation 合并。  
> 插件**不修改数据库**、**不改变真实会话归属**，便于生产环境部署、回滚和卸载。

---

## 功能特性

- 支持**单来源群**桥接
- 支持**多来源群**桥接
- 支持 `mixed` 模式
- 支持来源消息合并策略：
  - `concat`
  - `interleave`
- 支持**手工摘要兜底**
- 支持将“**不参与正常 bot 回复，但事件仍能到达插件层**”的群作为只读来源群
- 插件状态独立保存在 `plugin_data` 目录，便于迁移、备份和回滚

---

## 适用场景

- 跨群信息参考
- 多群讨论上下文融合
- 通知群 / 资料群内容辅助问答
- 被限制回复群的上下文延续
- 只读来源群 -> 目标群 AI 问答增强

---

## 工作原理

插件主要做三件事：

1. **监听来源群消息**  
   把来源群最近的消息缓存下来；

2. **记录桥接关系**  
   保存“哪些来源群 -> 哪个目标群”的映射关系；

3. **在目标群触发 AI 时注入上下文**  
   将来源群的最近消息整理为参考文本，追加到目标群 AI 的输入中。

因此，它实现的是：

- **上下文参考增强**
- **运行时拼接注入**
- **只读桥接**

而不是：

- 修改 AstrBot 底层会话结构
- 合并真实数据库会话
- 伪造真实消息归属

---

## 设计边界

### 1. 不是底层真实会话合并
本插件不会把多个群真的合并成一个会话，也不会改写 AstrBot 原有 session / conversation 数据。

### 2. 对“被封禁/黑名单群”的支持是有条件的
如果某个群只是：

- 不参与正常 bot 回复
- 但消息事件仍然能够传递到插件层

那么插件仍然可以把它作为**只读来源群**使用。

但如果某个群的消息事件在更前层就被完全丢弃，那么插件无法实时采集该群消息。  
这种情况下只能使用：

- 手工摘要
- 历史缓存
- 外部补录

来作为兜底。

### 3. 只做上下文注入，不做权限绕过
插件不会主动绕过平台规则、封禁策略或框架级权限控制。

---

## 目录结构

```text
astrbot_plugin_group_context_bridge/
├── main.py
├── metadata.yaml
├── _conf_schema.json
├── README.md
├── CHANGELOG.md
├── LICENSE
└── .gitignore
```

---

## 安装方法

将插件目录放入 AstrBot 插件目录，例如：

```bash
/root/astrbot/data/plugins/astrbot_plugin_group_context_bridge
```

确保目录内包含：

- `main.py`
- `metadata.yaml`
- `_conf_schema.json`
- `README.md`

如需完整开源仓库发布，建议同时包含：

- `CHANGELOG.md`
- `LICENSE`
- `.gitignore`

然后重启 AstrBot 容器：

```bash
docker restart astrbot
```

---

## 配置说明

插件配置由 `_conf_schema.json` 定义，实际字段以你当前插件代码实现为准。

常见配置项通常包括：

- 最大缓存消息数
- 每条消息最大截断长度
- 注入时最大拼接条数
- 来源消息保留策略
- 默认桥接模式
- 默认合并策略

---

## 数据保存位置

插件运行状态通常保存在：

```bash
/root/astrbot/data/plugin_data/astrbot_plugin_group_context_bridge/state.json
```

其中可能包含：

- 目标群与来源群绑定关系
- 来源群列表
- bridge 模式
- merge 策略
- 手工摘要
- 缓存状态

---

## 指令列表与作用说明

本插件通过 `/bridge` 系列指令管理“来源群 -> 目标群”的上下文桥接关系。

---

### `/bridge bind <source_gid> <target_gid>`

**作用：**  
将一个来源群绑定到一个目标群，建立最基础的桥接关系。

**说明：**
- `source_gid`：来源群 ID
- `target_gid`：目标群 ID
- 适合初始化单来源群桥接

**示例：**
```text
/bridge bind 10001 20001
```

表示将群 `10001` 的上下文桥接到群 `20001`。

---

### `/bridge bindto <target_gid>`

**作用：**  
把“当前群”直接绑定到指定目标群，省去手动填写当前群 ID。

**说明：**
- 当前执行命令的群会被视为来源群
- `target_gid` 为目标群 ID

**示例：**
```text
/bridge bindto 20001
```

表示把当前群作为来源群，桥接到 `20001`。

---

### `/bridge addsrc <target_gid> <source_gid>`

**作用：**  
给某个目标群继续追加一个来源群。

**说明：**
- 用于多来源群桥接场景
- 不会覆盖已有来源群，而是在原有基础上新增

**示例：**
```text
/bridge addsrc 20001 10002
```

表示为目标群 `20001` 新增来源群 `10002`。

---

### `/bridge delsrc <target_gid> <source_gid>`

**作用：**  
从某个目标群的桥接配置中移除一个来源群。

**说明：**
- 用于删除不再需要的来源群
- 删除后，该来源群的消息将不再参与注入

**示例：**
```text
/bridge delsrc 20001 10002
```

表示把来源群 `10002` 从目标群 `20001` 的桥接配置中移除。

---

### `/bridge sources <target_gid>`

**作用：**  
查看某个目标群当前配置的所有来源群。

**说明：**
- 用于核对桥接配置是否正确
- 适合排查“为什么没有注入某个群上下文”这类问题

**示例：**
```text
/bridge sources 20001
```

表示查看目标群 `20001` 当前有哪些来源群。

---

### `/bridge mode <target_gid> <mode>`

**作用：**  
设置目标群的桥接模式。

**说明：**
- `target_gid`：目标群 ID
- `mode`：桥接模式，例如 `mixed`
- 具体支持的模式以插件当前实现为准

**示例：**
```text
/bridge mode 20001 mixed
```

表示把目标群 `20001` 的桥接模式设置为 `mixed`。

---

### `/bridge merge <target_gid> <concat|interleave>`

**作用：**  
设置多个来源群消息的合并方式。

**说明：**
- `concat`：按来源或整理结果顺序直接拼接
- `interleave`：按时间交错混合多个来源群消息
- 适合多来源群场景下控制上下文组织方式

**示例：**
```text
/bridge merge 20001 concat
```

表示目标群 `20001` 使用 `concat` 方式合并多来源群消息。

```text
/bridge merge 20001 interleave
```

表示目标群 `20001` 使用 `interleave` 方式合并多来源群消息。

---

### `/bridge summary set <target_gid> <summary_text>`

**作用：**  
为目标群设置一段手工摘要，作为桥接上下文的补充或兜底。

**说明：**
- 当来源群消息无法实时采集时，可通过手工摘要补充背景信息
- 适合封禁群、资料群、历史讨论总结等场景

**示例：**
```text
/bridge summary set 20001 资料群最近主要讨论插件部署报错和 metadata 配置问题。
```

表示为目标群 `20001` 设置一段桥接摘要。

---

### `/bridge show <target_gid>`

**作用：**  
查看某个目标群当前完整的桥接配置。

**通常可查看内容包括：**
- 目标群 ID
- 已绑定的来源群列表
- 当前模式
- 当前合并策略
- 是否存在手工摘要
- 当前状态概览

**示例：**
```text
/bridge show 20001
```

表示查看目标群 `20001` 当前的桥接详细配置。

---

## 常见操作组合

### 1. 建立最基础桥接
```text
/bridge bind 10001 20001
```

**作用：**  
把来源群 `10001` 桥接到目标群 `20001`。

---

### 2. 给目标群增加多个来源群
```text
/bridge addsrc 20001 10001
/bridge addsrc 20001 10002
/bridge addsrc 20001 10003
```

**作用：**  
让目标群 `20001` 同时参考多个来源群的上下文。

---

### 3. 设置多来源合并策略
```text
/bridge merge 20001 interleave
```

**作用：**  
让多个来源群消息按时间交错方式组织，更适合多群同时讨论的场景。

---

### 4. 设置手工摘要兜底
```text
/bridge summary set 20001 资料群最近主要围绕 Docker 重启失败、插件目录结构和 metadata 修正展开讨论。
```

**作用：**  
即使某些来源群无法实时采集，也能给目标群 AI 提供背景信息。

---

### 5. 查看当前桥接状态
```text
/bridge show 20001
```

**作用：**  
快速确认目标群是否已经正确配置来源群、模式和摘要。

---

## 使用示例

### 示例 1：单来源群桥接

```text
/bridge bind 10001 20001
```

作用：将来源群 `10001` 桥接到目标群 `20001`。

---

### 示例 2：多来源群桥接

```text
/bridge addsrc 20001 10001
/bridge addsrc 20001 10002
/bridge addsrc 20001 10003
```

作用：让目标群 `20001` 同时参考多个来源群上下文。

---

### 示例 3：设置 interleave 合并策略

```text
/bridge merge 20001 interleave
```

作用：让多个来源群消息按时间交错融合，更适合多群同时讨论场景。

---

### 示例 4：设置摘要兜底

```text
/bridge summary set 20001 资料群最近主要讨论的是插件部署报错、Docker 重启和 metadata 配置问题。
```

作用：在无法实时采集来源群消息时，仍给目标群 AI 提供背景信息。

---

## 日志说明

正常工作时，你可能在日志中看到类似输出：

```text
[astrbot_plugin_group_context_bridge] 已注入桥接上下文: target_gid=..., source_gids=..., mode=..., merge=...
```

这表示：

- 目标群触发了 AI
- 插件成功找到桥接配置
- 并已将来源群上下文注入输入

如果没有出现注入日志，可能需要排查：

- 是否已正确绑定目标群和来源群
- 来源群消息事件是否确实能到达插件层
- 配置是否保存成功
- 当前群是否确实触发了 AI 回复流程

---

## 生产环境建议

- 建议先在测试群验证桥接逻辑
- 建议限制单次注入的最大消息条数，避免上下文过长
- 建议对每条缓存消息设置合理的长度截断
- 建议保留手工摘要机制，作为实时采集失败时的兜底
- 建议定期备份 `plugin_data/astrbot_plugin_group_context_bridge/state.json`
- 如需回滚，只需停用插件并删除插件目录/状态文件

---

## 升级兼容

当前版本已兼容从旧的：

- `source_gid`

迁移到新的：

- `source_gids`

如果你之前使用的是单来源群版本，升级后通常可以平滑迁移。

---

## 卸载与回滚

1. 删除插件目录：
   ```bash
   /root/astrbot/data/plugins/astrbot_plugin_group_context_bridge
   ```

2. 如需彻底清理状态，再删除：
   ```bash
   /root/astrbot/data/plugin_data/astrbot_plugin_group_context_bridge
   ```

3. 重启 AstrBot：
   ```bash
   docker restart astrbot
   ```

由于插件不修改数据库、不改真实会话归属，因此回滚风险较低。

---

## 版本信息

当前版本：`v1.1.0`

详见：

- [CHANGELOG.md](./CHANGELOG.md)

---

## 开源协议

本项目采用：

- [MIT License](./LICENSE)

---

## 仓库地址

GitHub：

- https://github.com/accbd472/astrbot_plugin_group_context_bridge
```
