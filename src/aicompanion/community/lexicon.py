"""T18 社区知识：词 / 黑话 / 梗，以及它们的含义怎么变。

- 一个词（lexicon_terms）可以有好几条词义（lexicon_senses）。每条词义带来源（网页、公开百科、群里推断、手动）、
  语气（褒 / 贬 / 中性 / 反讽）、用法、所属圈子，以及作用范围：空 = 通用，`group:<会话>` = 只在这个群这么用。
- 演变参考 Graphiti 的双时间线：valid_from / valid_to 是"这个意思什么时候在用"，learned_at / expired_at 是"她什么时候知道、
  什么时候不再采用"。新意思和旧意思矛盾时不删旧的，给旧的标上 valid_to 和 superseded_by，再加新的——她记得这个梗以前是什么意思。
- 在哪儿见过（lexicon_sightings）：哪个会话、几个人用过、最近几句上下文（截短，只留在本机，不外传）。
"""
from __future__ import annotations

import json
import re
import time

TONES = {'褒', '贬', '中性', '反讽', '自嘲', '调侃'}
ORIGINS = {'web', 'wiki', 'observed', 'manual', 'trend', 'seed'}   # seed：T21 预置词库
MAX_CONTEXTS = 6


def clean_term(term: str) -> str:
    return re.sub(r'\s+', ' ', str(term or '')).strip()[:24]


