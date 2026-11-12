# 开启 postponed annotations，让类型注解在运行时不立刻求值，减少循环引用和兼容性问题。
from __future__ import annotations

# argparse 是 Python 标准库，用来解析命令行参数。
import argparse

from aicompanion.config import load_settings
from aicompanion.llm.base import ChatTurn


def main() -> int:
    """Run a small LLM connectivity check without opening the desktop UI."""

    # 创建命令行参数解析器，用来支持自定义测试消息和是否启用流式输出。
    parser = argparse.ArgumentParser(
        # description 会显示在 --help 的说明文本中。
        description="Test the configured LLM provider and streaming response."
    )
    # 添加一个可选的位置参数 message，用来指定发送给模型的测试消息。
    parser.add_argument(
        # 参数名为 message，在命令行里可以直接写在模块名后面。
        "message",
        # nargs="?" 表示这个参数可填可不填。
        nargs="?",
        # 如果用户不传 message，就使用这句默认测试消息。
        default="你好，请用一句话介绍你现在的状态。",
        # help 会显示在 --help 的参数说明中。
        help="User message sent to the configured model.",
    )
    # 添加 --no-stream 开关，用来测试非流式响应。
    parser.add_argument(
        # 命令行参数名，使用时写成 --no-stream。
        "--no-stream",
        # store_true 表示只要命令行出现这个开关，args.no_stream 就会变成 True。
        action="store_true",
        # 说明这个开关会关闭 streaming，改用普通完整回复。
        help="Use a normal non-streaming response instead of streaming.",
    )
    # 解析命令行参数，得到 args.message 和 args.no_stream。
    args = parser.parse_args()

    try:
        from aicompanion.providers import real_chat_client
        client, resolved = real_chat_client(settings=load_settings())   # 用设置里配置的聊天模型
    except (ValueError, RuntimeError) as error:
        print(f"LLM configuration failed: {error}")
        return 1
    print("LLM diagnostics")
    print(f"- model: {resolved.label}")
    print(f"- base_url: {resolved.base_url}")
    # 打印本次是否使用流式输出；--no-stream 时这里会显示 False。
    print(f"- streaming: {not args.no_stream}")
    print()

    # 这里构造一个最小系统提示词，只用于诊断，不影响正式聊天窗口的角色 prompt。
    system_prompt = (
        "You are a warm AI companion. Reply naturally in Chinese, "
        "and keep the answer concise."
    )
    # 把命令行输入包装成项目统一的 ChatTurn 消息格式。
    messages = [ChatTurn(role="user", content=args.message)]

    # 用 try 包住真实 API 调用，避免网络/API 异常直接打印长 traceback。
    try:
        # 如果用户传入 --no-stream，就走普通非流式接口。
        if args.no_stream:
            # 调用 generate_reply，一次性拿到完整回复文本。
            reply = client.generate_reply(messages=messages, system_prompt=system_prompt)
            # 把完整回复打印到终端。
            print(reply)
            # 返回 0，表示诊断成功。
            return 0

        # 默认走流式接口，逐段接收模型返回的 token。
        for token in client.generate_reply_stream(
            # 传入用户消息列表。
            messages=messages,
            # 传入诊断用系统提示词。
            system_prompt=system_prompt,
        ):
            # end="" 表示不自动换行，flush=True 表示立即显示 token，模拟打字效果。
            print(token, end="", flush=True)
        # 流式输出结束后补一个换行，让终端提示符不会贴在回复后面。
        print()
        # 返回 0，表示诊断成功。
        return 0
    # 捕获所有异常，并转成简洁的诊断失败信息。
    except Exception as error:
        print()
        print(f"LLM diagnostics failed: {error}")
        # 返回 1，表示诊断失败，方便后续脚本或 CI 判断命令是否成功。
        return 1


# 只有直接运行 python -m aicompanion.llm.diagnostics 时，才会执行 main()。
if __name__ == "__main__":
    raise SystemExit(main())
