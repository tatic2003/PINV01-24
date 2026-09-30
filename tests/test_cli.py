"""
CLI and Server Entry Point Tests
Project: PINV01-24 - FIUNA

Tests for cli.py (ITSClient, CLI commands) and run_server.py (argument parsing).
"""

import pytest
import json
import sys
import os
from unittest.mock import patch, MagicMock, PropertyMock
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import ITSClient, print_json, cmd_status, cmd_health, cmd_devices_list
from cli import cmd_devices_get, cmd_devices_register, cmd_devices_remove
from cli import cmd_devices_state, cmd_classify, cmd_export, cmd_validate, main
from run_server import parse_args


# =====================
# ITSClient
# =====================

class TestITSClient:
    def test_init_default(self):
        client = ITSClient()
        assert client.base_url == "http://localhost:5000"

    def test_init_custom(self):
        client = ITSClient(host="10.0.0.1", port=8080)
        assert client.base_url == "http://10.0.0.1:8080"

    @patch('cli.requests.get')
    def test_request_get(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"status": "ok"}'
        mock_response.json.return_value = {"status": "ok"}
        mock_get.return_value = mock_response

        client = ITSClient()
        result = client._request("GET", "/health")
        assert result['success'] is True
        assert result['data'] == {"status": "ok"}

    @patch('cli.requests.post')
    def test_request_post(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"id": "dev1"}'
        mock_response.json.return_value = {"id": "dev1"}
        mock_post.return_value = mock_response

        client = ITSClient()
        result = client._request("POST", "/api/devices/register", {"id": "dev1"})
        assert result['success'] is True

    @patch('cli.requests.put')
    def test_request_put(self, mock_put):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"ok": true}'
        mock_response.json.return_value = {"ok": True}
        mock_put.return_value = mock_response

        client = ITSClient()
        result = client._request("PUT", "/api/devices/d1/state", {"state": "active"})
        assert result['success'] is True

    @patch('cli.requests.delete')
    def test_request_delete(self, mock_delete):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"removed": true}'
        mock_response.json.return_value = {"removed": True}
        mock_delete.return_value = mock_response

        client = ITSClient()
        result = client._request("DELETE", "/api/devices/d1")
        assert result['success'] is True

    def test_request_unknown_method(self):
        client = ITSClient()
        result = client._request("PATCH", "/test")
        assert result['success'] is False
        assert 'error' in result

    @patch('cli.requests.get')
    def test_request_connection_error(self, mock_get):
        import requests
        mock_get.side_effect = requests.exceptions.ConnectionError("refused")

        client = ITSClient()
        result = client._request("GET", "/health")
        assert result['success'] is False
        assert 'Connection failed' in result['error']

    @patch('cli.requests.get')
    def test_request_empty_response(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = ''
        mock_get.return_value = mock_response

        client = ITSClient()
        result = client._request("GET", "/test")
        assert result['success'] is True
        assert result['data'] == {}

    @patch('cli.requests.get')
    def test_request_404(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = '{"error": "not found"}'
        mock_response.json.return_value = {"error": "not found"}
        mock_get.return_value = mock_response

        client = ITSClient()
        result = client._request("GET", "/api/devices/none")
        assert result['success'] is False

    @patch('cli.requests.get')
    def test_health(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"status": "healthy"}'
        mock_response.json.return_value = {"status": "healthy"}
        mock_get.return_value = mock_response

        client = ITSClient()
        result = client.health()
        assert result['success'] is True

    @patch('cli.requests.get')
    def test_status(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"node_id": "n1"}'
        mock_response.json.return_value = {"node_id": "n1"}
        mock_get.return_value = mock_response

        client = ITSClient()
        result = client.status()
        assert result['success'] is True

    @patch('cli.requests.get')
    def test_list_devices(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"devices": []}'
        mock_response.json.return_value = {"devices": []}
        mock_get.return_value = mock_response

        client = ITSClient()
        result = client.list_devices()
        assert result['success'] is True

    @patch('cli.requests.post')
    def test_register_device(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"status": "registered"}'
        mock_response.json.return_value = {"status": "registered"}
        mock_post.return_value = mock_response

        client = ITSClient()
        result = client.register_device({"id": "dev1"})
        assert result['success'] is True

    @patch('cli.requests.delete')
    def test_remove_device(self, mock_delete):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"removed": true}'
        mock_response.json.return_value = {"removed": True}
        mock_delete.return_value = mock_response

        client = ITSClient()
        result = client.remove_device("dev1")
        assert result['success'] is True

    @patch('cli.requests.post')
    def test_classify(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"device_type": "ESP32"}'
        mock_response.json.return_value = {"device_type": "ESP32"}
        mock_post.return_value = mock_response

        client = ITSClient()
        result = client.classify({"hardware_type": "ESP32"})
        assert result['success'] is True

    @patch('cli.requests.get')
    def test_export_rdf(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "@prefix : <http://example.org/> ."
        mock_get.return_value = mock_response

        client = ITSClient()
        result = client.export_rdf("turtle")
        assert result['success'] is True
        assert '@prefix' in result['data']

    @patch('cli.requests.get')
    def test_validate(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"conforms": true}'
        mock_response.json.return_value = {"conforms": True}
        mock_get.return_value = mock_response

        client = ITSClient()
        result = client.validate()
        assert result['success'] is True

    @patch('cli.requests.put')
    def test_update_state(self, mock_put):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"success": true}'
        mock_response.json.return_value = {"success": True}
        mock_put.return_value = mock_response

        client = ITSClient()
        result = client.update_state("dev1", "active")
        assert result['success'] is True

    @patch('cli.requests.get')
    def test_get_device(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"id": "dev1"}'
        mock_response.json.return_value = {"id": "dev1"}
        mock_get.return_value = mock_response

        client = ITSClient()
        result = client.get_device("dev1")
        assert result['success'] is True


# =====================
# print_json
# =====================

class TestPrintJson:
    def test_print_json(self, capsys):
        print_json({"key": "value"})
        captured = capsys.readouterr()
        assert '"key": "value"' in captured.out


# =====================
# CLI Commands
# =====================

class TestCLICommands:
    def _make_client(self, method_name, return_value):
        client = MagicMock(spec=ITSClient)
        getattr(client, method_name).return_value = return_value
        return client

    def test_cmd_status_success(self, capsys):
        client = self._make_client('status', {"success": True, "data": {"node_id": "n1"}})
        cmd_status(client, MagicMock())
        output = capsys.readouterr().out
        assert 'n1' in output

    def test_cmd_status_failure(self):
        client = self._make_client('status', {"success": False, "error": "down"})
        with pytest.raises(SystemExit):
            cmd_status(client, MagicMock())

    def test_cmd_health_success(self, capsys):
        client = self._make_client('health', {"success": True, "data": {"status": "healthy"}})
        cmd_health(client, MagicMock())
        output = capsys.readouterr().out
        assert 'healthy' in output.lower()

    def test_cmd_health_failure(self):
        client = self._make_client('health', {"success": False, "error": "unhealthy"})
        with pytest.raises(SystemExit):
            cmd_health(client, MagicMock())

    def test_cmd_devices_list_empty(self, capsys):
        client = self._make_client('list_devices', {
            "success": True,
            "data": {"devices": []}
        })
        cmd_devices_list(client, MagicMock())
        output = capsys.readouterr().out
        assert 'No devices' in output

    def test_cmd_devices_list_with_devices(self, capsys):
        client = self._make_client('list_devices', {
            "success": True,
            "data": {"devices": [
                {"device_id": "dev1", "type": "ESP32", "hardware_type": "ESP32",
                 "ip_address": "1.1.1.1", "state": "active"}
            ]}
        })
        cmd_devices_list(client, MagicMock())
        output = capsys.readouterr().out
        assert 'dev1' in output

    def test_cmd_devices_list_failure(self):
        client = self._make_client('list_devices', {"success": False, "error": "err"})
        with pytest.raises(SystemExit):
            cmd_devices_list(client, MagicMock())

    def test_cmd_devices_get_success(self, capsys):
        client = self._make_client('get_device', {"success": True, "data": {"id": "dev1"}})
        args = MagicMock()
        args.device_id = "dev1"
        cmd_devices_get(client, args)
        output = capsys.readouterr().out
        assert 'dev1' in output

    def test_cmd_devices_get_failure(self):
        client = self._make_client('get_device', {"success": False, "error": "not found"})
        args = MagicMock()
        args.device_id = "none"
        with pytest.raises(SystemExit):
            cmd_devices_get(client, args)

    def test_cmd_devices_register_success(self, capsys):
        client = self._make_client('register_device', {
            "success": True,
            "data": {"status": "registered"}
        })
        args = MagicMock()
        args.json_data = '{"id": "dev1"}'
        cmd_devices_register(client, args)
        output = capsys.readouterr().out
        assert 'registered' in output.lower()

    def test_cmd_devices_register_invalid_json(self):
        client = MagicMock(spec=ITSClient)
        args = MagicMock()
        args.json_data = 'not json'
        with pytest.raises(SystemExit):
            cmd_devices_register(client, args)

    def test_cmd_devices_register_failure(self):
        client = self._make_client('register_device', {
            "success": False, "error": "bad", "data": {"detail": "fail"}
        })
        args = MagicMock()
        args.json_data = '{"id": "dev1"}'
        with pytest.raises(SystemExit):
            cmd_devices_register(client, args)

    def test_cmd_devices_remove_success(self, capsys):
        client = self._make_client('remove_device', {"success": True})
        args = MagicMock()
        args.device_id = "dev1"
        cmd_devices_remove(client, args)
        output = capsys.readouterr().out
        assert 'removed' in output.lower()

    def test_cmd_devices_remove_failure(self):
        client = self._make_client('remove_device', {"success": False, "error": "err"})
        args = MagicMock()
        args.device_id = "dev1"
        with pytest.raises(SystemExit):
            cmd_devices_remove(client, args)

    def test_cmd_devices_state_success(self, capsys):
        client = self._make_client('update_state', {
            "success": True, "data": {"state": "active"}
        })
        args = MagicMock()
        args.device_id = "dev1"
        args.state = "active"
        cmd_devices_state(client, args)
        output = capsys.readouterr().out
        assert 'active' in output

    def test_cmd_devices_state_failure(self):
        client = self._make_client('update_state', {"success": False, "error": "err"})
        args = MagicMock()
        args.device_id = "dev1"
        args.state = "active"
        with pytest.raises(SystemExit):
            cmd_devices_state(client, args)

    def test_cmd_classify_success(self, capsys):
        client = self._make_client('classify', {
            "success": True,
            "data": {
                "device_type": "ESP32Device",
                "confidence": 0.95,
                "suggested_role": "sensor",
                "suggested_capabilities": ["sensing", "wifi"]
            }
        })
        args = MagicMock()
        args.json_data = '{"hardware_type": "ESP32"}'
        cmd_classify(client, args)
        output = capsys.readouterr().out
        assert 'ESP32Device' in output

    def test_cmd_classify_invalid_json(self):
        client = MagicMock(spec=ITSClient)
        args = MagicMock()
        args.json_data = '{bad'
        with pytest.raises(SystemExit):
            cmd_classify(client, args)

    def test_cmd_classify_failure(self):
        client = self._make_client('classify', {"success": False, "error": "err"})
        args = MagicMock()
        args.json_data = '{"hardware_type": "ESP32"}'
        with pytest.raises(SystemExit):
            cmd_classify(client, args)

    def test_cmd_export_turtle(self, capsys):
        client = MagicMock(spec=ITSClient)
        client.export_rdf.return_value = {"success": True, "data": "@prefix : <ex> ."}
        args = MagicMock()
        args.format = "turtle"
        cmd_export(client, args)
        output = capsys.readouterr().out
        assert '@prefix' in output

    def test_cmd_export_json(self, capsys):
        client = MagicMock(spec=ITSClient)
        client.list_devices.return_value = {"success": True, "data": {"devices": []}}
        args = MagicMock()
        args.format = "json"
        cmd_export(client, args)
        output = capsys.readouterr().out
        assert 'devices' in output

    def test_cmd_export_unknown_format(self):
        client = MagicMock(spec=ITSClient)
        args = MagicMock()
        args.format = "csv"
        with pytest.raises(SystemExit):
            cmd_export(client, args)

    def test_cmd_validate_success(self, capsys):
        client = self._make_client('validate', {
            "success": True,
            "data": {
                "conforms": True,
                "violations": [],
                "warnings": []
            }
        })
        cmd_validate(client, MagicMock())
        output = capsys.readouterr().out
        assert 'Conforms' in output

    def test_cmd_validate_with_violations(self, capsys):
        client = self._make_client('validate', {
            "success": True,
            "data": {
                "conforms": False,
                "violations": [{"message": "Missing field"}],
                "warnings": []
            }
        })
        cmd_validate(client, MagicMock())
        output = capsys.readouterr().out
        assert 'Missing field' in output

    def test_cmd_validate_failure(self):
        client = self._make_client('validate', {"success": False, "error": "err"})
        with pytest.raises(SystemExit):
            cmd_validate(client, MagicMock())

    def test_cmd_export_rdf_format(self, capsys):
        client = MagicMock(spec=ITSClient)
        client.export_rdf.return_value = {"success": True, "data": "@prefix : <ex> ."}
        args = MagicMock()
        args.format = "rdf"
        cmd_export(client, args)
        output = capsys.readouterr().out
        assert '@prefix' in output


# =====================
# main() argument parsing
# =====================

class TestMainCLI:
    def test_no_command(self):
        with patch('sys.argv', ['cli.py']):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0


# =====================
# run_server.py parse_args
# =====================

class TestParseArgs:
    def test_default_args(self):
        with patch('sys.argv', ['run_server.py']):
            args = parse_args()
            assert args.host == '0.0.0.0'
            assert args.port == 5000
            assert args.node_id == 'edge-node-01'
            assert args.debug is False
            assert args.config is None
            assert args.classifier == 'rule_based'
            assert args.no_dag is False
            assert args.log_level == 'INFO'

    def test_custom_args(self):
        with patch('sys.argv', [
            'run_server.py',
            '--host', '127.0.0.1',
            '--port', '8080',
            '--node-id', 'gw-01',
            '--debug',
            '--classifier', 'random_forest',
            '--no-dag',
            '--log-level', 'DEBUG'
        ]):
            args = parse_args()
            assert args.host == '127.0.0.1'
            assert args.port == 8080
            assert args.node_id == 'gw-01'
            assert args.debug is True
            assert args.classifier == 'random_forest'
            assert args.no_dag is True
            assert args.log_level == 'DEBUG'

    def test_config_file_arg(self):
        with patch('sys.argv', ['run_server.py', '--config', '/path/to/config.yml']):
            args = parse_args()
            assert args.config == '/path/to/config.yml'

    def test_short_flags(self):
        with patch('sys.argv', ['run_server.py', '-p', '9000', '-d']):
            args = parse_args()
            assert args.port == 9000
            assert args.debug is True
