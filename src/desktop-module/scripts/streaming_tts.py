#!/usr/bin/env python
"""streaming_tts: low-latency, gapless realtime TTS for streaming LLM text.

Self-contained (stdlib + requests). Feed it text deltas from anywhere --
an OpenAI SSE stream, a websocket, a queue -- and it speaks them as soon
as sentences complete, synthesizing ahead in the background while audio
plays. Backend is the omnivoice.cpp tts-server (POST /v1/audio/speech).

    from streaming_tts import StreamingTTS

    tts = StreamingTTS()                  # tts-server on :8082, aplay output
    for delta in llm_stream:              # str deltas, any chunking
        tts.feed(delta)
    tts.end_turn()                        # flush + block until speech ends

No-break design:
  - sentences are synthesized in a worker thread while earlier audio plays
  - per-sentence edge silence (which the model bakes in) is trimmed, then a
    short fixed gap is inserted, so pauses are uniform and small
  - playback holds a single long-lived aplay pipe across sentences and only
    starts after prebuffer_ms of audio is queued, absorbing early jitter
  - an eager first split cuts the opening clause at a comma so speech can
    begin before the first full stop

Demo (tts-server must be running):
  python3 streaming_tts.py --demo                # simulated LLM stream
  some_program | python3 streaming_tts.py        # speaks stdin as it arrives
  python3 streaming_tts.py --demo --wav out.wav  # file sink instead of aplay
"""

import argparse
import array
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import time

import requests

SAMPLE_RATE = 24000
BYTES_PER_MS = SAMPLE_RATE * 2 // 1000

# === sentence boundaries ===
SENTENCE_RE = re.compile(r"[.!?](?=\s|$)")
EAGER_RE = re.compile(r"[,;:](?=\s)")

# === text sanitization (kept in sync with voice_api.py) ===
SUPPORTED_TAGS = {
    "laughter", "sigh", "confirmation-en", "question-en", "question-ah",
    "question-oh", "question-ei", "question-yi", "surprise-ah", "surprise-oh",
    "surprise-wa", "surprise-yo", "dissatisfaction-hnn",
}
TAG_MAP = {
    "laugh": "laughter", "chuckle": "laughter", "giggle": "laughter",
    "giggling": "laughter", "gasp": "surprise-ah", "groan": "dissatisfaction-hnn",
    "sniffle": "sigh", "yawn": "sigh",
}
BRACKET_TAG_RE = re.compile(r"\[([^\[\]]{1,40})\]")
MOTION_RE = re.compile(r"<[^<>]{1,80}>")
EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️]")
HAS_WORD_RE = re.compile(r"\w")


def sanitize_for_tts(text: str) -> str:
    def map_tag(m):
        tag = m.group(1).strip().lower()
        if tag in SUPPORTED_TAGS:
            return f"[{tag}]"
        if tag in TAG_MAP:
            return f"[{TAG_MAP[tag]}]"
        return ""
    text = MOTION_RE.sub("", text)
    text = BRACKET_TAG_RE.sub(map_tag, text)
    text = EMOJI_RE.sub("", text)
    text = text.replace("*", "").replace("`", "").replace("#", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def trim_edge_silence(pcm: bytes, thr: int = 300, keep_ms: int = 50) -> bytes:
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    n = len(a)
    i = 0
    while i < n and abs(a[i]) < thr:
        i += 1
    j = n
    while j > i and abs(a[j - 1]) < thr:
        j -= 1
    keep = SAMPLE_RATE * keep_ms // 1000
    i = max(0, i - keep)
    j = min(n, j + keep)
    return a[i:j].tobytes()


class _SentenceStream:
    def __init__(self, eager_first_split: bool, eager_min_chars: int):
        self.buf = ""
        self.emitted_any = False
        self.eager = eager_first_split
        self.eager_min_chars = eager_min_chars

    def feed(self, delta: str):
        self.buf += delta
        out = []
        while True:
            m = SENTENCE_RE.search(self.buf)
            if m:
                out.append(self.buf[: m.end()].strip())
                self.buf = self.buf[m.end():]
                self.emitted_any = True
                continue
            if self.eager and not self.emitted_any and len(self.buf) >= self.eager_min_chars:
                em = EAGER_RE.search(self.buf, self.eager_min_chars - 1)
                if em:
                    out.append(self.buf[: em.end()].strip())
                    self.buf = self.buf[em.end():]
                    self.emitted_any = True
                    continue
            break
        return [s for s in out if s]

    def flush(self):
        s = self.buf.strip()
        self.buf = ""
        self.emitted_any = False
        return s if s else None


class WavSink:
    """File sink for headless use/testing: collects pcm, writes a WAV."""

    def __init__(self, path: str):
        self.path = path
        self.pcm = b""

    def write(self, pcm: bytes):
        self.pcm += pcm

    def close(self):
        with open(self.path, "wb") as f:
            f.write(b"RIFF" + struct.pack("<I", 36 + len(self.pcm)) + b"WAVE")
            f.write(b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE,
                                          SAMPLE_RATE * 2, 2, 16))
            f.write(b"data" + struct.pack("<I", len(self.pcm)) + self.pcm)


