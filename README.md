# astrbot_plugin_group_context_bridge

一个低侵入的 AstrBot 群聊上下文桥接插件。  
支持将一个或多个来源群的最近消息或摘要以**只读参考上下文**的形式注入目标群对话，帮助目标群中的 AI 在回答时参考其他群聊内容。

> 这是运行时上下文注入模拟桥接，**不是**底层真实 session / conversation 合并。  
> 插件**不修改数据库**、**不改变真实会话归属**，便于生产环境部署、回滚和卸载。

---

## 功能特性

- 支持**单来源群**桥接
- 支持**多来源群**桥接
- 支持 `recent` / `summary` / `mixed` 三种模式
- 支持来源消息合并策略：
  - `concat`
  - `interleave`
- 支持**手工摘要兜底**
- 插件状态独立保存在插件数据目录中，便于迁移、备份和回滚
- 支持旧版 `source_gid` 配置平滑迁移到 `source_gids`

---

## 适用场景

- 跨群信息参考
- 多群讨论上下文融合
- 通知群 / 资料群内容辅助问答
- 只读来源群 -> 目标群 AI 问答增强
- 多个资料来源群聚合后统一供某个答疑群使用

---

## 工作原理

插件主要做三件事：

1. **监听群消息**
   - 将群内普通文本消息缓存为最近消息

2. **记录桥接关系**
   - 保存“目标群 -> 来源群列表”的映射关系
   - 支持保存摘要、模式、合并策略等配置

3. **在目标群触发 AI 时注入上下文**
   - 当目标群消息进入模型前
   - 将来源群最近消息或摘要整理为参考文本
   - 通过注入模板拼接到原始消息前面

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

### 2. 只做上下文注入，不做权限绕过
插件不会主动绕过平台规则、封禁策略或框架级权限控制。

### 3. 来源群消息采集取决于事件是否能到达插件层
如果某些群的消息事件在更前层已经被完全丢弃，那么插件无法实时采集该群消息。  
这种情况下可以使用：

- 手工摘要
- 已有历史缓存
- 外部补录

作为兜底。

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

确保目录内至少包含：

- `main.py`
- `metadata.yaml`
- `_conf_schema.json`
- `README.md`

如需完整开源仓库发布，建议同时包含：

- `CHANGELOG.md`
- `LICENSE`
- `.gitignore`

然后重启 AstrBot：

```bash
docker restart astrbot
```

---

## 配置说明

插件配置由 `_conf_schema.json` 定义，当前版本主要支持以下配置项：

- `default_recent_count`：默认读取每个来源群最近多少条消息
- `default_max_chars`：注入上下文的最大字符数
- `max_saved_per_group`：每个群本地缓存的最多消息数
- `only_when_wake`：仅在消息已被判定为唤醒时才注入
- `command_prefixes`：命令前缀列表，命中后不缓存
- `inject_template`：注入模板
- `save_interval_seconds`：状态文件最短落盘间隔秒数

---

## 数据保存位置

插件状态保存在 AstrBot 插件数据目录下，通常类似于：

```text
plugin_data/astrbot_plugin_group_context_bridge/state.json
```

实际路径由 AstrBot 的 `StarTools.get_data_dir()` 决定。

其中通常包含：

- 目标群与来源群绑定关系
- 来源群列表
- bridge 模式
- merge 策略
- 手工摘要
- 最近消息缓存

---

## 指令列表

本插件通过 `/bridge` 系列指令管理“目标群 -> 来源群”的上下文桥接关系。

### `/bridge bind <来源群号...>`

将**当前群**设置为目标群，并绑定一个或多个来源群。

**示例：**

```text
/bridge bind 10001
/bridge bind 10001 10002 10003
```

---

### `/bridge bindto <目标群号> <来源群号...>`

直接为指定目标群绑定一个或多个来源群。

**示例：**

```text
/bridge bindto 20001 10001
/bridge bindto 20001 10001 10002
```

---

### `/bridge addsrc <来源群号...>`

为**当前群**追加一个或多个来源群。

**示例：**

```text
/bridge addsrc 10002
/bridge addsrc 10003 10004
```

---

### `/bridge delsrc <来源群号...>`

从**当前群**桥接配置中移除一个或多个来源群。

**示例：**

```text
/bridge delsrc 10002
```

---

### `/bridge sources`

查看**当前群**配置的来源群列表。

**示例：**

```text
/bridge sources
```

---

### `/bridge unbind [目标群号]`

解绑当前群，或解绑指定目标群。

**示例：**

```text
/bridge unbind
/bridge unbind 20001
```

---

### `/bridge mode recent|summary|mixed`

设置**当前群**桥接模式。

- `recent`：只注入最近消息
- `summary`：只注入摘要
- `mixed`：同时注入摘要和最近消息

**示例：**

```text
/bridge mode recent
/bridge mode summary
/bridge mode mixed
```

---

### `/bridge merge concat|interleave`

设置**当前群**多来源消息合并方式。

- `concat`：按来源群分别拼接
- `interleave`：按时间交错混合多个来源群消息

**示例：**

```text
/bridge merge concat
/bridge merge interleave
```

---

### `/bridge recent <数量>`

设置**当前群**读取每个来源群最近多少条消息。

**示例：**

```text
/bridge recent 8
```

---

### `/bridge maxchars <字数>`

设置**当前群**注入上下文的最大字符数。

**示例： `/群摘要除
bridge summary clear
/bridge summary clear 10001
```

---

### `/bridge show`

查看**当前群**完整桥接配置与当前可注入内容预览。

**示例：**

```text
/bridge show
```

---

## 常见操作组合

### 1. 给当前群绑定单个来源群

```text
/bridge bind 10001
```

作用：将当前群作为目标群，把 `10001` 作为来源群。

---

### 2. 给当前群绑定多个来源群

```text
/bridge bind 10001 10002 10003
```

作用：让当前群同时参考多个来源群上下文。

---

### 3. 直接为指定目标群配置桥接

```text
/bridge bindto 20001 10001 10002
```

作用：将目标群 `20001` 绑定到多个来源群。

---

### 4. 设置多来源合并策略

```text
/bridge merge interleave
```

作用：让多个来源群消息按时间交错方式组织，更适合多群同时讨论场景。

---

### 5. 设置手工摘要兜底

```text
/bridge summary set 资料群最近主要围绕 Docker 重启失败、插件目录结构和 metadata 修正展开讨论。
```

作用：在无法实时采集完整来源消息时，给当前群 AI 提供背景信息。

---

### 6. 查看当前桥接状态

```text
/bridge show
```

作用：快速确认当前群是否已经正确配置来源群、模式和摘要。

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

如果没有出现注入日志，建议排查：

- 是否已正确绑定目标群和来源群
- 来源群最近消息是否已被缓存
- 当前消息是否确实触发了 AI 回复流程
- `only_when_wake` 是否导致普通消息不注入
- 配置是否保存成功

---

## 生产环境建议

- 建议先在测试群验证桥接逻辑
- 建议限制单次注入最大消息条数，避免上下文过长
- 建议对每条缓存消息设置合理长度截断
- 建议保留手工摘要机制，作为实时采集失败时的兜底
- 建议定期备份 `state.json`
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

2. 如需彻底清理状态，再删除对应插件数据目录

3. 重启 AstrBot：
   ```bash
   docker restart astrbot
   ```

由于插件不修改数据库、不改真实会话归属，因此回滚风险较低。

---

## 版本信息

当前版本：`v1.1.1`

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
