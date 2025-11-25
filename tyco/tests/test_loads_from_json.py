import json

import tyco


def round_trip(payload):
    """Helper that converts JSON → Tyco context → canonical JSON."""
    context = tyco.loads_from_json(json.dumps(payload))
    return context.as_json()


def test_round_trip_primary_example():
    original = {
        "timezone": "UTC",
        "Application": [
            {
                "service": "webserver",
                "profile": "primary",
                "command": "start_app webserver.primary -p 80",
                "host": {"hostname": "prod-01-us", "cores": 64, "hyperthreaded": False, "os": "Debian"},
                "port": {"name": "http_web", "number": 80},
            },
            {
                "service": "database",
                "profile": "mysql",
                "command": "start_app database.mysql -p 3306",
                "host": {"hostname": "prod-02-us", "cores": 32, "hyperthreaded": True, "os": "Fedora"},
                "port": {"name": "http_mysql", "number": 3306},
            },
        ],
        "Host": [
            {"hostname": "prod-01-us", "cores": 64, "hyperthreaded": False, "os": "Debian"},
            {"hostname": "prod-02-us", "cores": 32, "hyperthreaded": True, "os": "Fedora"},
        ],
        "Port": [
            {"name": "http_web", "number": 80},
            {"name": "http_mysql", "number": 3306},
        ],
    }

    converted = round_trip(original)
    assert converted == original


def test_nullable_fields_lifted_from_json():
    original = {
        "Service": [
            {"name": "api", "description": None},
            {"name": "web", "description": "public endpoint"},
        ],
        "metadata": {"region": "us-west-2", "version": 1},
    }

    converted = round_trip(original)
    assert converted["metadata"] == original["metadata"]
    assert converted["Service"][0]["description"] is None
    assert converted["Service"][1]["description"] == "public endpoint"


def test_mixed_arrays_convert_to_structs():
    original = {
        "name": "mixed",
        "values": [1, {"foo": "bar"}],
    }

    converted = round_trip(original)

    assert isinstance(converted["values"], dict)
    assert converted["values"]["_0"] == 1
    assert converted["values"]["_1"] == {"foo": "bar"}
