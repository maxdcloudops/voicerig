import asyncio

from aiohttp.test_utils import TestClient, TestServer

from voicerig.stub import make_app


def with_client(routes, fn):
    async def go():
        async with TestClient(TestServer(make_app(routes))) as c:
            return await fn(c)
    return asyncio.run(go())


def test_records_calls_and_answers_canned_json():
    async def fn(c):
        r = await c.post("/orders/cancel", json={"order_id": "1"}, headers={"Authorization": "Bearer SECRET"})
        assert r.status == 200 and await r.json() == {"status": "cancelled"}
        calls = await (await c.get("/__calls")).json()
        assert [x["path"] for x in calls] == ["/orders/cancel"] and calls[0]["body"] == {"order_id": "1"}
        assert "SECRET" not in str(calls)                    # headers are never recorded
        await c.post("/__reset")
        assert await (await c.get("/__calls")).json() == []
    with_client({"/orders/cancel": {"json": {"status": "cancelled"}}}, fn)


def test_unmatched_path_answers_ok_and_status_and_globs_work():
    async def fn(c):
        assert (await c.get("/anything")).status == 200
        r = await c.post("/api/v1/boom")
        assert r.status == 503 and await r.json() == {"err": "down"}
    with_client({"/api/*/boom": {"status": 503, "json": {"err": "down"}}}, fn)


def test_non_json_body_does_not_crash():
    async def fn(c):
        r = await c.post("/x", data=b"not json")
        assert r.status == 200
        assert (await (await c.get("/__calls")).json())[0]["body"] == {}
    with_client({}, fn)