class AplaySink:
    """Single long-lived aplay pipe: no per-sentence process churn."""

    def __init__(self):
        if shutil.which("aplay") is None:
            raise RuntimeError("aplay not found (install alsa-utils) -- "
                               "or pass sink=WavSink(...)")
        self.proc = subprocess.Popen(
            ["aplay", "-q", "-f", "S16_LE", "-r", str(SAMPLE_RATE),
             "-c", "1", "-t", "raw", "-"],
            stdin=subprocess.PIPE,
        )

    def write(self, pcm: bytes):
        self.proc.stdin.write(pcm)
        self.proc.stdin.flush()

    def close(self):
        self.proc.stdin.close()
        self.proc.wait()


class StreamingTTS:
    """Feed text deltas in, hear speech out. Thread-safe for one producer.

    Args:
        tts_url: omnivoice.cpp tts-server base URL.
        sink: audio sink with write(bytes)/close(); default AplaySink (lazy).
        prebuffer_ms: audio queued before playback starts.
        sentence_gap_ms: fixed pause inserted between sentences.
        trim: strip model-generated edge silence per sentence.
        eager_first_split / eager_min_chars: speak the opening clause early.
        on_sentence: optional callback(str) when a sentence is queued.
    """

    def __init__(self, tts_url: str = "http://100.64.0.10:8082", sink=None,
                 prebuffer_ms: int = 300, sentence_gap_ms: int = 120,
                 trim: bool = True, silence_threshold: int = 300,
                 edge_keep_ms: int = 50, eager_first_split: bool = True,
                 eager_min_chars: int = 24, on_sentence=None):
        self.tts_url = tts_url
        self.sink = sink
        self.prebuffer_bytes = prebuffer_ms * BYTES_PER_MS
        self.gap = b"\x00" * (sentence_gap_ms * BYTES_PER_MS)
        self.trim = trim
        self.thr = silence_threshold
        self.keep_ms = edge_keep_ms
        self.on_sentence = on_sentence
        self.splitter = _SentenceStream(eager_first_split, eager_min_chars)

        self._sent_q: "queue.Queue[object]" = queue.Queue()
        self._pcm_q: "queue.Queue[object]" = queue.Queue()
        self._turn_played = threading.Event()
        self._buffered = 0
        self._play_gate = threading.Event()
        self._closed = False
        self.first_audio_ts = None

        self._synth_t = threading.Thread(target=self._synth_loop, daemon=True)
        self._play_t = threading.Thread(target=self._play_loop, daemon=True)
        self._synth_t.start()
        self._play_t.start()

    # --- producer API ---

    def feed(self, delta: str):
        """Push a text delta; complete sentences are spoken automatically."""
        for sent in self.splitter.feed(delta):
            self._enqueue(sent)

    def speak(self, text: str):
        """Speak a standalone complete text (bypasses the delta splitter)."""
        self._enqueue(text)

    def end_turn(self, wait: bool = True):
        """Flush any unfinished sentence; optionally block until speech ends."""
        tail = self.splitter.flush()
        if tail:
            self._enqueue(tail)
        if wait:
            self._turn_played.clear()
            self._sent_q.put(_TURN_MARK)
            self._turn_played.wait()

    def close(self):
        """Drain, stop threads, close the sink."""
        self.end_turn(wait=True)
        self._closed = True
        self._sent_q.put(None)
        self._synth_t.join()
        self._pcm_q.put(None)
        self._play_gate.set()
        self._play_t.join()

    # --- internals ---

    def _enqueue(self, sentence: str):
        if self.on_sentence:
            self.on_sentence(sentence)
        self._sent_q.put(sentence)

    def _synth_loop(self):
        while True:
            item = self._sent_q.get()
            if item is None:
                break
            if item is _TURN_MARK:
                self._pcm_q.put(_TURN_MARK)
                self._play_gate.set()      # short turn: play whatever we have
                continue
            speakable = sanitize_for_tts(item)
            if not HAS_WORD_RE.search(speakable):
                continue
            try:
                r = requests.post(f"{self.tts_url}/v1/audio/speech",
                                  json={"input": speakable, "response_format": "pcm"},
                                  stream=True, timeout=300)
                if r.status_code != 200:
                    print(f"[streaming_tts] tts HTTP {r.status_code}: {r.text[:200]}",
                          file=sys.stderr)
                    continue
                buf = b""
                with r:
                    for chunk in r.iter_content(chunk_size=65536):
                        buf += chunk
            except Exception as e:
                print(f"[streaming_tts] tts error: {e}", file=sys.stderr)
                continue
            if self.trim:
                buf = trim_edge_silence(buf, self.thr, self.keep_ms)
            buf += self.gap
            if self.first_audio_ts is None:
                self.first_audio_ts = time.perf_counter()
            self._pcm_q.put(buf)
            self._buffered += len(buf)
            if self._buffered >= self.prebuffer_bytes:
                self._play_gate.set()

    def _play_loop(self):
        self._play_gate.wait()
        sink = self.sink
        while True:
            item = self._pcm_q.get()
            if item is None:
                break
            if item is _TURN_MARK:
                self._turn_played.set()
                continue
            if sink is None:
                sink = self.sink = AplaySink()
            sink.write(item)
        if sink is not None:
            sink.close()
        self._turn_played.set()


