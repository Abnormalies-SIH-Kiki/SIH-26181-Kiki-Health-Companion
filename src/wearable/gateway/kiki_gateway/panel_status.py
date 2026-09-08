"""Small display-only projection of the RPi environment cache (not LLM context)."""
import math


def panel_details(environment, summary, messages=None):
    """Bounded text pages, read-only: never acknowledge messages or act on alerts."""
    env = environment or {}
    weather = [environment_text(env)]
    if env.get("available"):
        weather += [f"Place: {env.get('place') or 'Configured location'}",
                    f"Feels like: {env.get('apparent_temperature_c', '--')} C",
                    f"Reading age: {env.get('age_seconds', '--')} seconds"]
        for row in env.get("forecast", [])[:3]:
            weather.append(f"{row.get('date')}: {row.get('temperature_2m_min', '--')} to "
                           f"{row.get('temperature_2m_max', '--')} C; rain chance "
                           f"{row.get('precipitation_probability_max', '--')}%")
        if not env.get("forecast"):
            weather.append("Forecast unavailable")
    alerts = []
    for row in (summary or {}).get("advisories", [])[:6]:
        alerts.append(f"{row.get('created_at', '')}\n{row.get('text', '')}\n{row.get('advice', '')}")
    fall = ((summary or {}).get("latest") or {}).get("fall") or {}
    if fall:
        alerts.insert(0, f"Fall check: {fall.get('status', 'unknown')}\n{fall.get('captured_at', '')}")
    updates = []
    for row in (messages or [])[:6]:
        if not isinstance(row, dict) or row.get("is_from_me"):
            continue
        content = str(row.get('content') or row.get('text') or '[Media message]')
        excerpt = content[:450] + (" [continued on phone]" if len(content) > 450 else "")
        updates.append(f"{row.get('chat_name') or row.get('sender_name') or row.get('sender') or 'WhatsApp'}"
                       f"\n{row.get('timestamp', '')}\n{excerpt}")
    return {"weather": "\n\n".join(weather)[:1800],
            "alerts": "\n\n---\n\n".join(alerts)[:1800] or "No current health alerts.",
            "whatsapp": "\n\n---\n\n".join(updates)[:2400] or
                ("No recent incoming updates." if messages is not None else "WhatsApp unavailable or still connecting."),
            "alert_count": len(alerts), "whatsapp_count": len(updates)}


def recent_whatsapp():
    from core.self_extend.whatsapp_mcp import get_whatsapp_mcp, call_whatsapp_tool_data
    if not get_whatsapp_mcp().ready:
        return None
    rows = call_whatsapp_tool_data("list_messages", {
        "limit": 12, "page": 0, "include_context": False}, timeout=3)
    return rows if isinstance(rows, list) else None


def environment_text(snapshot):
    if not isinstance(snapshot, dict) or not snapshot.get("available"):
        return "Weather unavailable\nAQI unavailable"

    def number(key):
        value = snapshot.get(key)
        return (f"{value:.0f}" if isinstance(value, (int, float))
                and not isinstance(value, bool) and math.isfinite(value) else "--")

    stale = " (stale)" if snapshot.get("state") != "fresh" else ""
    return (f"{number('temperature_c')} C  Humidity {number('humidity_pct')}%{stale}\n"
            f"AQI ~{number('aqi')} CPCB{stale}")