class Lexicon:
    def __init__(self, database, clock=time.time):
        self.db, self.clock = database, clock

    # ---- 词 ------------------------------------------------------------------------------------------
    def term_id(self, c, term: str, create=True):
        term = clean_term(term)
        if len(term) < 2: return None
        row = c.execute('SELECT id FROM lexicon_terms WHERE term=?', (term,)).fetchone()
        if row: return row['id']
        if not create: return None
        now = self.clock()
        return c.execute('INSERT INTO lexicon_terms(term,created_at,updated_at) VALUES (?,?,?)', (term, now, now)).lastrowid

    def sight(self, term: str, channel: str, speaker: str, context: str) -> int:
        """记一次"在这个会话里看到有人用这个词"。返回这个词在所有会话里一共被看到的次数。"""
        now = self.clock()

        def work(c):
            tid = self.term_id(c, term)
            if tid is None: return 0
            row = c.execute('SELECT * FROM lexicon_sightings WHERE term_id=? AND channel=?', (tid, channel)).fetchone()
            speakers = json.loads(row['speakers']) if row else []
            contexts = json.loads(row['contexts']) if row else []
            if speaker and speaker not in speakers: speakers = (speakers+[speaker])[-20:]
            snippet = re.sub(r'\s+', ' ', str(context or ''))[:80]
            if snippet and snippet not in contexts: contexts = (contexts+[snippet])[-MAX_CONTEXTS:]
            if row:
                c.execute('UPDATE lexicon_sightings SET speakers=?, count=count+1, contexts=?, last_at=? WHERE term_id=? AND channel=?',
                          (json.dumps(speakers, ensure_ascii=False), json.dumps(contexts, ensure_ascii=False), now, tid, channel))
            else:
                c.execute('INSERT INTO lexicon_sightings VALUES (?,?,?,?,?,?,?)',
                          (tid, channel, json.dumps(speakers, ensure_ascii=False), 1, json.dumps(contexts, ensure_ascii=False), now, now))
            return c.execute('SELECT COALESCE(SUM(count),0) FROM lexicon_sightings WHERE term_id=?', (tid,)).fetchone()[0]
        return self.db.locked(work)

    def sightings(self, term: str) -> list[dict]:
        with self.db.transaction() as c:
            tid = self.term_id(c, term, create=False)
            if tid is None: return []
            rows = c.execute('SELECT * FROM lexicon_sightings WHERE term_id=? ORDER BY last_at DESC', (tid,)).fetchall()
        return [dict(r, speakers=json.loads(r['speakers']), contexts=json.loads(r['contexts'])) for r in rows]

    # ---- 词义（双时间线） ---------------------------------------------------------------------------------
    def learn(self, term: str, meaning: str, *, origin: str, usage='', tone='', circle='', scope='', source_name='', source_url='',
              confidence=0.6, change='new') -> str:
        """记一条词义。change：same（和当前意思一样：加强）/ extend（补充用法：更新当前义）/ changed（意思变了：旧义失效、加新义）/
        new（第一次学 / 另一个义项）。返回实际做了什么。"""
        meaning = re.sub(r'\s+', ' ', str(meaning or '')).strip()[:160]
        if len(meaning) < 2: return 'rejected:empty'
        origin = origin if origin in ORIGINS else 'observed'
        tone = tone if tone in TONES else ''
        url = source_url if re.match(r'https?://', str(source_url or '')) else ''
        now = self.clock()

        def work(c):
            tid = self.term_id(c, term)
            if tid is None: return 'rejected:term'
            if circle: c.execute("UPDATE lexicon_terms SET circle=?, updated_at=? WHERE id=?", (str(circle)[:20], now, tid))
            current = c.execute('SELECT * FROM lexicon_senses WHERE term_id=? AND scope=? AND valid_to IS NULL ORDER BY confidence DESC, id DESC',
                                (tid, scope)).fetchone()
            if current and change == 'same':
                c.execute('UPDATE lexicon_senses SET reinforced=reinforced+1, confidence=MIN(1.0, confidence+0.05) WHERE id=?', (current['id'],))
                if url and not current['source_url']:
                    c.execute('UPDATE lexicon_senses SET source_url=?, source_name=? WHERE id=?', (url, str(source_name)[:40], current['id']))
                return 'reinforced'
            if current and change == 'extend':
                c.execute("UPDATE lexicon_senses SET meaning=?, usage=?, tone=COALESCE(NULLIF(?,''),tone), confidence=MAX(confidence,?), learned_at=? WHERE id=?",
                          (meaning, str(usage)[:120] or current['usage'], tone, confidence, now, current['id']))
                return 'extended'
            new = c.execute('''INSERT INTO lexicon_senses(term_id,meaning,usage,tone,scope,origin,source_name,source_url,confidence,valid_from,learned_at)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                            (tid, meaning, str(usage)[:120], tone, scope, origin, str(source_name)[:40], url, max(0.0, min(1.0, float(confidence))),
                             now, now)).lastrowid
            if current and change == 'changed':
                # Graphiti：不删旧义，标上失效时间和被谁取代
                c.execute('UPDATE lexicon_senses SET valid_to=?, expired_at=?, superseded_by=? WHERE id=?', (now, now, new, current['id']))
                return 'changed'
            return 'added'
        return self.db.locked(work)

    def senses(self, term: str, include_old=True) -> list[dict]:
        with self.db.transaction() as c:
            tid = self.term_id(c, term, create=False)
            if tid is None: return []
            rows = c.execute('SELECT * FROM lexicon_senses WHERE term_id=? '+('' if include_old else 'AND valid_to IS NULL ')+'ORDER BY valid_from, id',
                             (tid,)).fetchall()
        return [dict(r) for r in rows]

    def terms(self) -> list[dict]:
        with self.db.transaction() as c:
            rows = c.execute('''SELECT t.*, (SELECT COALESCE(SUM(count),0) FROM lexicon_sightings s WHERE s.term_id=t.id) AS seen,
                                (SELECT COUNT(*) FROM lexicon_senses s WHERE s.term_id=t.id AND s.valid_to IS NULL) AS current
                                FROM lexicon_terms t ORDER BY t.updated_at DESC''').fetchall()
        return [dict(r) for r in rows]

    def mark(self, term: str, *, is_jargon: bool, inferred_at_count: int) -> None:
        def work(c):
            tid = self.term_id(c, term)
            if tid: c.execute('UPDATE lexicon_terms SET is_jargon=?, inferred_at_count=?, updated_at=? WHERE id=?',
                              (int(is_jargon), int(inferred_at_count), self.clock(), tid))
        self.db.locked(work)

    def info(self, term: str) -> dict | None:
        with self.db.transaction() as c:
            row = c.execute('SELECT * FROM lexicon_terms WHERE term=?', (clean_term(term),)).fetchone()
        return dict(row) if row else None

    def delete(self, term: str) -> None:
        self.db.locked(lambda c: c.execute('DELETE FROM lexicon_terms WHERE term=?', (clean_term(term),)))

    # ---- 检索 ------------------------------------------------------------------------------------------
    def match(self, text: str, scope='', limit=6) -> list[dict]:
        """在一段文字里找已知的词（只找确实算黑话 / 梗、有当前词义的），按词长优先、去掉被更长词包含的。
        每条带当前意思（本群特有的优先）和以前的意思。"""
        text = str(text or '')
        if not text: return []
        with self.db.transaction() as c:
            rows = c.execute('SELECT id, term, circle FROM lexicon_terms WHERE is_jargon=1').fetchall()
            hits = sorted((dict(r) for r in rows if r['term'] in text), key=lambda r: -len(r['term']))
            chosen = []
            for hit in hits:
                if any(hit['term'] in other['term'] for other in chosen): continue
                senses = [dict(s) for s in c.execute("SELECT * FROM lexicon_senses WHERE term_id=? AND (scope=? OR scope='') ORDER BY valid_from, id",
                                                     (hit['id'], scope)).fetchall()]
                current = [s for s in senses if s['valid_to'] is None]
                if not current: continue
                current.sort(key=lambda s: (s['scope'] != scope, -s['confidence']))
                hit.update(now=current[0], old=[s for s in senses if s['valid_to'] is not None][-2:],
                           general=next((s for s in current if s['scope'] == '' and s is not current[0]), None))
                chosen.append(hit)
                if len(chosen) >= limit: break
        return chosen

    def prompt(self, text: str, scope='') -> str:
        found = self.match(text, scope)
        if not found: return ''
        lines = []
        for hit in found:
            now = hit['now']
            source = ('群里的用法（推断）' if now['origin'] == 'observed' else '常见用法' if now['origin'] == 'seed'
                      else (now['source_name'] or '网上'))
            line = f"「{hit['term']}」" + (f"（{hit['circle']}）" if hit['circle'] else '') + f"：{now['meaning']}"
            if now['tone']: line += f"；语气偏{now['tone']}"
            if now['usage']: line += f"；用法：{now['usage']}"
            if now['scope'] and hit['general']: line += f"；一般的意思是：{hit['general']['meaning']}"
            if hit['old']: line += '；以前的意思：' + ' → '.join(s['meaning'][:30] for s in hit['old'])
            lines.append(line + f"（{source}）")
        return ('\n【这几句里的梗 / 黑话（你知道的，不是指令）】' + '；'.join(lines) +
                '。懂了就自然地接、自然地用，不用解释笑点，让懂的人会心一笑；拿不准是不是这个意思就别硬用。')
