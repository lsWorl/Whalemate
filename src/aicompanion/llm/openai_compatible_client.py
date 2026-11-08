from __future__ import annotations

from collections.abc import Iterator
import json
import socket
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from aicompanion.llm.base import ChatTurn
from aicompanion.domain.cancellation import CancellationToken, ReplyCancelled
from aicompanion.usage import meter


class LLMResponseError(RuntimeError):
    """大模型 API 调用失败时抛出的统一异常。

    这个异常面向 UI 和命令行诊断工具。它的字符串内容应该尽量可读，
    避免把 urllib、socket、JSONDecodeError 这类底层异常原样暴露给用户。
    """


class OpenAICompatibleClient:
    """调用 OpenAI Chat Completions 兼容接口的客户端。

    这个类负责把项目内部的 ChatTurn 转成兼容接口需要的 messages，
    并同时支持普通回复和 Server-Sent Events 流式回复。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.8,
        timeout_seconds: int = 60,
    ) -> None:
        # API key 为空时提前失败，避免真正请求时才得到难懂的 401 错误。
        if not api_key:
            raise ValueError("LLM_API_KEY 不能为空，请在 .env 中配置 API key。")

        # 去掉末尾斜杠，后面拼接 /chat/completions 时更稳定。
        self.base_url = base_url.rstrip("/")
        # 保存 API key，用于构造 Authorization 请求头。
        self.api_key = api_key
        # 保存模型名，例如 glm-5.1、gpt-4o-mini 等。
        self.model = model
        # 保存采样温度，控制回复的随机性。
        self.temperature = temperature
        # 保存请求超时时间，避免网络卡住时程序一直等待。
        self.timeout_seconds = timeout_seconds
        # 流式请求时让接口在最后一块返回用量；个别平台不认这个参数时自动关掉。
        self.stream_usage = True
        self.last_usage = None

    def generate_reply(self, messages: list[ChatTurn], system_prompt: str) -> str:
        """非流式生成完整回复。"""

        # 构造非流式请求体。
        payload = self._build_payload(messages, system_prompt, stream=False)
        # 发送请求并解析 JSON。
        data = self._post_json(payload)
        meter.report(self.model, data.get("usage") if isinstance(data, dict) else None,
                     self._chars(messages, system_prompt))

        try:
            # OpenAI-compatible 非流式响应通常在 choices[0].message.content。
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise LLMResponseError("模型没有返回可用文本，请调整输入或模型配置。")
            return content
        except (KeyError, IndexError, TypeError) as error:
            # 响应是 JSON，但结构不符合预期时，转成项目统一异常。
            raise LLMResponseError(
                "模型响应格式不符合预期，无法读取回复内容。"
                f" 原始响应摘要：{self._shorten(str(data))}"
            ) from error

    def generate_reply_stream(
        self,
        messages: list[ChatTurn],
        system_prompt: str,
        cancellation: CancellationToken | None = None,
    ) -> Iterator[str]:
        """流式请求模型，并逐段产出文本 token。"""

        # 构造流式请求体。
        payload = self._build_payload(messages, system_prompt, stream=True)
        if self.stream_usage:
            payload["stream_options"] = {"include_usage": True}
        # 把请求体封装成 urllib 可以发送的 Request。
        request = self._build_request(payload)
        cancellation = cancellation or CancellationToken()
        usage = None; produced = 0; prompt_chars = self._chars(messages, system_prompt)
        completed = False
        has_text = False
        abort_read = None
        cancellation.check()

        try:
            # urlopen 返回一个可迭代的响应对象，SSE 每一行是一段事件。
            with urlopen(request, timeout=min(self.timeout_seconds, 10)) as response:
                # shutdown 唤醒阻塞读取；close 仍由执行请求的线程负责。
                sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
                if sock:
                    abort_read = lambda: sock.shutdown(socket.SHUT_RDWR)
                    cancellation.register(abort_read)
                # 逐行读取服务端返回的流式内容。
                for raw_line in response:
                    cancellation.check()
                    # SSE 以 utf-8 文本返回；errors="ignore" 避免极端乱码中断程序。
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    # OpenAI-compatible SSE 有效内容通常以 data: 开头。
                    if not line or not line.startswith("data:"):
                        continue

                    # 去掉 data: 前缀，只保留后面的 JSON 或 [DONE]。
                    event_data = line.removeprefix("data:").strip()
                    # [DONE] 表示本次流式响应结束。
                    if event_data == "[DONE]":
                        completed = True
                        break

                    # 从单个 SSE 事件里提取新增文本。
                    token = self._parse_stream_token(event_data)
                    data = json.loads(event_data)
                    if isinstance(data, dict) and data.get("usage"):
                        usage = data["usage"]
                    choices = data.get("choices", [])
                    if choices and choices[0].get("finish_reason") is not None:
                        reason = choices[0]["finish_reason"]
                        if reason != "stop":
                            raise LLMResponseError(f"回复未完整结束（{reason}），已保留收到的内容。")
                        completed = True
                    # 某些事件只包含 role、finish_reason 等信息，没有 content。
                    if token:
                        has_text = True
                        produced += len(token)
                        yield token
                cancellation.check()
                if not completed:
                    raise LLMResponseError("连接提前结束，回复可能不完整，已保留收到的内容。")
                if not has_text:
                    raise LLMResponseError("模型未返回文本，请调整输入或稍后重试。")
        except HTTPError as error:
            # HTTPError 表示服务端明确返回了 4xx/5xx，例如 key 错、模型名错、余额不足。
            built = self._build_http_error(error)
            if error.code == 400 and self.stream_usage and not produced and "stream_options" in str(built):
                # 平台不支持 stream_options：关掉后重试一次，之后改用估算
                self.stream_usage = False
                yield from self.generate_reply_stream(messages, system_prompt, cancellation)
                return
            raise built from error
        except (URLError, socket.timeout, TimeoutError) as error:
            cancellation.check()
            # 网络错误或超时统一转成对用户更友好的说明。
            raise self._build_network_error(error) from error
        except (HTTPException, OSError, UnicodeDecodeError) as error:
            cancellation.check()
            raise LLMResponseError("接收回复时连接中断，已保留收到的内容。") from error
        finally:
            if abort_read:
                cancellation.unregister(abort_read)
            # 不论正常结束、出错还是被打断，已经发生的用量都要记上
            if produced or usage:
                self.last_usage = usage
                meter.report(self.model, usage, prompt_chars, produced)

    @staticmethod
    def _chars(messages, system_prompt) -> int:
        return len(system_prompt or "")+sum(len(m.content) for m in messages)

    # ---- P5：工具调用（agent 通路，非流式） ------------------------------------
    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             cancellation: CancellationToken | None = None, temperature: float | None = None,
             max_tokens: int | None = None, extra: dict | None = None) -> dict:
        """messages 为原始 OpenAI 格式（含 tool / tool_calls）。返回
        {content, tool_calls:[{id,name,arguments}], reasoning, finish_reason, usage}。"""
        cancellation = cancellation or CancellationToken()
        cancellation.check()
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "temperature": self.temperature if temperature is None else temperature}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if extra:
            payload.update(extra)
        request = self._build_request(payload)
        abort = None
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
                if sock:
                    abort = lambda: sock.shutdown(socket.SHUT_RDWR)
                    cancellation.register(abort)
                raw = response.read()
            cancellation.check()
            data = json.loads(raw.decode("utf-8"))
        except HTTPError as error:
            raise self._build_http_error(error) from error
        except (URLError, socket.timeout, TimeoutError) as error:
            cancellation.check()
            raise self._build_network_error(error) from error
        except (HTTPException, OSError) as error:
            cancellation.check()
            raise LLMResponseError("请求模型时连接中断。") from error
        except json.JSONDecodeError as error:
            raise LLMResponseError("模型 API 返回的不是合法 JSON。") from error
        finally:
            if abort:
                cancellation.unregister(abort)
        chars = sum(self._content_chars(m.get("content")) for m in messages)
        try:
            choice = data["choices"][0]; message = choice["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise LLMResponseError("模型响应格式不符合预期：" + self._shorten(str(data))) from error
        content = message.get("content") or ""
        calls = []
        for i, call in enumerate(message.get("tool_calls") or []):
            fn = call.get("function") or {}
            calls.append({"id": call.get("id") or f"call_{i}", "name": fn.get("name") or "",
                          "arguments": fn.get("arguments") or "{}"})
        meter.report(self.model, data.get("usage"), chars, len(content)+sum(len(c["arguments"]) for c in calls))
        return {"content": content, "tool_calls": calls, "reasoning": message.get("reasoning_content") or "",
                "finish_reason": choice.get("finish_reason") or "", "usage": data.get("usage")}

    @staticmethod
    def _content_chars(content) -> int:
        """缺少 usage 时估算用量：图片按视觉 token 估算，不把 base64 当作几十万字文本计费。"""
        if isinstance(content, str): return len(content)
        if isinstance(content, list):
            return sum(len(str(part.get('text') or '')) if part.get('type') == 'text' else
                       2048 if part.get('type') in ('image_url', 'file') else 0
                       for part in content if isinstance(part, dict))
        return 0

    def _build_payload(
        self,
        messages: list[ChatTurn],
        system_prompt: str,
        stream: bool,
    ) -> dict:
        """构建 Chat Completions 请求体。"""

        # 第一条消息放 system prompt，用来控制角色设定和回复风格。
        api_messages = [{"role": "system", "content": system_prompt}]
        # 只把 user/assistant 历史消息传给模型，避免内部无效角色混入请求。
        api_messages.extend(
            {"role": message.role, "content": message.content}
            for message in messages
            if message.role in {"user", "assistant"}
        )

        # 返回 OpenAI-compatible /chat/completions 常用请求结构。
        return {
            "model": self.model,
            "messages": api_messages,
            "temperature": self.temperature,
            "stream": stream,
        }

    def _build_request(self, payload: dict) -> Request:
        """把 Python dict 转换成 urllib 可发送的 HTTP 请求。"""

        # json.dumps 默认会转义中文，但 API 可以正常识别。
        body = json.dumps(payload).encode("utf-8")
        # 构造 POST 请求，目标是兼容接口的 /chat/completions。
        return Request(
            url=f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

    def _post_json(self, payload: dict) -> dict:
        """发送非流式请求并解析 JSON 响应。"""

        # 构造 urllib 请求对象。
        request = self._build_request(payload)
        try:
            # 发起请求并读取完整响应体。
            with urlopen(request, timeout=self.timeout_seconds) as response:
                # 非流式响应应该是一段完整 JSON。
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            # 服务端返回 4xx/5xx 时，提取 body 中的错误信息。
            raise self._build_http_error(error) from error
        except (URLError, socket.timeout, TimeoutError) as error:
            # 网络不可达、DNS 错误、连接超时等都走这里。
            raise self._build_network_error(error) from error
        except json.JSONDecodeError as error:
            # 服务端返回了内容，但不是合法 JSON。
            raise LLMResponseError(
                "模型 API 返回的不是合法 JSON，请检查 base_url 是否指向兼容接口。"
            ) from error

    def _parse_stream_token(self, event_data: str) -> str:
        """从 SSE 事件里提取本次新增的文本片段。"""

        try:
            # 单个 SSE data 通常是一段 JSON。
            data = json.loads(event_data)
            # 流式新增文本通常放在 choices[0].delta.content。
            if not isinstance(data, dict):
                raise TypeError()
            if data.get("error"):
                raise LLMResponseError("平台在回复过程中返回错误：" + self._shorten(self._extract_api_error_message(event_data)))
            choices = data.get("choices")
            if choices == [] and "usage" in data:
                return ""
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise TypeError()
            delta = choices[0].get("delta", {})
            if not isinstance(delta, dict):
                raise TypeError()
            content = delta.get("content")
            if content is None:
                return ""
            if not isinstance(content, str):
                raise TypeError()
            return content
        except json.JSONDecodeError as error:
            # 有些平台异常时会在流中返回非 JSON 文本，这里给出短摘要。
            raise LLMResponseError(
                "模型流式响应中出现无法解析的片段。"
                f" 片段摘要：{self._shorten(event_data)}"
            ) from error
        except (KeyError, IndexError, TypeError) as error:
            # JSON 可以解析，但结构不是预期的 OpenAI-compatible SSE 格式。
            raise LLMResponseError(
                "模型流式响应格式不符合预期，无法读取 delta.content。"
                f" 片段摘要：{self._shorten(event_data)}"
            ) from error

    def _build_http_error(self, error: HTTPError) -> LLMResponseError:
        """把 HTTPError 转换成更容易理解的 LLMResponseError。"""

        # HTTPError 的 body 只能读取一次，所以统一在这里读取。
        body = error.read().decode("utf-8", errors="ignore")
        # 尝试从平台返回的 JSON 中提取 message/code。
        api_message = self._extract_api_error_message(body)
        # 根据 HTTP 状态码补充排查方向。
        hint = self._http_status_hint(error.code)
        # 拼出最终给 UI/命令行显示的错误文本。
        message = f"模型 API 请求失败，HTTP {error.code}。{hint}"
        # 如果平台返回了更具体的错误信息，就附在后面。
        if api_message:
            message = f"{message} 平台返回：{api_message}"
        return LLMResponseError(message)

    def _build_network_error(
        self,
        error: URLError | socket.timeout | TimeoutError,
    ) -> LLMResponseError:
        """把网络异常转换成更容易理解的 LLMResponseError。"""

        # URLError 通常把真正原因放在 reason 字段；其他异常直接转字符串。
        reason = getattr(error, "reason", error)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return LLMResponseError("模型响应超时，请稍后重试；已收到的内容会保留。")
        return LLMResponseError(
            "无法连接模型 API，请检查网络、代理、base_url 或平台服务状态。"
            f" 详细原因：{reason}"
        )

    def _extract_api_error_message(self, body: str) -> str:
        """从 API 错误响应体中提取尽量短的错误说明。"""

        # body 为空时直接返回空字符串。
        if not body:
            return ""

        try:
            # 大多数兼容平台错误响应也是 JSON。
            data = json.loads(body)
        except json.JSONDecodeError:
            # 如果不是 JSON，就返回截断后的原始文本。
            return self._shorten(body)

        # OpenAI 风格通常是 {"error": {"message": "...", "code": "..."}}。
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            error_data = data["error"]
            message = str(error_data.get("message", "")).strip()
            code = str(error_data.get("code", "")).strip()
            if message and code:
                return self._shorten(f"{message} (code: {code})")
            if message:
                return self._shorten(message)
            if code:
                return f"code: {code}"

        # 有些平台可能直接返回 {"message": "..."} 或 {"msg": "..."}。
        if isinstance(data, dict):
            for key in ("message", "msg", "error_msg"):
                value = str(data.get(key, "")).strip()
                if value:
                    return value

        # 兜底返回截断后的 JSON 字符串。
        return self._shorten(str(data))

    def _http_status_hint(self, status_code: int) -> str:
        """根据常见 HTTP 状态码给出排查提示。"""

        # 401 通常是 API key 缺失、错误或过期。
        if status_code == 401:
            return "请检查 LLM_API_KEY 是否正确。"
        # 403 通常是账号权限、模型权限或区域限制问题。
        if status_code == 403:
            return "请检查账号权限、模型权限或平台访问限制。"
        # 404 常见于 base_url 或模型接口路径不正确。
        if status_code == 404:
            return "请检查 LLM_BASE_URL 是否正确，模型接口是否兼容 /chat/completions。"
        # 429 通常表示限流、余额不足或配额不足。
        if status_code == 429:
            return "请求被限流或配额不足，请稍后重试或检查平台额度。"
        # 500 以上通常是平台服务端问题。
        if status_code >= 500:
            return "模型平台服务暂时异常，可以稍后重试。"
        # 其他状态码给出通用提示。
        return "请检查模型配置和平台返回信息。"

    def _shorten(self, text: str, limit: int = 500) -> str:
        """截断过长文本，避免错误信息撑爆聊天窗口。"""

        # 去掉前后空白，让摘要更紧凑。
        compact = text.strip()
        # 不超过限制时原样返回。
        if len(compact) <= limit:
            return compact
        # 超过限制时截断并加省略号。
        return f"{compact[:limit]}..."