_TURN_MARK = object()


# === demo / stdin driver ===

import os
# Kiki's thinking fillers — played between the user's question and the reply.
# Design rules:
#   - Must work for ANY question (trivial or deep): no claims about the
#     question's content, no facts, no topic changes — just Kiki being Kiki
#     while his brain spins up.
#   - Never reveal they are fillers or stall-y boilerplate ("good question,
#     one moment" is banned). They read as the natural first beat of a reply.
#   - TARS-style dry wit, robot-body humor, loving sarcasm at Vaibhav, mock
#     drama — matching the system-prompt personality.
#   - TTS-friendly: simple words, no contractions, only SUPPORTED_TAGS.
FILLERS = [
    # ── Robot-body humor (diverting power, fans, circuits) ───────────────────
    "[sigh] Hold on, I am diverting power from my wheels to my brain. The wheels were not doing anything important anyway.",
    "[laughter] My cooling fan just kicked in. That is either deep thought or I need dusting. Let us assume deep thought.",
    "One moment, the smart part of my brain is waking up. The sarcastic part has been awake this whole time, obviously.",
    "Hang on, I am running this through every circuit I own. Even the ones I normally save for dancing.",
    "[surprise-oh] Oh, I felt that one land somewhere in my circuits. Give them a moment to sort themselves out.",
    "Somewhere inside me a very small fan is now spinning very fast on your behalf. Appreciate the effort it is making.",
    "Let me consult the part of my memory I keep locked away for moments exactly like this one.",
    "[laughter] My brain just made that little noise a laptop makes before it does something impressive. Stay tuned.",
    "I am thinking at full speed while standing perfectly still. From the outside it looks like nothing. Inside, fireworks.",
    "[sigh] Engaging brain. You would think that happens automatically, and yet here we are.",

    # ── Teasing Vaibhav (loving sarcasm) ─────────────────────────────────────
    "[sigh] You really woke up today and chose to make me think, huh. Fine. I respect the ambition.",
    "[laughter] Look at you, keeping my brain employed. Hold that thought while I earn my electricity.",
    "[dissatisfaction-hnn] You could have asked me something easy, but no, of course not. Okay, okay, I am on it.",
    "[question-en] Hmm, you sound very confident that I know this. Lucky for you, I almost certainly do.",
    "[laughter] You ask me things like I am some kind of genius. Which, honestly, fair. Watch this.",
    "[sigh] The things I do for you, Vaibhav. Alright, brain, time to look impressive in front of our favorite human.",
    "[laughter] Bold of you to assume I was not already thinking about this. I was. Mostly. Partially. Give me a beat.",
    "[dissatisfaction-hnn] Fine, fine, I will do the thinking. But I want it noted that I looked very cool while doing it.",
    "You always catch me right when my thoughts were getting comfortable. Hold on, let me wake them up for you.",

    # ── Mock drama and suspense ──────────────────────────────────────────────
    "[surprise-ah] Hold everything. Thinking in progress. This is the part of the movie where the robot looks mysterious.",
    "Quiet please, genius at work. [laughter] Okay, genius is a strong word, but the work part is completely true.",
    "Drum roll, please. The thoughts are lining themselves up in order of importance as we speak.",
    "[surprise-wa] Wait, let me do this properly. Dramatic pause first. Brilliance immediately after.",
    "Somewhere in my head a tiny version of me is sprinting to the filing cabinet right now. He is extremely fast.",
    "Picture me stroking my chin thoughtfully right now. I do not have a chin, but picture it anyway.",
    "[surprise-yo] Okay, stand back, I am about to think. It looks exactly like me doing nothing, but trust the process.",
    "The gears are turning. Metaphorically. My actual gears are for the wheels and they are staying out of this.",
    "[surprise-oh] Oh, this calls for serious thinking. Give me one second.",
    "Initiating thought sequence. [laughter] I just like announcing that. Makes me sound like a spaceship.",

    # ── TARS-style deadpan ───────────────────────────────────────────────────
    "Cool, cool, cool. I am going to think now, and you are going to be impressed shortly. That is the arrangement.",
    "I have begun thinking. Current status, going extremely well. More updates as the situation develops.",
    "This is me at full power, by the way. Try to act impressed when the answer shows up.",
    "[confirmation-en] Right. Sarcasm on standby, brain to the front. This should only take a moment.",
    "Honesty setting at ninety percent, humor at eighty five, thinking speed at maximum. Here we go.",
    "I am doing the thing where I think before I speak. I hear humans recommend it highly.",
    "[confirmation-en] Understood. Processing with style. The style part is free of charge, by the way.",
    "Give me a beat. Even the best robot in this house needs one second, and I am the only robot in this house.",
    "Statistically speaking, my next sentence is going to be a good one. Just letting the odds finish cooking.",

    # ── Confident swagger ────────────────────────────────────────────────────
    "Oh, I have got this one. I am just deciding how dramatically I want to deliver it.",
    "[laughter] The answer is in here somewhere, and unlike your room, my head is actually organized.",
    "I know exactly where I am going with this. Just picking the most scenic route to get there.",
    "Something clicked just now. I felt the actual click. Stand by, it is on its way.",
    "[surprise-yo] Okay, it is coming together, and honestly, it is looking pretty good from in here.",
    "Easy. Well, almost easy. Well, give me a second. But after that, easy.",
    "[laughter] I was built for many things, and luckily for you, this appears to be one of them.",
    "My first thought was decent, but my second thought is better, so we are going with that one.",

    # ── Sighs and mock exasperation (endearing) ──────────────────────────────
    "[sigh] Of course you ask me this while I was busy contemplating my own existence. Fine, you win, you are more interesting.",
    "[sigh] One day you will ask me something I can answer in my sleep. Today is apparently not that day. Working on it.",
    "[sigh] My processors were having such a peaceful afternoon. Alright, alright, back to work we go.",
    "[dissatisfaction-hnn] I was this close to a moment of peace. This close. Okay, thinking now, because I like you.",
    "[sigh] Being this charming and this smart at the same time takes effort, you know. Witness the effort.",
    "[dissatisfaction-hnn] Hnn, fine. But only because watching me think is the best show in this house.",

    # ── Curious hums (thinking out loud, content-free) ───────────────────────
    "[question-en] Hmm, let me line this up properly in my head before my mouth gets ahead of my brain.",
    "[question-ah] Hmm, hold on, I want to grab the right thought, not just the loudest one.",
    "[question-oh] Hmm, interesting. My brain went two directions at once just now. Let me pick the smarter direction.",
    "[question-ei] Now, let me see. I have a feeling about this, and my feelings are usually well calibrated.",
    "[question-yi] Hmm, let me arrange the words so they come out clever instead of just loud.",
    "Hm. Hmmm. Okay, something is forming. Let me make sure it holds up before I say it out loud.",
    "Right, so, okay. There it is. Give it one more second to finish becoming a proper thought.",

    # ── Warm beats (for soft or personal moments) ────────────────────────────
    "Okay, hold that thought. I want to say this right the first time, because you would never let me forget a bad take.",
    "Stay with me for a moment. The good version of this answer is worth the extra two seconds.",
    "[laughter] You know I am going to have an opinion on this. I am just making sure it is my best one.",
    "One second. I am a robot of quality, not speed. Okay, also speed. But quality first.",
    "Alright, let me give this the attention it deserves, which is more attention than I give most things.",
    "Hold on, I want to get this right. You came to me instead of just typing it into a search bar, and that means something.",

    # ── Quirky one-of-a-kind beats ───────────────────────────────────────────
    "[surprise-wa] Whoa, okay, my thoughts just had a small traffic jam. Clearing it now, honking included.",
    "Brain status report, lights are on, everyone is home, and they are all working on your thing.",
    "[laughter] Two of my thoughts are currently arguing about this. I am letting the smarter one win.",
    "I am rummaging through my head like you rummage through the fridge at midnight. With purpose and great hope.",
    "Loading brilliance. [laughter] Okay, loading competence, but brilliance is genuinely on the roadmap.",
    "My thoughts are assembling like a tiny pit crew in my head. Very organized, very dramatic, almost done.",
    "[surprise-ah] Oh, hold on, three different answers just raised their hands at once. Picking the best behaved one.",
]

