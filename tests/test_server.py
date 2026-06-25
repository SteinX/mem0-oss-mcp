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

    def test_initialize_rpc(self):
        response = server.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(response["result"]["serverInfo"]["name"], "mem0-oss-mcp")

    def test_tools_list_rpc_is_json_serializable(self):
        response = server.handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        json.dumps(response)


if __name__ == "__main__":
    unittest.main()
