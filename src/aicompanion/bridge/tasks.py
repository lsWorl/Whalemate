"""主人 QQ 办事。桥只接收平台身份和消息，运行和审批始终由桌面任务持有。"""
import base64
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
import re
import threading
import time
from uuid import uuid4

from aicompanion.agent import attachments
from aicompanion.agent.approval import Approvals

ANSWER = re.compile(r'(同意全部|同意这类|以后都允许|同意|拒绝)(?:\s+([A-F0-9]{8}))?(?:\s+(.{1,300}))?',re.I)
ID = re.compile(r'[0-9]{5,12}')


def result_notice(ok, text, spoken=''):
    summary = (spoken or text).strip()
    if (len(summary) > 160 or '\n' in summary or len(re.findall(r'[。！？!?]+', summary)) > 2):
        return ('办好了。' if ok else '这件事没办完。') + '详细见桌面办事记录。'
    if not ok:
        return '这件事没办完。' + (summary or '详细见桌面办事记录。')
    return summary or '办好了。'


def bind_owner(db, owner, bot=''):
    """只能由桌面设置调用。离线绑定时，等待已连接的机器人确认自己的账号。"""
    def bind(c):
        previous = str(db.setting_in(c,'bridge_owner:qq','') or '')
        prior = db.setting_in(c, 'qq_task_binding', [])
        if previous != owner or (bot and prior != [owner, bot]):
            c.execute("UPDATE platform_identities SET link_state='owner' WHERE platform='qq' AND link_state='bound'")
        db.set_setting_in(c,'bridge_owner:qq',owner)
        if previous!=owner or not prior or bot:
            db.set_setting_in(c,'qq_task_binding', [owner,bot] if owner else [])
    db.locked(bind)


def peer_of(db, packet):
    if packet.get('platform') != 'qq' or packet.get('group_id'):
        return None
    peer = str(packet.get('bot_account_id') or ''), str(packet.get('sender_id') or '')
    return peer if authorized(db,peer) else None


def authorized(db, peer):
    if not peer or not all(ID.fullmatch(value) for value in peer):
        return False
    bot, owner = peer
    with db.transaction() as c:
        if (db.setting_in(c, 'bridge_owner:qq', '') != owner
                or db.setting_in(c, 'qq_task_binding', []) != [owner, bot]):
            return False
        return bool(c.execute("""SELECT 1 FROM platform_identities qq JOIN platform_identities desk
            ON desk.person_id=qq.person_id AND desk.platform='desktop' AND desk.platform_user_id='owner'
            WHERE qq.platform='qq' AND qq.bot_account_id=? AND qq.platform_user_id=? AND qq.link_state='bound'""",peer).fetchone())


def activate_binding(db, bot):
    if not ID.fullmatch(bot):
        return
    from aicompanion.relationship.service import RelationshipService, PersonRef
    binding = db.get_setting('qq_task_binding',[])
    owner = str(db.get_setting('bridge_owner:qq','') or '')
    if not owner or binding not in ([owner,''],[owner,bot]):
        return
    if authorized(db,(bot,owner)):
        return
    RelationshipService(db).resolve(PersonRef('qq',bot,owner,''), 'deepseek-whale',count=False)
    def activate(c):
        if db.setting_in(c,'bridge_owner:qq','')==owner and db.setting_in(c,'qq_task_binding',[])==binding:
            db.set_setting_in(c,'qq_task_binding',[owner,bot])
            c.execute("""UPDATE platform_identities SET link_state='bound' WHERE platform='qq' AND bot_account_id=?
                         AND platform_user_id=? AND link_state='owner'""",(bot,owner))
    db.locked(activate)


