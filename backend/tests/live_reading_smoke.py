"""Opt-in real PDF download/parse; no model calls or private documents."""
import asyncio
import json
import httpx
from app.reading import download_pdf, extract_pdf


async def main():
    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
        data, final_url = await download_pdf('https://arxiv.org/pdf/1706.03762', client)
    doc = await extract_pdf(data)
    print(json.dumps({'source': final_url, 'bytes': len(data), 'pages': doc['page_count'],
        'sections': len(doc['sections']), 'first_titles': [s['title'] for s in doc['sections'][:6]]}, ensure_ascii=True))


if __name__ == '__main__':
    asyncio.run(main())
