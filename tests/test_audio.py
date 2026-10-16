import io
import math
import wave
from array import array

import pytest

from services.audio import (SAMPLE_RATE, AudioError, AudioTooLong, decode_to_pcm, duration_seconds, is_silent,
                            pcm_to_ogg, split_pcm)


def _tone(seconds: float, amp: int = 8000, rate: int = SAMPLE_RATE) -> array:
    n = int(seconds * rate)
    return array("h", (int(amp * math.sin(2 * math.pi * 440 * i / rate)) for i in range(n)))


def _wav(samples: array, rate: int = SAMPLE_RATE, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def test_decode_resamples_stereo_44k_to_mono_16k():
    stereo = array("h")
    for s in _tone(2, rate=44100):
        stereo.extend((s, s))
    pcm = decode_to_pcm(_wav(stereo, rate=44100, channels=2), max_seconds=60)
    assert duration_seconds(pcm) == pytest.approx(2, abs=0.05)
    assert not is_silent(pcm)


def test_decode_rejects_garbage():
    with pytest.raises(AudioError):
        decode_to_pcm(b"not audio at all" * 100, max_seconds=60)


def test_decode_too_long():
    with pytest.raises(AudioTooLong):
        decode_to_pcm(_wav(_tone(5)), max_seconds=2)


def test_silence_detected():
    assert is_silent(array("h", [0] * SAMPLE_RATE * 2).tobytes())


def test_split_short_is_single_chunk():
    pcm = _tone(10).tobytes()
    assert split_pcm(pcm) == [pcm]


def test_split_cuts_at_quiet_place_and_keeps_everything():
    # 45 с тона, 0.5 с тишины, ещё 45 с тона: граница первого куска (50 с) должна сдвинуться в тишину.
    samples = _tone(45) + array("h", [0] * (SAMPLE_RATE // 2)) + _tone(45)
    pcm = samples.tobytes()
    chunks = split_pcm(pcm, chunk_seconds=50, search_seconds=8)
    assert b"".join(chunks) == pcm
    assert len(chunks) == 2
    first = duration_seconds(chunks[0])
    assert 45 <= first <= 45.5
    assert all(duration_seconds(c) <= 50 for c in chunks)


def test_ogg_roundtrip_keeps_duration():
    pcm = _tone(7).tobytes()
    ogg = pcm_to_ogg(pcm)
    assert ogg[:4] == b"OggS"
    assert len(ogg) < len(pcm) / 5
    assert duration_seconds(decode_to_pcm(ogg, max_seconds=60)) == pytest.approx(7, abs=0.1)