def decode_files(items):
    if not isinstance(items,list) or len(items)>attachments.COUNT_LIMIT:
        raise ValueError(attachments.LIMIT_HINT)
    total, files = 0, []
    for item in items:
        if not isinstance(item,dict):
            raise ValueError('文件格式无效')
        name, encoded = item.get('name'), item.get('base64')
        if (not isinstance(name,str) or len(name)>180 or not name or PureWindowsPath(name).name!=name
                or re.search(r'[\x00-\x1f<>:"/\\|?*]',name) or name.rstrip('. ')!=name
                or re.match(r'(?i)^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)',name) or name.startswith('.')):
            raise ValueError('附件名称无效')
        if Path(name).suffix.lower()[1:] not in attachments.TEXT | attachments.DOCUMENTS | attachments.IMAGES.keys():
            raise ValueError('暂不支持这种附件格式')
        if not isinstance(encoded,str) or len(encoded)>4*((attachments.FILE_LIMIT+2)//3):
            raise ValueError(attachments.LIMIT_HINT)
        try:
            data = base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise ValueError('附件传输内容无效，请重新发送') from error
        total += len(data)
        if len(data)>attachments.FILE_LIMIT or total>attachments.BATCH_LIMIT:
            raise ValueError(attachments.LIMIT_HINT)
        files.append((name,data))
    return files


@dataclass
class Offer:
    peer: tuple
    character: str
    goal: str
    files: list
    message: str


class TaskBridge:
    def __init__(self, db, offer, changed, clock=time.monotonic):
        self.db, self.offer, self.clock = db,offer,clock
        self.approvals = Approvals(changed,clock)
        self.lock = threading.Lock()
        self.notices = {}
        self.claimed = set()
        self.online = None

    def reserve(self, peer, message):
        if not re.fullmatch(r'[-\w:.]{1,96}',message) or not authorized(self.db,peer):
            return False
        with self.db.transaction() as c:
            return bool(c.execute('INSERT OR IGNORE INTO qq_task_turns VALUES (?,?,?)',(*peer,message)).rowcount)

    def submit(self, peer, character, goal, files, message):
        if not self.reserve(peer,message):
            return False
        self.offer(Offer(peer, character, goal, files, message))
        return True

    def notify(self, peer, text, req=None):
        if not authorized(self.db,peer):
            return
        with self.lock:
            self.notices[uuid4().hex] = (peer,text,req,self.clock()+180)

    def heartbeat(self, bot):
        owner = str(self.db.get_setting('bridge_owner:qq',''))
        peer = (bot,owner)
        self.online = (peer,self.clock()) if authorized(self.db,peer) else None
        requests = self.approvals.items()
        with self.lock:
            self.claimed.intersection_update(r.code for r in requests)
            self.notices = {key:row for key,row in self.notices.items() if self.clock()<row[3]
                            and (not row[2] or row[2] in requests)}
            keys = [k for k,r in self.notices.items() if r[0]==peer][:20] if self.online else []
        return {'ids':keys,'active':[r.code for r in requests if self.online and r.peer==peer and r.deadline>self.clock()]}

    def current_peer(self):
        if self.online and self.clock()-self.online[1]<45 and authorized(self.db,self.online[0]):
            return self.online[0]

    def claim(self, bot, key):
        peer = self.current_peer()
        if not peer or peer[0]!=bot:
            return {}
        with self.lock:
            row = self.notices.get(key)
            if not row or row[0]!=peer or self.clock()>=row[3]:
                return {}
            del self.notices[key]
        req = row[2]
        if req and (req.ready.is_set() or self.clock()>=req.deadline):
            return {}
        if req:
            with self.lock:
                self.claimed.add(req.code)
        return {
            'owner': peer[1], 'text': row[1], 'code': req.code if req else '',
            'kind': req.kind if req else '', 'bulk': bool(req and req.rule), 'run': req.run if req else 0,
            'rule': {'category': req.rule.category, 'pattern': req.rule.pattern} if req and req.rule else None,
        }

    def answer(self, packet, peer, match):
        if not peer or not self.reserve(peer,str(packet.get('task_message') or '')):
            return '这条回复没有生效。'
        action, code, reason = match.groups()
        if action=='同意全部' and not code and not reason:
            shown = packet.get('shown_approvals',[])
            if not isinstance(shown,list) or len(shown)>64:
                return '没有可批量确认的项目。'
            with self.lock:
                delivered = self.claimed.intersection(code for code in shown if isinstance(code, str))
            selected = [r for r in self.approvals.items() if r.peer == peer and r.code in delivered and r.rule]
            if not selected:
                return '没有可批量确认的项目。'
            count = self.approvals.batch([r.code for r in selected],selected[0].run,peer)
            return f'已同意 {count} 项。'
        choice = {'同意':'once','同意这类':'always','以后都允许':'persist','拒绝':'no'}.get(action)
        if choice and code and (not reason or choice=='no') and self.approvals.answer(code.upper(),choice,reason or '',peer=peer):
            return '已记录。'
        return '编号无效、已处理或已超时。'
