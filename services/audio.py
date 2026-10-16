"""Звук любого формата (ogg/opus голосовых, mp4 кружочков, mp3, m4a...) → PCM 16 кГц моно и нарезка на куски.

Декодирует и кодирует PyAV: ffmpeg у него внутри wheel, ставить ffmpeg в систему не нужно.
Длинное аудио режется на куски до 5 минут — по самому тихому месту возле границы, чтобы не разрезать слово:
на таком куске модель отвечает за ~20–30 с и не упирается в лимит длины ответа.
Каждый кусок перед отправкой сжимается в ogg/opus (4 мин ≈ 650 КБ).
"""
from __future__ import annotations

import io
from array import array

import av

SAMPLE_RATE = 16_000
BYTES_PER_SAMPLE = 2  # s16le
BYTES_PER_SECOND = SAMPLE_RATE * BYTES_PER_SAMPLE

CHUNK_SECONDS = 300
SEARCH_SECONDS = 15     # где искать тишину: последние N секунд перед границей куска
WINDOW_MS = 100
SILENCE_LEVEL = 150     # средняя амплитуда s16 ниже этого — по сути тишина


class AudioError(Exception):
    """user_message можно показать пользователю как есть."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


class AudioTooLong(AudioError):
    pass


def decode_to_pcm(data: bytes, max_seconds: int) -> bytes:
    """Возвращает s16le моно 16 кГц. Длиннее max_seconds — AudioTooLong (декодирование прерывается рано)."""
    try:
        container = av.open(io.BytesIO(data))
    except av.FFmpegError as exc:
        raise AudioError("Не получилось открыть файл. Пришлите голосовое, кружок или аудиофайл.") from exc

    with container:
        if not container.streams.audio:
            raise AudioError("В этом файле нет звука.")
        stream = container.streams.audio[0]
        if container.duration and container.duration / av.time_base > max_seconds + 1:
            raise AudioTooLong(_too_long_text(max_seconds))

        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        max_bytes = (max_seconds + 1) * BYTES_PER_SECOND
        out = bytearray()
        try:
            for frame in container.decode(stream):
                for res in resampler.resample(frame):
                    out += _frame_bytes(res)
                if len(out) > max_bytes:
                    raise AudioTooLong(_too_long_text(max_seconds))
            for res in resampler.resample(None):
                out += _frame_bytes(res)
        except av.FFmpegError as exc:
            if not out:
                raise AudioError("Файл повреждён или в неподдерживаемом формате.") from exc
            # Обрезанный в конце файл: то, что успели декодировать, всё равно расшифруем.
    if not out:
        raise AudioError("В этом файле нет звука.")
    return bytes(out)


def _frame_bytes(frame: av.AudioFrame) -> bytes:
    # У плоскости бывает выравнивающий хвост — берём ровно samples * 2 байта.
    return bytes(frame.planes[0])[: frame.samples * BYTES_PER_SAMPLE]


def _too_long_text(max_seconds: int) -> str:
    return f"Файл слишком длинный: можно до {max_seconds // 60} мин за раз."


def pcm_to_ogg(pcm: bytes, bit_rate: int = 24_000) -> bytes:
    """PCM s16le моно 16 кГц → ogg/opus. Для речи 24 кбит/с хватает с запасом."""
    buf = io.BytesIO()
    with av.open(buf, "w", format="ogg") as out:
        stream = out.add_stream("libopus", rate=SAMPLE_RATE)
        stream.layout = "mono"
        stream.bit_rate = bit_rate
        frame = av.AudioFrame(format="s16", layout="mono", samples=len(pcm) // BYTES_PER_SAMPLE)
        frame.planes[0].update(pcm[: frame.samples * BYTES_PER_SAMPLE])
        frame.sample_rate = SAMPLE_RATE
        # opus хочет свой формат сэмплов и кадры фиксированной длины — это делает resampler.
        resampler = av.AudioResampler(format=stream.format.name, layout="mono", rate=SAMPLE_RATE)
        for res in resampler.resample(frame):
            for packet in stream.encode(res):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


def duration_seconds(pcm: bytes) -> float:
    return len(pcm) / BYTES_PER_SECOND


def _level(samples: array, start: int, end: int) -> float:
    if end <= start:
        return 0.0
    return sum(abs(s) for s in samples[start:end]) / (end - start)


def is_silent(pcm: bytes) -> bool:
    samples = array("h", pcm)
    window = SAMPLE_RATE * WINDOW_MS // 1000
    # Есть хотя бы одно окно громче порога — не тишина.
    return all(_level(samples, i, i + window) < SILENCE_LEVEL for i in range(0, len(samples), window))


def split_pcm(pcm: bytes, chunk_seconds: int = CHUNK_SECONDS, search_seconds: int = SEARCH_SECONDS) -> list[bytes]:
    samples = array("h", pcm)
    total = len(samples)
    max_len = chunk_seconds * SAMPLE_RATE
    if total <= max_len:
        return [pcm]

    window = SAMPLE_RATE * WINDOW_MS // 1000
    search = search_seconds * SAMPLE_RATE
    chunks: list[bytes] = []
    start = 0
    while total - start > max_len:
        hard_end = start + max_len
        best_cut, best_level = hard_end, float("inf")
        for w_start in range(hard_end - search, hard_end - window + 1, window):
            level = _level(samples, w_start, w_start + window)
            if level < best_level:
                best_level, best_cut = level, w_start + window // 2
        chunks.append(samples[start:best_cut].tobytes())
        start = best_cut
    chunks.append(samples[start:].tobytes())
    return chunks
