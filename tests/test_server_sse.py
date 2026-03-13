# SPDX-FileCopyrightText: Copyright (c) Nico Ueberfeldt
#
# SPDX-License-Identifier: MIT

import asyncio

from tests.server_test_support import ServerToolsTestBase


class _FakeSSERequest:
    def __init__(self, last: str = "1"):
        self.query_params = {"last": last}
        self._disconnect_checks = 0

    async def is_disconnected(self) -> bool:
        self._disconnect_checks += 1
        return self._disconnect_checks > 1


class ServerSSETest(ServerToolsTestBase):
    def setUp(self):
        super().setUp()
        self.server._SSE_EVENT_HISTORY.clear()
        self.server._SSE_SUBSCRIBERS.clear()
        self.server._SSE_EVENT_SEQ = 0

    def test_command_runner_publishes_sse_events(self):
        out = self.server.command_runner(["cat", "README.md"])

        self.assertTrue(out["ok"])
        events = list(self.server._SSE_EVENT_HISTORY)
        event_names = [row["event"] for row in events]
        self.assertIn("tool.start", event_names)
        self.assertIn("tool.output", event_names)
        self.assertIn("tool.finish", event_names)

        start = next(row for row in events if row["event"] == "tool.start")
        finish = next(row for row in events if row["event"] == "tool.finish")
        output = next(row for row in events if row["event"] == "tool.output")
        self.assertEqual(start["source"], "command_runner")
        self.assertEqual(finish["source"], "command_runner")
        self.assertEqual(output["stream"], "stdout")
        self.assertIn("# Test Repo", output["chunk"])

    def test_sse_route_replays_recent_events(self):
        self.server._sse_publish("tool.test", source="unit", run_id="abc123")
        request = _FakeSSERequest(last="1")
        response = asyncio.run(self.server.sse_events(request))

        self.assertEqual(response.media_type, "text/event-stream")
        self.assertEqual(response.headers["Cache-Control"], "no-cache")

        async def _read_first_chunk() -> str:
            return await response.body_iterator.__anext__()

        first_chunk = asyncio.run(_read_first_chunk())
        if isinstance(first_chunk, bytes):
            first_chunk = first_chunk.decode("utf-8", errors="replace")

        self.assertIn("event: tool.test", first_chunk)
        self.assertIn('"source": "unit"', first_chunk)
        self.assertIn('"run_id": "abc123"', first_chunk)
