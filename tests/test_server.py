import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mem0_oss_mcp import server


class MappingTests(unittest.TestCase):
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
        server.Config.list_fetch_limit = 1000
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

        self.assertEqual(captured["query"], {"user_id": "u1", "agent_id": None, "run_id": None, "top_k": 1000})
        self.assertEqual(result["count"], 2)
        self.assertEqual([memory["id"] for memory in result["results"]], ["repo-1"])

    def test_get_memories_caps_backend_fetch_limit_at_oss_route_limit(self):
        captured = {}
        original_backend = server._backend
        original_limit = server.Config.list_fetch_limit

        def fake_backend(method, path, body=None, query=None):
            captured.update({"method": method, "path": path, "body": body, "query": query})
            return {"results": []}

        server._backend = fake_backend
        server.Config.list_fetch_limit = 5000
        try:
            server.get_memories({"user_id": "u1"})
        finally:
            server._backend = original_backend
            server.Config.list_fetch_limit = original_limit

        self.assertEqual(captured["query"]["top_k"], 1000)

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
