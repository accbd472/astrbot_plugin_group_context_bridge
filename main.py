import json
import os
import time
from typing import Any, Dict, List, Optional

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

try:
    from astrbot.api.message_components import Plain
except Exception:
    Plain = None


@register(
    "astrbot_plugin_group_context_bridge",
    "Operit",
    "将多个来源群的上下文只读模拟嫁接到目标群，在目标群消息进入模型前注入来源群最近消息或摘要。",
    "1.1.0",
)
class GroupContextBridgePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context
        self.plugin_root = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = os.path.join(
            os.path.dirname(os.path.dirname(self.plugin_root)),
            "plugin_data",
            "astrbot_plugin_group_context_bridge",
        )
        os.makedirs(self.data_dir, exist_ok=True)
        self.state_path = os.path.join(self.data_dir, "state.json")

        self.config = {
            "default_recent_count": 12,
            "default_max_chars": 1800,
            "max_saved_per_group": 60,
            "only_when_wake": True,
            "allow_collect_from_blocked_groups": True,
            "command_prefixes": ["/", "!", "。", "！"],
            "inject_template": (
                "[桥接上下文开始]\n"
                "来源群: {source_gids}\n"
                "{context_block}\n"
                "[桥接上下文结束]\n\n"
                "{original_message}"
            ),
        }

        try:
            runtime_cfg = getattr(self.context, "get_config", lambda: {})() or {}
            if isinstance(runtime_cfg, dict):
                for k in self.config:
                    if k in runtime_cfg:
                        self.config[k] = runtime_cfg[k]
        except Exception:
            pass

        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        default_state = {
            "bridges": {},
            "summaries": {},
            "recent_messages": {},
        }
        if not os.path.exists(self.state_path):
            self._save_state(default_state)
            return default_state
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return default_state
            data.setdefault("bridges", {})
            data.setdefault("summaries", {})
            data.setdefault("recent_messages", {})
            return data
        except Exception:
            return default_state

    def _save_state(self, state: Optional[Dict[str, Any]] = None):
        if state is not None:
            self.state = state
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)

    def _get_gid(self, event: AstrMessageEvent) -> Optional[str]:
        for attr in ["group_id", "gid"]:
            val = getattr(event, attr, None)
            if val:
                return str(val)
        session_id = getattr(event, "session_id", None)
        if session_id and ":" in str(session_id):
            return str(session_id).split(":")[-1]
        return None

    def _get_uid(self, event: AstrMessageEvent) -> str:
        for attr in ["user_id", "sender_id", "uid"]:
            val = getattr(event, attr, None)
            if val:
                return str(val)
        sender = getattr(event, "sender", None)
        if sender:
            for attr in ["user_id", "id", "uid"]:
                val = getattr(sender, attr, None)
                if val:
                    return str(val)
        return "unknown"

    def _is_group_message(self, event: AstrMessageEvent) -> bool:
        if getattr(event, "group_id", None):
            return True
        msg_type = getattr(event, "message_type", None)
        if msg_type and str(msg_type).lower() in ("group", "group_message"):
            return True
        session_id = getattr(event, "session_id", None)
        if session_id and str(session_id).startswith("group:"):
            return True
        return False

    def _get_message_text(self, event: AstrMessageEvent) -> str:
        text = getattr(event, "message_str", None)
        if isinstance(text, str):
            return text.strip()
        try:
            message_obj = getattr(event, "message_obj", None)
            if message_obj and hasattr(message_obj, "message"):
                parts = []
                for comp in message_obj.message:
                    txt = getattr(comp, "text", None)
                    if txt:
                        parts.append(str(txt))
                if parts:
                    return "".join(parts).strip()
        except Exception:
            pass
        return ""

    def _is_command_text(self, text: str) -> bool:
        prefixes = self.config.get("command_prefixes") or []
        return any(text.startswith(p) for p in prefixes if p)

    def _trim_text(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[-max_chars:]

    def _get_bridge(self, target_gid: str) -> Optional[Dict[str, Any]]:
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

    def _set_bridge(self, target_gid: str, bridge: Dict[str, Any]):
        self.state["bridges"][str(target_gid)] = bridge
        self._save_state()

    def _delete_bridge(self, target_gid: str):
        self.state["bridges"].pop(str(target_gid), None)
        self._save_state()

    def _append_recent_message(self, gid: str, uid: str, text: str):
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

    def _get_recent_messages(self, gid: str, limit: int) -> List[Dict[str, Any]]:
        return list(self.state["recent_messages"].get(str(gid), []))[-limit:]

    def _set_summary(self, gid: str, text: str):
        self.state["summaries"][str(gid)] = text
        self._save_state()

    def _clear_summary(self, gid: str):
        self.state["summaries"].pop(str(gid), None)
        self._save_state()

    def _build_context_block(self, source_gids: List[str], mode: str, recent_count: int, max_chars: int, merge_strategy: str) -> str:
        pieces = []

        if mode in ("summary", "mixed"):
            for gid in source_gids:
                summary = self.state["summaries"].get(str(gid), "").strip()
                if summary:
                    pieces.append(f"## 来源群 {gid} 摘要\n{summary}")

        if mode in ("recent", "mixed"):
            if merge_strategy == "interleave":
                merged = []
                for gid in source_gids:
                    msgs = self._get_recent_messages(gid, recent_count)
                    for m in msgs:
                        merged.append(
                            {
                                "gid": str(gid),
                                "uid": m.get("uid", "unknown"),
                                "text": m.get("text", ""),
                                "ts": m.get("ts", 0),
                            }
                        )
                merged.sort(key=lambda x: x["ts"])
                if merged:
                    lines = [f"[{m['gid']}]({m['uid']}): {m['text']}" for m in merged if m.get("text")]
                    if lines:
                        pieces.append("## 多来源最近消息(按时间交错)\n" + "\n".join(lines))
            else:
                for gid in source_gids:
                    msgs = self._get_recent_messages(gid, recent_count)
                    if msgs:
                        lines = [f"({m.get('uid', 'unknown')}): {m.get('text', '')}" for m in msgs if m.get("text")]
                        if lines:
                            pieces.append(f"## 来源群 {gid} 最近消息\n" + "\n".join(lines))

        if not pieces:
            return ""

        context_block = "\n\n".join(pieces).strip()
        return self._trim_text(context_block, max_chars)

    def _is_wake_event(self, event: AstrMessageEvent) -> bool:
        is_wake = getattr(event, "is_wake", None)
        if is_wake:
            return True
        try:
            if callable(getattr(event, "is_at_or_wake_command", None)):
                return bool(event.is_at_or_wake_command())
        except Exception:
            pass
        return False

    def _inject_message_text(self, event: AstrMessageEvent, new_text: str):
        try:
            event.message_str = new_text
        except Exception:
            pass

        if Plain is None:
            return

        try:
            message_obj = getattr(event, "message_obj", None)
            if not message_obj or not hasattr(message_obj, "message"):
                return

            replaced = False
            for i, comp in enumerate(message_obj.message):
                if isinstance(comp, Plain):
                    message_obj.message[i] = Plain(new_text)
                    replaced = True
                    break

            if not replaced:
                message_obj.message.insert(0, Plain(new_text))
        except Exception:
            pass

    async def _reply(self, event: AstrMessageEvent, text: str):
        try:
            yield event.plain_result(text)
            return
        except Exception:
            pass
        try:
            yield event.make_result().message(text)
            return
        except Exception:
            pass

    @filter.command("bridge")
    async def bridge(self, event: AstrMessageEvent):
        text = self._get_message_text(event)
        parts = text.split()
        gid = self._get_gid(event)

        if not gid:
            async for r in self._reply(event, "只能在群聊中使用 bridge 命令。"):
                yield r
            return

        if len(parts) < 2:
            async for r in self._reply(
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
                "/bridge summary clear [来源群号]\n"
                "/bridge show"
            ):
                yield r
            return

        sub = parts[1].lower()

        if sub == "bind":
            if len(parts) < 3:
                async for r in self._reply(event, "用法: /bridge bind <来源群号...>"):
                    yield r
                return
            source_gids = [str(x) for x in parts[2:] if str(x).strip()]
            bridge = self._get_bridge(gid) or {}
            bridge["source_gids"] = source_gids
            bridge.setdefault("mode", "recent")
            bridge.setdefault("recent_count", self.config["default_recent_count"])
            bridge.setdefault("max_chars", self.config["default_max_chars"])
            bridge.setdefault("merge_strategy", "concat")
            self._set_bridge(gid, bridge)
            async for r in self._reply(event, f"已绑定当前群 {gid} -> 来源群 {', '.join(source_gids)}"):
                yield r
            return

        if sub == "bindto":
            if len(parts) < 4:
                async for r in self._reply(event, "用法: /bridge bindto <目标群号> <来源群号...>"):
                    yield r
                return
            target_gid = str(parts[2])
            source_gids = [str(x) for x in parts[3:] if str(x).strip()]
            bridge = self._get_bridge(target_gid) or {}
            bridge["source_gids"] = source_gids
            bridge.setdefault("mode", "recent")
            bridge.setdefault("recent_count", self.config["default_recent_count"])
            bridge.setdefault("max_chars", self.config["default_max_chars"])
            bridge.setdefault("merge_strategy", "concat")
            self._set_bridge(target_gid, bridge)
            async for r in self._reply(event, f"已绑定目标群 {target_gid} -> 来源群 {', '.join(source_gids)}"):
                yield r
            return

        if sub == "addsrc":
            if len(parts) < 3:
                async for r in self._reply(event, "用法: /bridge addsrc <来源群号...>"):
                    yield r
                return
            bridge = self._get_bridge(gid)
            if not bridge:
                async for r in self._reply(event, "当前群还没有 bridge，请先 /bridge bind ..."):
                    yield r
                return
            current = list(bridge.get("source_gids", []))
            for s in parts[2:]:
                s = str(s)
                if s not in current:
                    current.append(s)
            bridge["source_gids"] = current
            self._set_bridge(gid, bridge)
            async for r in self._reply(event, f"已更新来源群: {', '.join(current)}"):
                yield r
            return

        if sub == "delsrc":
            if len(parts) < 3:
                async for r in self._reply(event, "用法: /bridge delsrc <来源群号...>"):
                    yield r
                return
            bridge = self._get_bridge(gid)
            if not bridge:
                async for r in self._reply(event, "当前群还没有 bridge。"):
                    yield r
                return
            remove_set = {str(x) for x in parts[2:]}
            current = [x for x in bridge.get("source_gids", []) if str(x) not in remove_set]
            bridge["source_gids"] = current
            self._set_bridge(gid, bridge)
            async for r in self._reply(event, f"已更新来源群: {', '.join(current) if current else '(空)'}"):
                yield r
            return

        if sub == "sources":
            bridge = self._get_bridge(gid)
            if not bridge:
                async for r in self._reply(event, "当前群还没有 bridge。"):
                    yield r
                return
            srcs = bridge.get("source_gids", [])
            async for r in self._reply(event, "当前来源群: " + (", ".join(srcs) if srcs else "(空)")):
                yield r
            return

        if sub == "unbind":
            target_gid = str(parts[2]) if len(parts) >= 3 else gid
            self._delete_bridge(target_gid)
            async for r in self._reply(event, f"已解除目标群 {target_gid} 的 bridge。"):
                yield r
            return

        if sub == "mode":
            if len(parts) < 3 or parts[2] not in ("recent", "summary", "mixed"):
                async for r in self._reply(event, "用法: /bridge mode recent|summary|mixed"):
                    yield r
                return
            bridge = self._get_bridge(gid)
            if not bridge:
                async for r in self._reply(event, "当前群还没有 bridge，请先 /bridge bind ..."):
                    yield r
                return
            bridge["mode"] = parts[2]
            self._set_bridge(gid, bridge)
            async for r in self._reply(event, f"已设置 bridge mode = {parts[2]}"):
                yield r
            return

        if sub == "merge":
            if len(parts) < 3 or parts[2] not in ("concat", "interleave"):
                async for r in self._reply(event, "用法: /bridge merge concat|interleave"):
                    yield r
                return
            bridge = self._get_bridge(gid)
            if not bridge:
                async for r in self._reply(event, "当前群还没有 bridge，请先 /bridge bind ..."):
                    yield r
                return
            bridge["merge_strategy"] = parts[2]
            self._set_bridge(gid, bridge)
            async for r in self._reply(event, f"已设置 merge_strategy = {parts[2]}"):
                yield r
            return

        if sub == "recent":
            if len(parts) < 3 or not parts[2].isdigit():
                async for r in self._reply(event, "用法: /bridge recent <数量>"):
                    yield r
                return
            bridge = self._get_bridge(gid)
            if not bridge:
                async for r in self._reply(event, "当前群还没有 bridge，请先 /bridge bind ..."):
                    yield r
                return
            bridge["recent_count"] = int(parts[2])
            self._set_bridge(gid, bridge)
            async for r in self._reply(event, f"已设置 recent_count = {parts[2]}"):
                yield r
            return

        if sub == "maxchars":
            if len(parts) < 3 or not parts[2].isdigit():
                async for r in self._reply(event, "用法: /bridge maxchars <字数>"):
                    yield r
                return
            bridge = self._get_bridge(gid)
            if not bridge:
                async for r in self._reply(event, "当前群还没有 bridge，请先 /bridge bind ..."):
                    yield r
                return
            bridge["max_chars"] = int(parts[2])
            self._set_bridge(gid, bridge)
            async for r in self._reply(event, f"已设置 max_chars = {parts[2]}"):
                yield r
            return

        if sub == "summary":
            if len(parts) < 3:
                async for r in self._reply(event, "用法: /bridge summary set <摘要> | /bridge summary clear [来源群号]"):
                    yield r
                return
            action = parts[2].lower()
            if action == "set":
                content = text.split("summary set", 1)[-1].strip()
                if not content:
                    async for r in self._reply(event, "请提供摘要内容。"):
                        yield r
                    return
                self._set_summary(gid, content)
                async for r in self._reply(event, f"已设置群 {gid} 的摘要。"):
                    yield r
                return
            if action == "clear":
                target_gid = str(parts[3]) if len(parts) >= 4 else gid
                self._clear_summary(target_gid)
                async for r in self._reply(event, f"已清除群 {target_gid} 的摘要。"):
                    yield r
                return
            async for r in self._reply(event, "用法: /bridge summary set <摘要> | /bridge summary clear [来源群号]"):
                yield r
            return

        if sub == "show":
            bridge = self._get_bridge(gid)
            if not bridge:
                async for r in self._reply(event, "当前群还没有 bridge。"):
                    yield r
                return
            srcs = bridge.get("source_gids", [])
            mode = bridge.get("mode", "recent")
            recent_count = int(bridge.get("recent_count", self.config["default_recent_count"]))
            max_chars = int(bridge.get("max_chars", self.config["default_max_chars"]))
            merge_strategy = bridge.get("merge_strategy", "concat")
            context_block = self._build_context_block(srcs, mode, recent_count, max_chars, merge_strategy)
            preview = context_block[:1000] if context_block else "(当前无可注入内容)"
            msg = (
                f"target_gid: {gid}\n"
                f"source_gids: {', '.join(srcs) if srcs else '(空)'}\n"
                f"mode: {mode}\n"
                f"merge_strategy: {merge_strategy}\n"
                f"recent_count: {recent_count}\n"
                f"max_chars: {max_chars}\n"
                f"\n预览:\n{preview}"
            )
            async for r in self._reply(event, msg):
                yield r
            return

        async for r in self._reply(event, "未知子命令，请使用 /bridge 查看帮助。"):
            yield r

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1)
    async def on_all_message(self, event: AstrMessageEvent):
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

            if self.config.get("only_when_wake", True):
                if not self._is_wake_event(event):
                    return

            source_gids = [str(x) for x in bridge.get("source_gids", []) if str(x) != str(gid)]
            if not source_gids:
                return

            mode = bridge.get("mode", "recent")
            recent_count = int(bridge.get("recent_count", self.config["default_recent_count"]))
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

            original_message = text
            injected = self.config["inject_template"].format(
                source_gids=", ".join(source_gids),
                context_block=context_block,
                original_message=original_message,
            )
            self._inject_message_text(event, injected)

            try:
                self.context.logger.info(
                    f"[astrbot_plugin_group_context_bridge] 已注入桥接上下文: "
                    f"target_gid={gid}, source_gids={source_gids}, mode={mode}, merge={merge_strategy}"
                )
            except Exception:
                pass
        except Exception as e:
            try:
                self.context.logger.error(f"[astrbot_plugin_group_context_bridge] 处理消息异常: {e}")
            except Exception:
                pass