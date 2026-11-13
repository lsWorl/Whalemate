"""把本地附件副本转换为 Harness 可分段读取的正文。不会执行宏、公式、嵌入对象或外部链接。"""
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile

MAX_XML = 16 * 1024 * 1024
MAX_TEXT = 4 * 1024 * 1024
PAGE_CHARS = 3600


def _xml(book, name, remaining):
    data = bytearray()
    with book.open(name) as stream:
        while chunk := stream.read(min(65536, remaining[0]+1)):
            remaining[0] -= len(chunk)
            if remaining[0] < 0:
                raise ValueError('文档实际解压内容过大，请拆分后再交给她')
            data.extend(chunk)
    markup = data.replace(b'\0', b'').upper()
    if b'<!DOCTYPE' in markup or b'<!ENTITY' in markup:
        raise ValueError('不支持含 DTD 或声明实体的文档')
    return ET.fromstring(data)


def _strings(element):
    return ''.join(node.text or '' for node in element.iterfind('.//{*}t'))


def _office(path, data):
    with ZipFile(BytesIO(data)) as book:
        remaining = [MAX_XML]
        members = book.infolist()
        if len(members) > 2048 or sum(m.file_size for m in members) > MAX_XML:
            raise ValueError('文档解压后过大，请拆分后再交给她')
        if path.suffix.lower() == '.docx':
            for paragraph in _xml(book, 'word/document.xml', remaining).iterfind('.//{*}p'):
                yield _strings(paragraph)
            return
        names = book.namelist()
        strings = ([_strings(si) for si in _xml(book, 'xl/sharedStrings.xml', remaining)]
                   if 'xl/sharedStrings.xml' in names else [])
        workbook = _xml(book, 'xl/workbook.xml', remaining)
        targets = {r.get('Id'): r.get('Target', '') for r in _xml(book, 'xl/_rels/workbook.xml.rels', remaining)
                   if r.get('TargetMode') != 'External'}
        for sheet in workbook.iterfind('.//{*}sheet'):
            rid = next((value for key, value in sheet.attrib.items() if key.endswith('}id')), '')
            target = targets.get(rid, '')
            member = target.lstrip('/') if target.startswith('/') else 'xl/'+target
            if not member.startswith('xl/worksheets/') or '..' in member.split('/'):
                continue
            yield '工作表 '+sheet.get('name', '')
            for cell in _xml(book, member, remaining).iterfind('.//{*}c'):
                value = cell.findtext('{*}v', '')
                if cell.get('t') == 's':
                    if not value.isdecimal() or int(value) >= len(strings):
                        raise ValueError('表格的共享文字索引无效')
                    value = strings[int(value)]
                elif cell.get('t') == 'inlineStr':
                    value = _strings(cell)
                elif not value:
                    value = cell.findtext('{*}f', '')
                if value:
                    yield cell.get('r', '')+' '+value


def _pdf(data):
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtPdf import QPdfDocument
    buffer = QBuffer()
    buffer.setData(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    doc = QPdfDocument(None)
    doc.load(buffer)
    if doc.status() != QPdfDocument.Status.Ready:
        raise ValueError('PDF 加密或损坏，请提供可读的副本')
    try:
        for i in range(doc.pageCount()):
            yield doc.getAllText(i).text()
    finally:
        doc.close()


def document_text(path: Path, data: bytes):
    ext = path.suffix.lower()
    if ext not in ('.pdf', '.docx', '.xlsx'):
        encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
        try:
            result = data.decode(encoding)
        except UnicodeError:
            raise ValueError('文本编码无法识别，请另存为 UTF-8 再添加') from None
        if '\0' in result:
            raise ValueError('文件含二进制内容，请提供文本格式')
        return result
    parts, size = [], 0
    try:
        for part in (_pdf(data) if ext == '.pdf' else _office(path, data)):
            size += len(part)+1
            if size > MAX_TEXT:
                raise ValueError('文档正文过大，请拆分后再交给她')
            if part.strip():
                parts.append(part)
    except (BadZipFile, KeyError, ET.ParseError, RuntimeError, NotImplementedError) as error:
        raise ValueError(f'无法读取 {ext}，请检查文档是否损坏或加密') from error
    if not parts and ext == '.pdf':
        raise ValueError('PDF 没有可提取的文字；扫描件请提供页面图片')
    return '\n'.join(parts)


def read_page(text, offset=1, limit=None, max_chars=PAGE_CHARS):
    """沿用 read_file 的行号语义；把超长行分成短行，保证每一个字都可以翻到。"""
    width = min(400, max_chars//2)
    lines = (line[pos:pos+width] for line in text.splitlines() for pos in range(0, max(1, len(line)), width))
    start = max(1, int(offset or 1))
    count = max(1, int(limit)) if limit is not None else max_chars
    out, used = [], 0
    for number, line in enumerate(lines, 1):
        if number < start:
            continue
        item = f'{number}\t{line}'
        if len(out) >= count or used+len(item)+1 > max_chars-60:
            out.append(f'（还有内容，用 offset={number} 继续读）')
            break
        out.append(item)
        used += len(item)+1
    return '\n'.join(out) or '（空文件或已读完）'
