"""Read-only stdio smoke check against a running backend; no model calls."""
import argparse
import asyncio
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check(base_url):
    params = StdioServerParameters(command=sys.executable,
        args=['-X', 'utf8', str(Path(__file__).with_name('mcp_server.py')), '--base-url', base_url])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = (await client.list_tools()).tools
            print('Tools:', ', '.join(tool.name for tool in tools))
            result = await client.call_tool('list_papers', {})
            if result.isError:
                raise RuntimeError(str(result.content))
            papers = result.structuredContent['papers']
            print(f'Backend connected; {len(papers)} library records. No model calls.')
            if papers:
                result = await client.call_tool('get_paper', {'reading_id': papers[0]['id']})
                if result.isError:
                    raise RuntimeError(str(result.content))
                assert 'sections' not in result.structuredContent
                print('Paper metadata read successfully; full text omitted.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    asyncio.run(check(parser.parse_args().base_url))
