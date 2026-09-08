from kiki_gateway.panel_status import environment_text
from kiki_gateway.panel_status import panel_details
from kiki_gateway.health_bridge import WearableHealthBridge
import time


def test_missing_weather_is_not_zero():
    assert environment_text({}) == "Weather unavailable\nAQI unavailable"


def test_partial_and_stale_weather():
    text = environment_text({"available": True, "state": "stale", "aqi": 82})
    assert "-- C" in text and "AQI ~82 CPCB (stale)" in text


def test_nonfinite_weather():
    assert "nan" not in environment_text({"available": True, "temperature_c": float("nan")})


def test_environment_cache_expires_without_network():
    bridge = WearableHealthBridge("")
    bridge._environment = {"available": True, "temperature_c": 25}
    bridge._environment_received = time.monotonic()
    assert bridge.environment()["temperature_c"] == 25
    bridge._environment_received -= 121
    assert not bridge.environment()["available"]


def test_detail_pages_contain_real_forecast_and_incoming_messages_only():
    details = panel_details({"available": True, "state": "fresh", "forecast": [
        {"date": "2026-09-07", "temperature_2m_min": 24, "temperature_2m_max": 31,
         "precipitation_probability_max": 70}]}, {"advisories": [{"text": "Battery low"}]},
        [{"content": "Hello", "sender": "Family"}, {"content": "Outgoing", "is_from_me": True}])
    assert "24 to 31 C; rain chance 70%" in details["weather"]
    assert details["alert_count"] == 1
    assert details["whatsapp_count"] == 1
    assert "Outgoing" not in details["whatsapp"]


def test_detail_pages_distinguish_empty_from_unavailable():
    assert "connecting" in panel_details({}, {}, None)["whatsapp"]
    assert "No recent" in panel_details({}, {}, [])["whatsapp"]


def test_long_detail_payloads_are_bounded():
    details = panel_details({}, {}, [{"content": "a" * 10000}] * 20)
    assert len(details["whatsapp"]) <= 2400
    assert "continued on phone" in details["whatsapp"]