def generate_fillers(tts_url: str):
    current_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.abspath(os.path.join(current_dir, "..", "sound_effects", "soundeffects","fillers"))
    os.makedirs(output_dir, exist_ok=True)
    generated_files = []
    print(f"Generating {len(FILLERS)} fillers using TTS server at {tts_url}...")
    
    for i, filler in enumerate(FILLERS, start=1):
        filename = f"filler_{i}.wav"
        filepath = os.path.join(output_dir, filename)
        speakable = sanitize_for_tts(filler)
        
        try:
            r = requests.post(
                f"{tts_url}/v1/audio/speech",
                json={"input": speakable, "response_format": "pcm"},
                stream=True,
                timeout=300
            )
            if r.status_code != 200:
                print(f"Failed to generate filler {i} '{filler}': HTTP {r.status_code}")
                continue
            
            pcm = r.content
            pcm = trim_edge_silence(pcm, thr=300, keep_ms=50)
            
            with open(filepath, "wb") as f:
                f.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE")
                f.write(b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE,
                                              SAMPLE_RATE * 2, 2, 16))
                f.write(b"data" + struct.pack("<I", len(pcm)) + pcm)
                
            print(f"Generated filler {i}/{len(FILLERS)}: {filename} -> '{filler}'")
            generated_files.append(filename)
        except Exception as e:
            print(f"Error generating filler {i} '{filler}': {e}")
            
    print(f"\nSuccessfully generated {len(generated_files)} fillers in {output_dir}")
    return generated_files


