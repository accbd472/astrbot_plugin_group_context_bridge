"""AstrBot 群上下文桥接插件。"""

from __future__ import annotations
import json
import os
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter as event_filter
from astrbot.api.star import Context, Star, StarTools, register

try:
    from astrbot.api.message_components import Plain
except ImportError:
    Plain = None


@register(
    "astrbot_plugin_group_context_bridge",
    "accbd472",
    "将多个来源群的上下文只读模拟嫁接到目标群，在目标群消息进入模型前注入来源群最近消息或摘要。",
    "1.1.0",
)
class GroupContextBridgePlugin(Star):
    """将多个来源群的最近消息或摘要注入目标群上下文。"""

    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self.context = context
        self.plugin_name = "astrbot_plugin_group_context_bridge"
        self.plugin_root = Path(__file__).resolve().parent
        self.data_dir = StarTools.get_data_dir() / self.plugin_name
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_dir / "state.json"

        self.config: dict[str, Any] = {
            "default_recent_count": 12,
            "default_max_chars": 1800,
            "max_saved_per_group": 60,
            "only_when_wake": True,
            "command_prefixes": ["/", "!", "。", "！"],
            "inject_template": (
                "[桥接上下文开始]\n"
                "来源群: {source_gids}\n"
                "{context_block}\n"
                "[桥接上下文结束]\n\n"
                "{original_message}"
            ),
            "save_interval_seconds": 15,
        }

        self._last_save_ts = 0.0
        self._dirty = False

        try:
            runtime_cfg = getattr(self.context, "get_config", lambda: {})() or {}
            if isinstance(runtime_cfg, dict):
                for key in self.config:
                    if key in runtime_cfg:
                        self.config[key] = runtime_cfg[key]
        except Exception as exc:
            logger.warning(
                f"[{self.plugin_name}] 读取运行时配置失败，将使用默认配置: {exc}"
            )

        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        """加载插件状态。"""
        default_state: dict[str, Any] = {
            "bridges": {},
            "summaries": {},
            "recent_messages": {},
        }
        if not self.state_path.exists():
            self._save_state(default_state, force=True)
            return default_state

        try:
            with self.state_path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            if not isinstance(data, dict):
                return default_state
            data.setdefault("bridges", {})
            data.setdefault("summaries", {})
            data.setdefault("recent_messages", {})
            return data
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                f"[{self.plugin_name}] 读取状态文件失败，使用默认状态: {exc}"
            )
            return default_state

    def _save_state(
        self,
        state: dict[str, Any] | None = None,
        *,
        force: bool = False,
    ) -> None:
        """保存插件状态，带简单节流。"""
        if state is not None:
            self.state = state

        self._dirty = True
        now = time.time()
        save_interval = float(self.config.get("save_interval_seconds", 15) or 15)

        if not force and (now - self._last_save_ts) < save_interval:
            return

        tmp_path = self.state_path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as file:
            json.dump(self.state, file, ensure_ascii=False, indent=2)

        os.replace(tmp_path, self.state_path)
        self._last_save_ts = now
        self._dirty = False

    def _flush_state(self) -> None:
        """强制将脏状态写入磁盘。"""
        if self._dirty:
            self._save_state(force=True)

    def _get_gid(self, event: AstrMessageEvent) -> str | None:
        """获取群号。"""
        for attr in ("group_id", "gid"):
            value = getattr(event, attr, None)
            if value:
                return str(value)

        session_id = getattr(event, "session_id", None)
        if session_id and ":" in str(session_id):
            return str(session_id).split(":")[-1]
        return None

    def _get_uid(self, event: AstrMessageEvent) -> str:
        """获取用户 ID。"""
        for attr in ("user_id", "sender_id", "uid"):
            value = getattr(event, attr, None)
            if value:
                return str(value)

        sender = getattr(event, "sender", None)
        if sender:
            for attr in ("user_id", "id", "uid"):
                value = getattr(sender, attr, None)
                if value:
                    return str(value)

        return "unknown"

    def _is_group_message(self, event: AstrMessageEvent) -> bool:
        """判断是否为群消息。"""
        if getattr(event, "group_id", None):
            return True

        msg_type = getattr(event, "message_type", None)
        if msg_type and str(msg_type).lower() in ("group", "group_message"):
            return True

        session_id = getattr(event, "session_id", None)
        return bool(session_id and str(session_id).startswith("group:"))

    def _get_message_text(self, event: AstrMessageEvent) -> str:
        """提取消息文本。"""
        text = getattr(event, "message_str", None)
        if isinstance(text, str):
            return text.strip()

        try:
            message_obj = getattr(event, "message_obj", None)
            if message_obj and hasattr(message_obj, "message"):
                parts: list[str] = []
                for comp in message_obj.message:
                    txt = getattr(comp, "text", None)
                    if txt:
                        parts.append(str(txt))
                if parts:
                    return "".join(parts).strip()
        except Exception as exc:
            logger.debug(f"[{self.plugin_name}] 提取消息文本失败: {exc}")

        return ""

    def _is_command_text(self, text: str) -> bool:
        """判断消息是否为命令。"""
        prefixes = self.config.get("command_prefixes") or []
        return any(text.startswith(prefix) for prefix in prefixes if prefix)

    def _trim_text(self, text: str, max_chars: int) -> str:
        """将文本裁剪到最大长度。"""
        if len(text) <= max_chars:
            return text
        return text[-max_chars:]

    def _get_bridge(self, target_gid: str) -> dict[str, Any] | None:
        """获取指定目标群桥接配置。"""
        bridge = self.state["bridges"].get(str(target_gid))
        if not bridge:
            return None

        if "source_gids" not in bridge:
            old = bridge.get("source_gid")
            bridge["source_gids"] = [str(old)] if old else []

        bridge.setdefault("mode", "recent")
        bridge.setdefault("recent_count", self.config["default_recent_count"])
        bridge.setdefault("max_chars", self.config["default_max_chars"])
        bridge.setdefault("merge_strategy", "concat")
        return bridge

    def _set_bridge(self, target_gid: str, bridge: dict[str, Any]) -> None:
        """设置桥接配置。"""
        self.state["bridges"][str(target_gid)] = bridge
        self._save_state(force=True)

    def _delete_bridge(self, target_gid: str) -> None:
        """删除桥接配置。"""
        self.state["bridges"].pop(str(target_gid), None)
        self._save_state(force=True)

    def _append_recent_message(self, gid: str, uid: str, text: str) -> None:
        """追加最近消息。"""
        if not gid or not text:
            return

        recents = self.state["recent_messages"].setdefault(str(gid), [])
        recents.append(
            {
                "uid": str(uid),
                "text": text,
                "ts": int(time.time()),
            }
        )

        keep = int(self.config.get("max_saved_per_group", 60) or 60)
        if len(recents) > keep:
            del recents[:-keep]

        self._save_state()

    def _get_recent_messages(self, gid: str, limit: int) -> list[dict[str, Any]]:
        """获取最近消息列表。"""
        return list(self.state["recent_messages"].get(str(gid), []))[-limit:]

    def _set_summary(self, gid: str, text: str) -> None:
        """设置群摘要。"""
        self.state["summaries"][str(gid)] = text
        self._save_state(force=True)

    def _clear_summary(self, gid: str) -> None:
        """清除群摘要。"""
        self.state["summaries"].pop(str(gid), None)
        self._save_state(force=True)

    def _build_context_block(
        self,
        source_gids: list[str],
        mode: str,
        recent_count: int,
        max_chars: int,
        merge_strategy: str,
    ) -> str:
        """构建注入上下文文本。"""
        pieces: list[str] = []

        if mode in ("summary", "mixed"):
            for gid in source_gids:
                summary = self.state["summaries"].get(str(gid), "").strip()
                if summary:
                    pieces.append(f"## 来源群 {gid} 摘要\n{summary}")

        if mode in ("recent", "mixed"):
            if merge_strategy == "interleave":
                merged: list[dict[str, Any]] = []
                for gid in source_gids:
                    msgs = self._get_recent_messages(gid, recent_count)
                    for msg in msgs:
                        merged.append(
                            {
                                "gid": str(gid),
                                "uid": msg.get("uid", "unknown"),
                                "text": msg.get("text", ""),
                                "ts": msg.get("ts", 0),
                            }
                        )

                merged.sort(key=lambda item: item["ts"])
                if merged:
                    lines = [
                        f"[{item['gid']}]({item['uid']}): {item['text']}"
                        for item in merged
                        if item.get("text")
                    ]
                    if lines:
                        pieces.append(
                            "## 多来源最近消息(按时间交错)\n" + "\n".join(lines)
                        )
            else:
                for gid in source_gids:
                    msgs = self._get_recent_messages(gid, recent_count)
                    if msgs:
                        lines = [
                            f"({msg.get('uid', 'unknown')}): {msg.get('text', '')}"
                            for msg in msgs
                            if msg.get("text")
                        ]
                        if lines:
                            pieces.append(
                                f"## 来源群 {gid} 最近消息\n" + "\n".join(lines)
                            )

        if not pieces:
            return ""

        context_block = "\n\n".join(pieces).strip()
        return self._trim_text(context_block, max_chars)

    def _is_wake_event(self, event: AstrMessageEvent) -> bool:
        """判断是否为唤醒事件。"""
        is_wake = getattr(event, "is_wake", None)
        if is_wake:
            return True

        try:
            wake_checker = getattr(event, "is_at_or_wake_command", None)
            if callable(wake_checker):
                return bool(wake_checker())
        except Exception as exc:
            logger.debug(f"[{self.plugin_name}] 判断唤醒事件失败: {exc}")

        return False

    def _inject_message_text(self, event: AstrMessageEvent, new_text: str) -> None:
        """将新文本写回事件对象。"""
        try:
            event.message_str = new_text
        except Exception as exc:
            logger.debug(f"[{self.plugin_name}] 写入 event.message_str 失败: {exc}")

        if Plain is None:
            return

        try:
            message_obj = getattr(event, "message_obj", None)
            if not message_obj or not hasattr(message_obj, "message"):
                return

            replaced = False
            for index, comp in enumerate(message_obj.message):
                if isinstance(comp, Plain):
                    message_obj.message[index] = Plain(new_text)
                    replaced = True
                    break

            if not replaced:
                message_obj.message.insert(0, Plain(new_text))
        except Exception as exc:
            logger.debug(f"[{self.plugin_name}] 注入 Plain 消息失败: {exc}")

    async def _reply(self, event: AstrMessageEvent, text: str):
        """兼容不同回复接口。"""
        try:
            yield event.plain_result(text)
            return
        except Exception as exc:
            logger.debug(f"[{self.plugin_name}] plain_result 回复失败: {exc}")

        try:
            yield event.make_result().message(text)
            return
        except Exception as exc:
            logger.warning(f"[{self.plugin_name}] 回复消息失败: {exc}")

    @event_filter.command("bridge")
    async def bridge(self, event: AstrMessageEvent):
        """bridge 管理命令。"""
        text = self._get_message_text(event)
        parts = text.split()
        gid = self._get_gid(event)

        if not gid:
            async for result in self._reply(event, "只能在群聊中使用 bridge 命令。"):
                yield result
            return

        if len(parts) < 2:
            async for result in self._reply(
                event,
                "用法:\n"
                "/bridge bind <来源群号...>\n"
                "/bridge bindto <目标群号> <来源群号...>\n"
                "/bridge addsrc <来源群号...>\n"
                "/bridge delsrc <来源群号...>\n"
                "/bridge sources\n"
                "/bridge unbind [目标群号]\n"
                "/bridge mode recent|summary|mixed\n"
                "/bridge merge concat|interleave\n"
                "/bridge recent <数量>\n"
                "/bridge maxchars <字数>\n"
                "/bridge summary set <摘要>\n"
                "/bridge summary clear [群号]\n"
                "/bridge show",
            ):
                yield result
            return

        sub = parts[1].lower()

        if sub == "bind":
            if len(parts) < 3:
                async for result in self._reply(
                    event, "用法: /bridge bind <来源群号...>"
                ):
                    yield result
                return

            source_gids = [str(item) for item in parts[2:] if str(item).strip()]
            bridge = self._get_bridge(gid) or {}
            bridge["source_gids"] = source_gids
            bridge.setdefault("mode", "recent")
            bridge.setdefault("recent_count", self.config["default_recent_count"])
            bridge.setdefault("max_chars", self.config["default_max_chars"])
            bridge.setdefault("merge_strategy", "concat")
            self._set_bridge(gid, bridge)

            async for result in self._reply(
                event,
                f"已绑定当前群 {gid} -> 来源群 {', '.join(source_gids)}",
            ):
                yield result
            return

        if sub == "bindto":
            if len(parts) < 4:
                async for result in self._reply(
                    event,
                    "用法: /bridge bindto <目标群号> <来源群号...>",
                ):
                    yield result
                return

            target_gid = str(parts[2])
            source_gids = [str(item) for item in parts[3:] if str(item).strip()]
            bridge = self._get_bridge(target_gid) or {}
            bridge["source_gids"] = source_gids
            bridge.setdefault("mode", "recent")
            bridge.setdefault("recent_count", self.config["default_recent_count"])
            bridge.setdefault("max_chars", self.config["default_max_chars"])
            bridge.setdefault("merge_strategy", "concat")
            self._set_bridge(target_gid, bridge)

            async for result in self._reply(
                event,
                f"已绑定目标群 {target_gid} -> 来源群 {', '.join(source_gids)}",
            ):
                yield result
            return

        if sub == "addsrc":
            if len(parts) < 3:
                async for result in self._reply(
                    event, "用法: /bridge addsrc <来源群号...>"
                ):
                    yield result
                return

            bridge = self._get_bridge(gid)
            if not bridge:
                async for result in self._reply(
                    event,
                    "当前群还没有 bridge，请先 /bridge bind ...",
                ):
                    yield result
                return

            current = list(bridge.get("source_gids", []))
            for source_gid in parts[2:]:
                source_gid = str(source_gid)
                if source_gid not in current:
                    current.append(source_gid)

            bridge["source_gids"] = current
            self._set_bridge(gid, bridge)

            async for result in self._reply(
                event, f"已更新来源群: {', '.join(current)}"
            ):
                yield result
            return

        if sub == "delsrc":
            if len(parts) < 3:
                async for result in self._reply(
                    event, "用法: /bridge delsrc <来源群号...>"
                ):
                    yield result
                return

            bridge = self._get_bridge(gid)
            if not bridge:
                async for result in self._reply(event, "当前群还没有 bridge。"):
                    yield result
                return

            remove_set = {str(item) for item in parts[2:]}
            current = [
                item
                for item in bridge.get("source_gids", [])
                if str(item) not in remove_set
            ]
            bridge["source_gids"] = current
            self._set_bridge(gid, bridge)

            async for result in self._reply(
                event,
                f"已更新来源群: {', '.join(current) if current else '(空)'}",
            ):
                yield result
            return

        if sub == "sources":
            bridge = self._get_bridge(gid)
            if not bridge:
                async for result in self._reply(event, "当前群还没有 bridge。"):
                    yield result
                return

            source_gids = bridge.get("source_gids", [])
            async for result in self._reply(
                event,
                "当前来源群: " + (", ".join(source_gids) if source_gids else "(空)"),
            ):
                yield result
            return

        if sub == "unbind":
            target_gid = str(parts[2]) if len(parts) >= 3 else gid
            self._delete_bridge(target_gid)

            async for result in self._reply(
                event,
                f"已解除目标群 {target_gid} 的 bridge。",
            ):
                yield result
            return

        if sub == "mode":
            if len(parts) < 3 or parts[2] not in ("recent", "summary", "mixed"):
                async for result in self._reply(
                    event,
                    "用法: /bridge mode recent|summary|mixed",
                ):
                    yield result
                return

            bridge = self._get_bridge(gid)
            if not bridge:
                async for result in self._reply(
                    event,
                    "当前群还没有 bridge，请先 /bridge bind ...",
                ):
                    yield result
                return

            bridge["mode"] = parts[2]
            self._set_bridge(gid, bridge)

            async for result in self._reply(event, f"已设置 bridge mode = {parts[2]}"):
                yield result
            return

        if sub == "merge":
            if len(parts) < 3 or parts[2] not in ("concat", "interleave"):
                async for result in self._reply(
                    event,
                    "用法: /bridge merge concat|interleave",
                ):
                    yield result
                return

            bridge = self._get_bridge(gid)
            if not bridge:
                async for result in self._reply(
                    event,
                    "当前群还没有 bridge，请先 /bridge bind ...",
                ):
                    yield result
                return

            bridge["merge_strategy"] = parts[2]
            self._set_bridge(gid, bridge)

            async for result in self._reply(
                event,
                f"已设置 merge_strategy = {parts[2]}",
            ):
                yield result
            return

        if sub == "recent":
            if len(parts) < 3 or not parts[2].isdigit():
                async for result in self._reply(event, "用法: /bridge recent <数量>"):
                    yield result
                return

            bridge = self._get_bridge(gid)
            if not bridge:
                async for result in self._reply(
                    event,
                    "当前群还没有 bridge，请先 /bridge bind ...",
                ):
                    yield result
                return

            bridge["recent_count"] = int(parts[2])
            self._set_bridge(gid, bridge)

            async for result in self._reply(
                event,
                f"已设置 recent_count = {parts[2]}",
            ):
                yield result
            return

        if sub == "maxchars":
            if len(parts) < 3 or not parts[2].isdigit():
                async for result in self._reply(event, "用法: /bridge maxchars <字数>"):
                    yield result
                return

            bridge = self._get_bridge(gid)
            if not bridge:
                async for result in self._reply(
                    event,
                    "当前群还没有 bridge，请先 /bridge bind ...",
                ):
                    yield result
                return

            bridge["max_chars"] = int(parts[2])
            self._set_bridge(gid, bridge)

            async for result in self._reply(
                event,
                f"已设置 max_chars = {parts[2]}",
            ):
                yield result
            return

        if sub == "summary":
            if len(parts) < 3:
                async for result in self._reply(
                    event,
                    "用法: /bridge summary set <摘要> | /bridge summary clear [群号]",
                ):
                    yield result
                return

            action = parts[2].lower()

            if action == "set":
                content = text.split("summary set", 1)[-1].strip()
                if not content:
                    async for result in self._reply(event, "请提供摘要内容。"):
                        yield result
                    return

                self._set_summary(gid, content)
                async for result in self._reply(event, f"已设置群 {gid} 的摘要。"):
                    yield result
                return

            if action == "clear":
                target_gid = str(parts[3]) if len(parts) >= 4 else gid
                self._clear_summary(target_gid)
                async for result in self._reply(
                    event,
                    f"已清除群 {target_gid} 的摘要。",
                ):
                    yield result
                return

            async for result in self._reply(
                event,
                "用法: /bridge summary set <摘要> | /bridge summary clear [群号]",
            ):
                yield result
            return

        if sub == "show":
            bridge = self._get_bridge(gid)
            if not bridge:
                async for result in self._reply(event, "当前群还没有 bridge。"):
                    yield result
                return

            source_gids = bridge.get("source_gids", [])
            mode = bridge.get("mode", "recent")
            recent_count = int(
                bridge.get("recent_count", self.config["default_recent_count"])
            )
            max_chars = int(bridge.get("max_chars", self.config["default_max_chars"]))
            merge_strategy = bridge.get("merge_strategy", "concat")
            context_block = self._build_context_block(
                source_gids,
                mode,
                recent_count,
                max_chars,
                merge_strategy,
            )
            preview = context_block[:1000] if context_block else "(当前无可注入内容)"
            message = (
                f"target_gid: {gid}\n"
                f"source_gids: {', '.join(source_gids) if source_gids else '(空)'}\n"
                f"mode: {mode}\n"
                f"merge_strategy: {merge_strategy}\n"
                f"recent_count: {recent_count}\n"
                f"max_chars: {max_chars}\n"
                f"\n预览:\n{preview}"
            )

            async for result in self._reply(event, message):
                yield result
            return

        async for result in self._reply(
            event,
            "未知子命令，请使用 /bridge 查看帮助。",
        ):
            yield result

    @event_filter.event_message_type(event_filter.EventMessageType.ALL, priority=1)
    async def on_all_message(self, event: AstrMessageEvent):
        """监听群消息并在需要时注入桥接上下文。"""
        try:
            if not self._is_group_message(event):
                return

            gid = self._get_gid(event)
            if not gid:
                return

            text = self._get_message_text(event)
            if not text:
                return

            if self._is_command_text(text):
                return

            uid = self._get_uid(event)
            self._append_recent_message(gid, uid, text)

            bridge = self._get_bridge(gid)
            if not bridge:
                return

            if self.config.get("only_when_wake", True) and not self._is_wake_event(
                event
            ):
                return

            source_gids = [
                str(item)
                for item in bridge.get("source_gids", [])
                if str(item) != str(gid)
            ]
            if not source_gids:
                return

            mode = bridge.get("mode", "recent")
            recent_count = int(
                bridge.get("recent_count", self.config["default_recent_count"])
            )
            max_chars = int(bridge.get("max_chars", self.config["default_max_chars"]))
            merge_strategy = bridge.get("merge_strategy", "concat")

            context_block = self._build_context_block(
                source_gids=source_gids,
                mode=mode,
                recent_count=recent_count,
                max_chars=max_chars,
                merge_strategy=merge_strategy,
            )
            if not context_block.strip():
                return

            injected = self.config["inject_template"].format(
                source_gids=", ".join(source_gids),
                context_block=context_block,
                original_message=text,
            )
            self._inject_message_text(event, injected)

            logger.info(
                f"[{self.plugin_name}] 已注入桥接上下文: "
                f"target_gid={gid}, source_gids={source_gids}, "
                f"mode={mode}, merge={merge_strategy}"
            )

            self._flush_state()
        except Exception as exc:
            logger.error(f"[{self.plugin_name}] 处理消息异常: {exc}")
            self._flush_state()
