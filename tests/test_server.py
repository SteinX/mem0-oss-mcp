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
            {"AND": [{"user_id": "u1"}, {"app_id": "repo"}, {"type": "decision"}]},
        )

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
