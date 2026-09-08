import numpy as np

from kiki_gateway.inference import amplify_pcm_s16, sanitize_for_omnivoice


def test_omnivoice_sanitizer_keeps_only_supported_tags():
    text = "[cheerful] [warm] Hi [laughter] there [question-oh]"
    assert sanitize_for_omnivoice(text) == "Hi [laughter] there [question-oh]"


def test_omnivoice_sanitizer_maps_common_expression_aliases():
    assert sanitize_for_omnivoice("[chuckle] okay [gasp]") == (
        "[laughter] okay [surprise-ah]"
    )


def test_speech_gain_raises_quiet_pcm_and_limits_peaks():
    source = np.asarray([1000, -1000, 30000, -30000], dtype="<i2")
    amplified = np.frombuffer(amplify_pcm_s16(source.tobytes(), 3.2), dtype="<i2")
    assert abs(int(amplified[0])) == 3200
    assert int(np.max(np.abs(amplified.astype(np.int32)))) <= 30935


def test_speech_gain_is_linear_below_soft_knee():
    source = np.asarray([-5000, -1000, 0, 1000, 5000], dtype="<i2")
    amplified = np.frombuffer(amplify_pcm_s16(source.tobytes(), 3.2), dtype="<i2")
    np.testing.assert_allclose(amplified, source.astype(np.int32) * 3.2, atol=1)


def test_speech_gain_can_attenuate_for_calibration():
    source = np.asarray([-10000, 10000], dtype="<i2")
    attenuated = np.frombuffer(amplify_pcm_s16(source.tobytes(), 0.5), dtype="<i2")
    np.testing.assert_array_equal(attenuated, [-5000, 5000])
