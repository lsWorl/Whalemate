"""主库内维护索引和缩略图；文件只使用用户已选择的 qq_meme_root。"""
from __future__ import annotations
import hashlib
import json
import os
import tempfile
from pathlib import Path
import time
LICENSES = ('project-original','user-owned','CC0-1.0','CC-BY-4.0','CC-BY-SA-4.0','CC-BY-NC-4.0','kiriyuki-noncommercial-attribution')
SUFFIXES = {'.png','.jpg','.jpeg','.gif','.webp'}
MAX_BYTES = 1024*1024

def inspect(data):
    from aicompanion.bridge.media import inspect_image
    from PySide6.QtCore import QBuffer,QByteArray,QIODevice,Qt
    from PySide6.QtGui import QImageReader
    mime=inspect_image(data)
    buffer=QBuffer(); buffer.setData(QByteArray(data)); buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader=QImageReader(buffer)
    if reader.imageCount()>240: raise ValueError('动画帧数超过 240')
    first=None; frames=0; pixels=0; representative=max(0,reader.imageCount()//2)
    while reader.canRead():
        image=reader.read()
        if image.isNull(): raise ValueError('图片或动画帧损坏')
        if first is None or frames==representative: first=image
        frames+=1; pixels+=image.width()*image.height()
        if pixels>32_000_000: raise ValueError("动画累计像素超过限制")
        if frames>240: raise ValueError('动画帧数超过 240')
        if not reader.supportsAnimation(): break
    if not frames: raise ValueError('图片不可读')
    thumb=first.scaled(128,128,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
    out=QBuffer(); out.open(QIODevice.OpenModeFlag.WriteOnly); thumb.save(out,'PNG')
    return mime,bytes(out.data())

def normalize_metadata(value):
    if not isinstance(value,dict):raise ValueError('元数据必须是对象')
    result=dict(value)
    for key,default,limit in [('author','',200),('source','',500),('license','unknown',80),('semantic','',160),
                              ('image_text','',80),('category','',64),('character_id','deepseek-whale',80)]:
        field=result.get(key,default)
        if not isinstance(field,str):raise ValueError('元数据文本字段无效')
        result[key]=field[:limit]
    for key,default in [('emotions',[]),('intents',[]),('semantic_tags',[]),('allowed_scenes',[]),('blocked_scenes',[]),('audiences',['ordinary'])]:
        field=result.get(key,default)
        if not isinstance(field,(list,tuple)) or len(field)>16 or any(not isinstance(x,str) for x in field):
            raise ValueError('元数据标签必须是有限文本列表')
        result[key]=[x[:40] for x in field]
    tone=result.get('tone_strength',1)
    if type(tone) is not int or not 0<=tone<=3:raise ValueError('语气强度范围为 0～3')
    result['tone_strength']=tone
    return result

class AssetCatalog:
    def __init__(self,database,clock=time.time):
        self.db,self.clock=database,clock
    @property
    def builtin_root(self):
        return Path(__file__).resolve().parents[1]/'characters/deepseek-whale/sprites'
    @property
    def root(self):
        value=str(self.db.get_setting('qq_meme_root','') or '').strip()
        return Path(value).expanduser().resolve() if value else self.builtin_root.resolve()
    def register(self,path,root,metadata=None):
        path,root=Path(path).resolve(),Path(root).resolve(); metadata=normalize_metadata(metadata or {})
        if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in SUFFIXES:
            raise ValueError('文件不在图库中或格式不支持')
        if 'coopanion' in (str(path)+' '+json.dumps(metadata,ensure_ascii=False)).casefold():
            raise ValueError('禁止来源素材')
        with path.open('rb') as file: data=file.read(MAX_BYTES+1)
        if not data or len(data)>MAX_BYTES: raise ValueError('表情超过 1 MiB 或为空')
        mime,thumbnail=inspect(data); digest=hashlib.sha256(data).hexdigest()
        identity=str(metadata.get('provider','local'))+':'+str(metadata.get('original_file_id') or digest)
        if metadata.get('builtin'): identity='builtin:'+path.name
        asset_id='st_'+hashlib.sha256(identity.encode()).hexdigest()[:32]
        with self.db.transaction() as c:
            duplicate=c.execute('SELECT asset_id FROM expression_assets WHERE sha256=? LIMIT 1',(digest,)).fetchone()
            if duplicate:asset_id=duplicate['asset_id']
        license_name=str(metadata.get('license','unknown')); author=str(metadata.get('author','')); source=str(metadata.get('source',''))
        approved=license_name in LICENSES and bool(author and source) and metadata.get('review_status','approved')=='approved'
        metadata.update(license=license_name,author=author,source=source,media_type=mime,
                        original_file_id=str(metadata.get('original_file_id') or path.name))
        with self.db.transaction() as c:
            previous=c.execute('SELECT asset_id FROM expression_assets WHERE asset_id=?',(asset_id,)).fetchone()
            c.execute("""INSERT INTO expression_assets
            (asset_id,revision,root,relative_path,sha256,metadata,thumbnail,review_status,enabled,imported_at)
            VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(asset_id) DO UPDATE SET
            root=excluded.root,relative_path=excluded.relative_path,revision=excluded.revision,
            review_status=CASE WHEN sha256=excluded.sha256 THEN review_status ELSE 'pending_review' END,
            enabled=CASE WHEN sha256=excluded.sha256 THEN enabled ELSE 0 END,
            sha256=excluded.sha256,thumbnail=excluded.thumbnail""",
            (asset_id,digest,str(root),path.relative_to(root).as_posix(),digest,json.dumps(metadata,ensure_ascii=False),
             thumbnail,'approved' if approved else 'pending_review',int(approved),self.clock()))
            c.execute("INSERT INTO expression_asset_revisions VALUES (?,?,?,?,?) ON CONFLICT(asset_id,revision) DO UPDATE SET root=excluded.root,relative_path=excluded.relative_path",
                      (asset_id,digest,str(root),path.relative_to(root).as_posix(),digest))
        return asset_id,bool(previous)
    def scan(self,cancellation=None):
        from aicompanion.bridge.media import BUILTINS
        counts=dict(imported=0,reused=0,invalid=0,pending=0); root=self.root
        if root==self.builtin_root.resolve():
            entries=[(root/name,dict(builtin=True,category=key,semantic=desc,emotions=[key],intents=desc.split('、'),
                     character_id='deepseek-whale',license='project-original',author='AICompanion 原创姿态',source='repository:T5'))
                     for key,(name,desc) in BUILTINS.items()]
        else:
            if not root.is_dir(): return counts
            conf={}; manifest=root/'manifest.json'
            if manifest.is_file() and manifest.stat().st_size<=256*1024:
                try: conf=json.loads(manifest.read_text(encoding='utf-8-sig'))
                except (ValueError,OSError): counts['invalid']+=1
            if not isinstance(conf,dict): conf={}; counts['invalid']+=1
            if not isinstance(conf.get('categories',{}),dict):conf['categories']={};counts['invalid']+=1
            folder=root/'memes' if (root/'memes').is_dir() else root
            if not folder.resolve().is_relative_to(root): return counts
            entries=[]
            for category in sorted(folder.iterdir())[:256]:
                if not category.is_dir() or category.name.startswith('.') or category.name=='aicompanion-library': continue
                if not category.resolve().is_relative_to(root): continue
                raw=(conf.get('categories') or {}).get(category.name,{})
                raw=raw if isinstance(raw,dict) else {}
                for path in sorted(category.iterdir())[:512]:
                    if path.suffix.lower() not in SUFFIXES: continue
                    meta=dict(conf.get('asset_defaults') or {}); meta.update(raw.get('asset_metadata') or {})
                    meta.update(category=category.name,semantic=raw.get('description',category.name))
                    entries.append((path,meta))
        originals=self.original_sprites() if root!=self.builtin_root.resolve() else {}
        for path,metadata in entries[:2048]:
            if cancellation:cancellation.check()
            try:
                # 用户图库里和仓库自带姿态图内容完全相同的文件（例如 T13 新建图库时补入的 6 张）：按原创素材归档。
                # 只认内容哈希，不认目录名或 manifest，用户之后放进同一目录的其他图不会被误标。
                original=originals.get(self._digest(path)) if originals else None
                if original:metadata=dict(metadata,**original)
                aid,reused=self.register(path,root,metadata)
                if original:self._approve_original(aid,original)
                counts['reused' if reused else 'imported']+=1
                if self.get(aid)['review_status']!='approved': counts['pending']+=1
            except (ValueError,OSError,TypeError): counts['invalid']+=1
        return counts
    def original_sprites(self):
        """仓库自带 6 张原创姿态图的内容哈希 → 内置元数据。"""
        from aicompanion.bridge.media import BUILTINS
        result={}
        for key,(name,desc) in BUILTINS.items():
            path=self.builtin_root/name
            try:result[self._digest(path)]=dict(category=key,semantic=desc,emotions=[key],intents=desc.split('、'),
                       character_id='deepseek-whale',license='project-original',author='AICompanion 原创姿态',source='repository:T5')
            except OSError:continue
        return result
    @staticmethod
    def _digest(path):
        with Path(path).open('rb') as file:data=file.read(MAX_BYTES+1)
        return hashlib.sha256(data).hexdigest() if 0<len(data)<=MAX_BYTES else None
    def _approve_original(self,asset_id,original):
        """之前已按"无许可"登记成待审核的同一张原创图，补上原创元数据并通过；用户已拒绝 / 停用的不动。"""
        with self.db.transaction() as c:
            row=c.execute('SELECT metadata,review_status FROM expression_assets WHERE asset_id=?',(asset_id,)).fetchone()
            if not row or row['review_status']!='pending_review':return
            metadata=json.loads(row['metadata']);metadata.update(original)
            c.execute("UPDATE expression_assets SET metadata=?,review_status='approved',enabled=1 WHERE asset_id=?",
                      (json.dumps(metadata,ensure_ascii=False),asset_id))
    def get(self,asset_id):
        with self.db.transaction() as c: row=c.execute('SELECT * FROM expression_assets WHERE asset_id=?',(asset_id,)).fetchone()
        if row is None: return None
        value=dict(row); value['metadata']=json.loads(value['metadata']); return value
    def rows(self,root=None):
        with self.db.transaction() as c:
            rows=c.execute('SELECT * FROM expression_assets WHERE root=? ORDER BY asset_id',(str(Path(root or self.root).resolve()),)).fetchall()
        return [dict(r,metadata=json.loads(r['metadata'])) for r in rows]
    def review(self,asset_id,*,author,source,license,enabled=True):
        if license not in LICENSES or not str(author).strip() or not str(source).strip():
            raise ValueError('需填写作者、来源和明确许可；未知许可不能启用')
        row=self.get(asset_id)
        if row is None: raise ValueError('素材不存在')
        metadata=row['metadata']; metadata.update(author=str(author)[:200],source=str(source)[:500],license=license,review_status='approved')
        if 'coopanion' in json.dumps(metadata,ensure_ascii=False).casefold(): raise ValueError('禁止来源素材')
        with self.db.transaction() as c:
            c.execute('UPDATE expression_assets SET metadata=?,review_status=?,enabled=? WHERE asset_id=?',
                      (json.dumps(metadata,ensure_ascii=False),'approved',int(enabled),asset_id))
    def annotate(self,asset_id,values):
        row=self.get(asset_id)
        if row is None:raise ValueError('素材不存在')
        metadata=row['metadata']
        for key in ('semantic','intents','emotions','character_id'):
            if key in values:metadata[key]=values[key]
        metadata=normalize_metadata(metadata)
        with self.db.transaction() as c:c.execute('UPDATE expression_assets SET metadata=? WHERE asset_id=?',(json.dumps(metadata,ensure_ascii=False),asset_id))

    def enable(self,asset_id,enabled):
        with self.db.transaction() as c:
            c.execute("UPDATE expression_assets SET enabled=? WHERE asset_id=? AND review_status='approved'",(int(enabled),asset_id))
    def resolve(self,asset_id,revision=None,approved=True):
        row=self.get(asset_id)
        if row is None or (approved and (not row['enabled'] or row['review_status']!='approved')): return None
        if revision is not None and row['revision']!=revision:
            if approved:return None
            with self.db.transaction() as c:old=c.execute('SELECT * FROM expression_asset_revisions WHERE asset_id=? AND revision=?',(asset_id,revision)).fetchone()
            if old is None:return None
            row.update(dict(old))
        root=Path(row['root']).resolve(); path=(root/row['relative_path']).resolve()
        if not path.is_relative_to(root) or not path.is_file(): return None
        try:
            with path.open('rb') as file: data=file.read(MAX_BYTES+1)
            if len(data)>MAX_BYTES or hashlib.sha256(data).hexdigest()!=row['sha256']: return None
            from aicompanion.bridge.media import inspect_image
            return data,inspect_image(data)
        except (OSError,ValueError): return None
    def import_image(self,source,metadata):
        metadata=normalize_metadata(metadata);source=Path(source).resolve()
        if 'coopanion' in (str(source)+' '+json.dumps(metadata,ensure_ascii=False)).casefold():raise ValueError('禁止来源素材')
        configured=str(self.db.get_setting('qq_meme_root','') or '').strip()
        if not configured: raise ValueError('请先在聊天平台选择非系统盘图库目录')
        root=Path(configured).expanduser().resolve()
        if os.name=='nt' and root.drive.casefold()==os.getenv('SystemDrive','C:').casefold():
            raise ValueError('请使用已选的非系统盘图库目录')
        source=Path(source)
        if source.suffix.lower() not in SUFFIXES: raise ValueError('不支持的图片格式')
        with source.open('rb') as file: data=file.read(MAX_BYTES+1)
        if len(data)>MAX_BYTES: raise ValueError('表情超过 1 MiB')
        inspect(data); digest=hashlib.sha256(data).hexdigest()
        folder=root/'memes/imported'
        if not folder.resolve().is_relative_to(root):raise ValueError('图库子目录越界')
        folder.mkdir(parents=True,exist_ok=True)
        target=folder/(digest+source.suffix.lower())
        if not target.resolve().is_relative_to(root):raise ValueError('图库文件越界')
        if target.exists():
            if target.stat().st_size>MAX_BYTES:raise ValueError('目标冲突，未覆盖')
            if target.read_bytes()!=data: raise ValueError('目标冲突，未覆盖')
        else:
            fd,temporary=tempfile.mkstemp(prefix='.import-',dir=folder)
            try:
                with os.fdopen(fd,'wb') as file:file.write(data);file.flush();os.fsync(file.fileno())
                if os.name=='nt':os.rename(temporary,target)
                else:os.link(temporary,target)
            except FileExistsError:
                if target.read_bytes()!=data:raise ValueError('目标冲突，未覆盖')
            finally:
                if Path(temporary).exists():Path(temporary).unlink()
        return self.register(target,root,dict(metadata,category=metadata.get('category','imported')))
    def import_pack(self,manifest_path,cancellation=None):
        manifest_path=Path(manifest_path).resolve()
        with manifest_path.open('rb') as file:raw=file.read(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('素材清单超过 1 MiB')
        data=json.loads(raw.decode('utf-8-sig'))
        if not isinstance(data,dict) or data.get('version')!=1 or not isinstance(data.get('assets'),list) or len(data['assets'])>2048:
            raise ValueError('素材清单格式不支持')
        counts=dict(imported=0,reused=0,invalid=0)
        for item in data['assets']:
            if cancellation:cancellation.check()
            try:
                path=(manifest_path.parent/item['file']).resolve()
                if not path.is_relative_to(manifest_path.parent):raise ValueError('素材越界')
                metadata=normalize_metadata(item.get('metadata',{}))
                if 'coopanion' in (str(path)+' '+json.dumps(metadata,ensure_ascii=False)).casefold():raise ValueError('禁止来源素材')
                if path.stat().st_size>MAX_BYTES:raise ValueError('素材超过体积限制')
                if item.get('sha256') and hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('素材哈希不符')
                _,reused=self.import_image(path,metadata)
                counts['reused' if reused else 'imported']+=1
            except (ValueError,OSError,KeyError,TypeError):counts['invalid']+=1
        return counts

    def rebuild(self): return self.scan()
    def clear_thumbnails(self):
        with self.db.transaction() as c: c.execute('UPDATE expression_assets SET thumbnail=NULL')
    def stats(self):
        rows=self.rows()
        return dict(total=len(rows),approved=sum(r['review_status']=='approved' for r in rows),
                    pending=sum(r['review_status']!='approved' for r in rows),
                    thumbnail_bytes=sum(len(r['thumbnail'] or b'') for r in rows))
