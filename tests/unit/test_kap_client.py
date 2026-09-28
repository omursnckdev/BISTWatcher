from __future__ import annotations

import asyncio
from datetime import date

import httpx

from bist_quant.data.kap_data import ROW_CAP, KapClient


def test_row_cap_splitting_and_member_resolution():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content or b"{}")
        if request.url.path.endswith("search/combined"):
            return httpx.Response(
                200,
                json=[
                    {
                        "category": "companyOrFunds",
                        "results": [
                            {
                                "searchType": "C",
                                "cmpOrFundCode": "krdma,krdmb,krdmd",
                                "memberOrFundOid": "OID-K",
                            }
                        ],
                    }
                ],
            )
        requests.append((body["fromDate"], body["toDate"]))
        days = (date.fromisoformat(body["toDate"]) - date.fromisoformat(body["fromDate"])).days + 1
        n = ROW_CAP if days > 2 else 10  # windows longer than 2 days hit the cap
        rows = [{"disclosureIndex": f"{body['fromDate']}-{i}"} for i in range(n)]
        return httpx.Response(200, json=rows)

    async def run():
        client = KapClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            members = await client.resolve_members(["KRDMD"])
            rows = await client.disclosures(date(2024, 1, 1), date(2024, 1, 8), ["OID-K"])
        finally:
            await client._client.aclose()
        return members, rows

    members, rows = asyncio.run(run())
    assert members == {"KRDMD": "OID-K"}
    covered = sorted(requests)
    assert all(len(r) == 10 for r in [rows[i : i + 10] for i in range(0, len(rows), 10)])
    assert len(rows) < ROW_CAP  # capped windows were split, not returned truncated
    assert covered[0][0] == "2024-01-01" and max(r[1] for r in covered) == "2024-01-08"
