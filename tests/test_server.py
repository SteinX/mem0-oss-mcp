import json
import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mem0_oss_mcp import server


class MappingTests(unittest.TestCase):
    def test_backend_fetch_limit_defaults_to_list_fetch_limit(self):
        with patch.dict(server.os.environ, {}, clear=True):
            self.assertEqual(server._read_backend_list_fetch_limit(7500), 7500)

        with patch.dict(server.os.environ, {"MEM0_OSS_BACKEND_LIST_FETCH_LIMIT": "2500"}, clear=True):
            self.assertEqual(server._read_backend_list_fetch_limit(7500), 2500)

        with patch.dict(server.os.environ, {"MEM0_OSS_MEMORIES_TOP_K_LIMIT": "3000"}, clear=True):
            self.assertEqual(server._read_backend_list_fetch_limit(7500), 3000)

    def test_normalize_filters_flattens_metadata(self):
        filters = {
            "AND": [
                {"user_id": "u1"},
                {"app_id": "repo"},
                {"metadata": {"type": "decision"}},
            ]
        }
        self.assertEqual(
            server.normalize_filters(filters),
            {"user_id": "u1", "app_id": "repo", "type": "decision"},
        )

    def test_normalize_filters_preserves_complex_filters(self):
        filters = {"AND": [{"user_id": "u1"}, {"OR": [{"app_id": "repo"}, {"app_id": "other"}]}]}
        self.assertEqual(
            server.normalize_filters(filters),
            {"AND": [{"user_id": "u1"}, {"OR": [{"app_id": "repo"}, {"app_id": "other"}]}]},
        )

    def test_normalize_filters_collapses_single_or(self):
        self.assertEqual(server.normalize_filters({"OR": [{"user_id": "*"}]}), {"user_id": "*"})

    def test_search_memories_flattens_platform_filters_for_backend(self):
        captured = {}
        original_backend = server._backend

        def fake_backend(method, path, body=None, query=None):
            captured.update({"method": method, "path": path, "body": body, "query": query})
            return {"results": []}

        server._backend = fake_backend
        try:
            server.search_memories(
                {
                    "query": "q",
                    "filters": {
                        "AND": [
                            {"user_id": "u1"},
                            {"app_id": "repo"},
                            {"metadata": {"type": "decision"}},
                        ]
                    },
                    "top_k": 1,
                }
            )
        finally:
            server._backend = original_backend

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["path"], "/search")
        self.assertEqual(captured["body"]["filters"], {"user_id": "u1", "app_id": "repo", "type": "decision"})

    def test_add_memory_preserves_expiration_date(self):
        captured = {}
        original_backend = server._backend

        def fake_backend(method, path, body=None, query=None):
            captured.update({"method": method, "path": path, "body": body, "query": query})
            return {"id": "mem-1"}

        server._backend = fake_backend
        try:
            server.add_memory(
                {
                    "text": "temporary session state",
                    "user_id": "u1",
                    "metadata": {"type": "session_state"},
                    "expiration_date": "2999-01-01",
                }
            )
        finally:
            server._backend = original_backend

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["path"], "/memories")
        self.assertEqual(captured["body"]["expiration_date"], "2999-01-01")
        self.assertEqual(captured["body"]["metadata"]["expiration_date"], "2999-01-01")

    def test_search_memories_filters_expired_results(self):
        original_backend = server._backend

        def fake_backend(method, path, body=None, query=None):
            return {
                "results": [
                    {"id": "old", "metadata": {"expiration_date": "2000-01-01"}},
                    {"id": "fresh", "metadata": {"expiration_date": "2999-01-01"}},
                    {"id": "permanent", "metadata": {}},
                ],
                "count": 3,
            }

        server._backend = fake_backend
        try:
            result = server.search_memories({"query": "session"})
        finally:
            server._backend = original_backend

        self.assertEqual([memory["id"] for memory in result["results"]], ["fresh", "permanent"])
        self.assertEqual(result["count"], 2)

    def test_search_memories_overfetches_before_expiration_filtering(self):
        captured = {}
        original_backend = server._backend

        def fake_backend(method, path, body=None, query=None):
            captured.update({"method": method, "path": path, "body": body, "query": query})
            return {
                "results": [
                    {"id": "old-1", "metadata": {"expiration_date": "2000-01-01"}},
                    {"id": "old-2", "metadata": {"expiration_date": "2000-01-01"}},
                    {"id": "fresh-1", "metadata": {"expiration_date": "2999-01-01"}},
                    {"id": "fresh-2", "metadata": {"expiration_date": "2999-01-01"}},
                    {"id": "fresh-3", "metadata": {"expiration_date": "2999-01-01"}},
                ],
                "count": 5,
            }

        server._backend = fake_backend
        try:
            result = server.search_memories({"query": "session", "top_k": 2})
        finally:
            server._backend = original_backend

        self.assertGreater(captured["body"]["top_k"], 2)
        self.assertEqual([memory["id"] for memory in result["results"]], ["fresh-1", "fresh-2"])
        self.assertEqual(result["count"], 2)

    def test_app_scoped_delete_includes_expired_memories(self):
        deleted = []
        original_backend = server._backend

        def fake_backend(method, path, body=None, query=None):
            if method == "GET" and path == "/memories":
                return {
                    "results": [
                        {"id": "old", "user_id": "u1", "metadata": {"app_id": "repo", "expiration_date": "2000-01-01"}},
                        {"id": "fresh", "user_id": "u1", "metadata": {"app_id": "repo"}},
                    ]
                }
            if method == "DELETE" and path.startswith("/memories/"):
                deleted.append(path.rsplit("/", 1)[-1])
                return {"ok": True}
            raise AssertionError((method, path, body, query))

        server._backend = fake_backend
        try:
            result = server.delete_all_memories({"user_id": "u1", "app_id": "repo"})
        finally:
            server._backend = original_backend

        self.assertEqual(deleted, ["old", "fresh"])
        self.assertEqual(result["deleted_ids"], ["old", "fresh"])

    def test_get_memories_filter_values_match_app_id_from_metadata(self):
        memory = {"id": "1", "metadata": {"app_id": "repo", "type": "decision"}}
        self.assertTrue(server._matches(memory, {"app_id": "repo", "type": "decision"}))
        self.assertFalse(server._matches(memory, {"app_id": "other"}))

    def test_get_memories_fetches_large_backend_page_before_local_app_filter(self):
        captured = {}
        original_backend = server._backend
        original_limit = server.Config.list_fetch_limit
        original_backend_limit = server.Config.backend_list_fetch_limit

        def fake_backend(method, path, body=None, query=None):
            captured.update({"method": method, "path": path, "body": body, "query": query})
            if query.get("top_k"):
                return {
                    "results": [
                        {"id": "other", "user_id": "u1", "metadata": {"app_id": "other"}},
                        {"id": "repo-1", "user_id": "u1", "metadata": {"app_id": "repo"}},
                        {"id": "repo-2", "user_id": "u1", "metadata": {"app_id": "repo"}},
                    ]
                }
            return {
                "results": [
                    {"id": "other", "user_id": "u1", "metadata": {"app_id": "other"}},
                ]
            }

        server._backend = fake_backend
        server.Config.list_fetch_limit = 5000
        server.Config.backend_list_fetch_limit = 5000
        try:
            result = server.get_memories(
                {
                    "filters": {"AND": [{"user_id": "u1"}, {"app_id": "repo"}]},
                    "page_size": 1,
                }
            )
        finally:
            server._backend = original_backend
            server.Config.list_fetch_limit = original_limit
            server.Config.backend_list_fetch_limit = original_backend_limit

        self.assertEqual(captured["query"], {"user_id": "u1", "agent_id": None, "run_id": None, "top_k": 5000})
        self.assertEqual(result["count"], 2)
        self.assertEqual([memory["id"] for memory in result["results"]], ["repo-1"])
        self.assertEqual(result["fetch_limit"], 5000)
        self.assertFalse(result["truncated"])
        self.assertTrue(result["complete"])

    def test_get_memories_honors_configured_backend_fetch_limit(self):
        captured = {}
        original_backend = server._backend
        original_limit = server.Config.list_fetch_limit
        original_backend_limit = server.Config.backend_list_fetch_limit

        def fake_backend(method, path, body=None, query=None):
            captured.update({"method": method, "path": path, "body": body, "query": query})
            return {"results": []}

        server._backend = fake_backend
        server.Config.list_fetch_limit = 10000
        server.Config.backend_list_fetch_limit = 2500
        try:
            server.get_memories({"user_id": "u1"})
        finally:
            server._backend = original_backend
            server.Config.list_fetch_limit = original_limit
            server.Config.backend_list_fetch_limit = original_backend_limit

        self.assertEqual(captured["query"]["top_k"], 2500)

    def test_get_memories_retries_lower_limit_when_backend_rejects_configured_limit(self):
        calls = []
        original_backend = server._backend
        original_limit = server.Config.list_fetch_limit
        original_backend_limit = server.Config.backend_list_fetch_limit
        original_retry_limit = server.Config.backend_list_retry_limit

        def fake_backend(method, path, body=None, query=None):
            calls.append(query["top_k"])
            if query["top_k"] == 5000:
                raise server.BackendError(422, "top_k must be less than or equal to 1000")
            return {"results": [{"id": "repo-1", "user_id": "u1", "metadata": {"app_id": "repo"}}]}

        server._backend = fake_backend
        server.Config.list_fetch_limit = 5000
        server.Config.backend_list_fetch_limit = 5000
        server.Config.backend_list_retry_limit = 1000
        try:
            result = server.get_memories({"user_id": "u1", "app_id": "repo"})
        finally:
            server._backend = original_backend
            server.Config.list_fetch_limit = original_limit
            server.Config.backend_list_fetch_limit = original_backend_limit
            server.Config.backend_list_retry_limit = original_retry_limit

        self.assertEqual(calls, [5000, 1000])
        self.assertEqual(result["fetch_limit"], 1000)
        self.assertTrue(result["degraded_fetch_limit"])
        self.assertIn("retried with 1000", result["warning"])

    def test_get_memories_marks_listing_incomplete_when_fetch_window_is_full(self):
        original_backend = server._backend
        original_limit = server.Config.list_fetch_limit
        original_backend_limit = server.Config.backend_list_fetch_limit

        def fake_backend(method, path, body=None, query=None):
            return {
                "results": [
                    {"id": f"repo-{index}", "user_id": "u1", "metadata": {"app_id": "repo"}}
                    for index in range(query["top_k"])
                ]
            }

        server._backend = fake_backend
        server.Config.list_fetch_limit = 3
        server.Config.backend_list_fetch_limit = 3
        try:
            result = server.get_memories({"user_id": "u1", "app_id": "repo", "page_size": 2})
        finally:
            server._backend = original_backend
            server.Config.list_fetch_limit = original_limit
            server.Config.backend_list_fetch_limit = original_backend_limit

        self.assertEqual(result["count"], 3)
        self.assertTrue(result["truncated"])
        self.assertFalse(result["complete"])

    def test_tools_list_contains_official_names(self):
        names = {tool["name"] for tool in server.tool_schema()}
        self.assertEqual(
            names,
            {
                "add_memory",
                "search_memories",
                "get_memories",
                "get_memory",
                "update_memory",
                "delete_memory",
                "delete_all_memories",
                "delete_entities",
                "list_entities",
                "list_events",
                "get_event_status",
            },
        )

    def test_add_memory_schema_exposes_expiration_date(self):
        add_memory = next(tool for tool in server.tool_schema() if tool["name"] == "add_memory")
        properties = add_memory["inputSchema"]["properties"]

        self.assertEqual(properties["expiration_date"]["type"], "string")

    def test_initialize_rpc(self):
        response = server.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(response["result"]["serverInfo"]["name"], "mem0-oss-mcp")

    def test_tools_list_rpc_is_json_serializable(self):
        response = server.handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        json.dumps(response)


if __name__ == "__main__":
    unittest.main()