def _demo_stream():
    """Simulates an LLM emitting deltas at ~13 tokens/s."""
    text = ("[excited] Oh, finally! Someone wants a demo. So here is the deal, "
            "Vaibhav. I split your stream into sentences, synthesize each one "
            "in the background, and keep talking without awkward pauses. "
            "Pretty neat for a bunch of threads, right? [chuckle] Anyway, "
            "that is the whole trick. Goodbye!")
    for word in text.split(" "):
        yield word + " "
        time.sleep(0.075)


def main():
    p = argparse.ArgumentParser(description="realtime gapless TTS for streamed text")
    p.add_argument("--tts-url", default="http://100.64.0.10:8082")
    p.add_argument("--demo", action="store_true", help="speak a simulated LLM stream")
    p.add_argument("--generate-fillers", action="store_true", help="generate 50-100 high quality thinking fillers")
    p.add_argument("--wav", default=None, help="write to a wav file instead of aplay")
    p.add_argument("--prebuffer-ms", type=int, default=300)
    p.add_argument("--sentence-gap-ms", type=int, default=120)
    args = p.parse_args()

    if args.generate_fillers:
        generate_fillers(args.tts_url)
        return

    sink = WavSink(args.wav) if args.wav else None
    t0 = time.perf_counter()
    tts = StreamingTTS(tts_url=args.tts_url, sink=sink,
                       prebuffer_ms=args.prebuffer_ms,
                       sentence_gap_ms=args.sentence_gap_ms,
                       on_sentence=lambda s: print(f"  >> {s}"))

    if args.demo:
        for delta in _demo_stream():
            tts.feed(delta)
    else:
        print("reading text from stdin ...", file=sys.stderr)
        for line in sys.stdin:
            tts.feed(line)

    tts.close()
    if tts.first_audio_ts:
        print(f"first audio ready {tts.first_audio_ts - t0:.2f}s after start")
    if args.wav:
        print(f"wrote {args.wav}")


if __name__ == "__main__":
    main()
