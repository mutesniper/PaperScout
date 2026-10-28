"""手动启用的真实 PDF 下载与解析检查，不调用模型或使用私有文档。"""
import asyncio
import json
import httpx
from app.papers.service import download_pdf, extract_pdf


async def main():
    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
        data, final_url = await download_pdf('https://arxiv.org/pdf/1706.03762', client)
    doc = await extract_pdf(data)
    print(json.dumps({'source': final_url, 'bytes': len(data), 'pages': doc['page_count'],
        'sections': len(doc['sections']), 'first_titles': [s['title'] for s in doc['sections'][:6]]}, ensure_ascii=True))


if __name__ == '__main__':
    asyncio.run(main())
