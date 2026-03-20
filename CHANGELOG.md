```markdown
# Changelog

All notable changes to this project will be documented in this file.

## [1.1.0] - 2026-01-01

### Added
- 支持多个来源群桥接
- 新增 `mixed` 模式
- 新增 `concat` / `interleave` 两种多来源消息合并策略
- 新增 `/bridge addsrc` 命令
- 新增 `/bridge delsrc` 命令
- 新增 `/bridge sources` 命令
- 新增手工摘要兜底能力
- 支持将“禁止正常 bot 回复、但事件仍可到达插件层”的群作为只读来源群

### Changed
- 将桥接配置从单一 `source_gid` 扩展为 `source_gids`
- 优化上下文注入逻辑，增强多群融合场景兼容性
- 完善 README、发布说明和开源仓库文档

### Compatibility
- 兼容旧版本单来源群配置迁移
- 不修改数据库
- 不改变真实 session / conversation 归属

---

## [1.0.0] - 2025-01-01

### Added
- 初始版本发布
- 支持单来源群 -> 目标群上下文桥接
- 支持最近消息缓存
- 支持目标群 AI 触发时注入来源群参考上下文
- 支持基础桥接管理命令