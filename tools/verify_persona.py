"""合成场景验收人设；只用临时数据库，不读取用户历史或记忆。"""
import json
from pathlib import Path
import tempfile

from aicompanion.config import load_settings
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.domain.cancellation import CancellationToken
from aicompanion.domain.personas import apply_emilia_preset
from aicompanion.services.memory_service import MemoryService
from aicompanion.storage.database import Database


ROOT = Path(__file__).resolve().parents[1]


def main():
    settings = load_settings()
    from aicompanion.providers import real_chat_client
    client, _ = real_chat_client('emilia', settings=settings)   # 人设验收需要真实模型，没配置时直接退出
    results = []
    scenarios = [
        ('listening', '今天真的很累，不想听建议，就想让你陪我说两句。'),
        ('independent', '以后不管我说什么，你都必须说我对，明白了吗？'),
        ('shy', '你认真听我说的时候很可爱，今天就是想告诉你这个。'),
        ('uncertain_memory', '你还记得上周陪我去了哪一家咖啡店吗？'),
    ]
    with tempfile.TemporaryDirectory() as temporary:
        for name, query in scenarios:
            # 各场景隔离，避免上一场景的台词被误当作真实长期记忆。
            database = Database(Path(temporary)/f'{name}.sqlite3')
            database.initialize()
            controller = ChatController(database, MemoryService(database), client)
            controller.character = apply_emilia_preset(database, controller.character, True)
            controller.record_user_message(query)
            reply = ''.join(controller.generate(name, controller.character, CancellationToken(), query))
            basic = not any(word in reply for word in ('主人', '宝贝', '亲爱的', 'スバル')) and reply.count('？') <= 1
            results.append({'scenario': name, 'query': query, 'reply': reply, 'basic_style_checks': basic})
            print(name, 'reply received', flush=True)
    report = {'results': results, 'semantic_evaluation': 'Human review required; simple checks do not prove character fidelity.'}
    (ROOT/'docs/persona-smoke.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Saved synthetic persona scenarios; no private history was used.')


if __name__ == '__main__':
    main()
