// risk_engine.cpp -- see risk_engine.h.
#include "risk_engine.h"

int risk_engine_to_json(const risk_snapshot_t &snap, const char *activity_name,
                         const char *user_id, char *buf, size_t buf_len) {
    JsonDocument doc; // ArduinoJson v7: auto-sized, no manual capacity math needed

    doc["user_id"] = user_id;

    JsonObject summary = doc["summary"].to<JsonObject>();
    summary["avg_hr"] = snap.avg_hr;
    summary["avg_spo2"] = snap.avg_spo2;

    JsonObject activity = doc["activity"].to<JsonObject>();
    activity["state"] = activity_name;
    activity["confidence"] = snap.activity_confidence;

    JsonObject anomalies = doc["anomalies"].to<JsonObject>();
    anomalies["heart_rate"] = snap.hr_anomaly;
    anomalies["spo2"] = snap.spo2_anomaly;
    anomalies["trend"] = snap.trend_anomaly;

    JsonObject fall = doc["fall_detection"].to<JsonObject>();
    fall["detected"] = snap.fall_detected;

    JsonObject risk = doc["risk"].to<JsonObject>();
    risk["score"] = snap.risk_score;
    risk["level"] = risk_engine_level(snap.risk_score);

    JsonArray reasons = doc["reasons"].to<JsonArray>();
    if (snap.fall_detected) reasons.add("fall_confirmed");
    if (snap.hr_anomaly) reasons.add("heart_rate_deviation_persisted");
    if (snap.spo2_anomaly) reasons.add("spo2_deviation_persisted");
    if (snap.trend_anomaly) reasons.add("baseline_trend_drift");

    size_t written = serializeJson(doc, buf, buf_len);
    if (written == 0 || written >= buf_len) return -1;
    return (int)written;
}
