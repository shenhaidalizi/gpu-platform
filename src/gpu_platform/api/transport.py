import httpx


class Runner:
    def __init__(self, socket):
        self.client = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=str(socket)),
                                        base_url="http://runner", timeout=45)

    async def call(self, method, path, **kwargs):
        response = await self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()
