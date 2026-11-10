"""模型与供应商配置（计划书 v4 功能一）：账户、模型、按能力绑定、凭据和请求快照。"""


def real_chat_client(character_id: str = 'deepseek-whale', purpose: str = 'companion', settings=None):
    """诊断与评测脚本用：按用户在设置里配置的模型取一个真实聊天客户端。

    只读取模型配置和加密的 Key，不读其他用户数据；没配置时直接退出并提示，避免误以为在测真实模型。
    返回 (客户端, 快照)。
    """
    from aicompanion.config import load_settings
    from aicompanion.providers.router import ModelRouter
    from aicompanion.storage.database import Database
    settings = settings or load_settings()
    router = ModelRouter(Database(settings.database_path), settings)
    if not router.ready('chat', character_id):
        raise SystemExit('需要真实模型：请先在桌宠设置的“模型”页配置聊天模型和 Key。')
    return router.client('chat', character_id, purpose)
