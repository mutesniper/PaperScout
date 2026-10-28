"""在资源受限的子进程中提取不可信 PDF 文本的入口。"""
import io
import json
import re
import sys
from pypdf import PdfReader

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 100
MAX_TEXT = 600_000
CHUNK_SIZE = 8000
HEADING = re.compile(r'^(?:\d{1,2}(?:\.\d{1,2}){0,2}\.?\s+[A-Za-z][^.!?]{1,100}|'
                     r'abstract|introduction|background|related work|methods?|methodology|'
                     r'experiments?|results?|discussion|conclusions?|references|acknowledg\w*|'
                     r'摘要|引言|相关工作|方法|实验|结论|参考文献)$', re.I)


def sections_from_pages(pages):
    sections = []
    title = '正文（未识别章节标题）'
    fragments = []
    size = 0

    def flush():
        nonlocal fragments, size
        if fragments:
            sections.append({'id': f's{len(sections) + 1}', 'title': title, 'fragments': fragments})
        fragments, size = [], 0

    for page, text in enumerate(pages, 1):
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if HEADING.fullmatch(line):
                flush()
                title = line
            for start in range(0, len(line), CHUNK_SIZE):
                part = line[start:start + CHUNK_SIZE]
                if size + len(part) + 1 > CHUNK_SIZE:
                    flush()
                if fragments and fragments[-1]['page'] == page:
                    fragments[-1]['text'] += '\n' + part
                else:
                    fragments.append({'page': page, 'text': part})
                size += len(part) + 1
    flush()
    return sections


def parse_pdf(data):
    if len(data) > MAX_BYTES or not data.startswith(b'%PDF-'):
        raise ValueError('仅支持不超过 20 MB 的 PDF 文件。')
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise ValueError('暂不支持加密 PDF，请上传你有权使用的未加密版本。')
    if not 0 < len(reader.pages) <= MAX_PAGES:
        raise ValueError('仅支持 1–100 页的 PDF。')
    texts, total = [], 0
    for page in reader.pages:
        contents = page.get_contents()
        if contents is not None and len(contents.get_data()) > 8 * 1024 * 1024:
            raise ValueError('PDF 页面过于复杂，请拆分后上传。')
        text = (page.extract_text() or '').replace('\x00', '')
        total += len(text)
        if total > MAX_TEXT:
            raise ValueError('PDF 文本过长，请拆分后上传。')
        texts.append(text)
    if sum(len(t.strip()) for t in texts) < 40:
        raise ValueError('未提取到足够文字，可能是扫描件；当前未启用 OCR。')
    warnings = ['章节通过标题规则识别，长章节拆段；双栏、公式和表格可能解析不完整，请核对原 PDF。',
                '页码使用 PDF 文件物理页序号，可能与论文印刷页码不同。']
    empty = [str(i + 1) for i, text in enumerate(texts) if not text.strip()]
    if empty:
        warnings.append('以下页面未提取到文字，不能据此总结：' + '、'.join(empty))
    return {'page_count': len(texts), 'sections': sections_from_pages(texts), 'warnings': warnings}


if __name__ == '__main__':
    try:
        output = parse_pdf(sys.stdin.buffer.read(MAX_BYTES + 1))
    except ValueError as exc:
        output = {'error': str(exc)}
    except Exception:
        output = {'error': 'PDF 无法解析，文件可能损坏或格式不受支持。'}
    sys.stdout.buffer.write(json.dumps(output, ensure_ascii=True).encode('utf-8'))
