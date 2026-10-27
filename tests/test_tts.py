import unittest

from ttchat import tts


class FormatTests(unittest.TestCase):
    def test_rate_and_pitch_format(self):
        self.assertEqual(tts.format_rate(0), "+0%")
        self.assertEqual(tts.format_rate(25), "+25%")
        self.assertEqual(tts.format_rate(-10), "-10%")
        self.assertEqual(tts.format_pitch(0), "+0Hz")
        self.assertEqual(tts.format_pitch(-5), "-5Hz")

    def test_mp3_header_detection(self):
        self.assertTrue(tts.looks_like_mp3(b"ID3\x04\x00\x00\x00"))
        self.assertTrue(tts.looks_like_mp3(b"\xff\xf3\x48\xc4"))
        self.assertFalse(tts.looks_like_mp3(b"RIFF....WAVE"))
        self.assertFalse(tts.looks_like_mp3(b""))

    def test_estimate_seconds(self):
        self.assertAlmostEqual(tts.estimate_seconds(b"x" * 6000), 1.0)

    def test_voice_catalogue(self):
        self.assertIn("ru-RU-SvetlanaNeural", tts.VOICES)
        self.assertNotIn("ru-RU-DariyaNeural", tts.VOICES)  # в каталоге Microsoft такого голоса нет
        self.assertNotIn("ru-RU-DmitryNeural", tts.VOICES)  # только женские голоса


class SynthesizerTests(unittest.TestCase):
    def test_success_passes_arguments(self):
        calls = []

        async def backend(text, voice, rate, pitch):
            calls.append((text, voice, rate, pitch))
            return b"\xff\xf3audio"

        synth = tts.Synthesizer(backend=backend)
        self.assertEqual(synth("привет", "ru-RU-SvetlanaNeural", 10, -2), b"\xff\xf3audio")
        self.assertEqual(calls, [("привет", "ru-RU-SvetlanaNeural", 10, -2)])
        synth.close()

    def test_retries_then_succeeds(self):
        state = {"n": 0}

        async def backend(text, voice, rate, pitch):
            state["n"] += 1
            if state["n"] < 3:
                raise ConnectionError("сеть")
            return b"ok"

        synth = tts.Synthesizer(attempts=3, backend=backend)
        synth_sleep = synth  # сокращаем паузы между повторами
        self.assertEqual(synth_sleep("т", "v"), b"ok")
        self.assertEqual(state["n"], 3)
        synth.close()

    def test_gives_up_with_tts_error(self):
        async def backend(text, voice, rate, pitch):
            raise ConnectionError("нет сети")

        synth = tts.Synthesizer(attempts=2, backend=backend)
        with self.assertRaises(tts.TTSError) as ctx:
            synth("т", "v")
        self.assertIn("ConnectionError", str(ctx.exception))
        synth.close()

    def test_timeout_is_error(self):
        import asyncio

        async def backend(text, voice, rate, pitch):
            await asyncio.sleep(5)

        synth = tts.Synthesizer(attempts=1, timeout=0.05, backend=backend)
        with self.assertRaises(tts.TTSError):
            synth("т", "v")
        synth.close()


if __name__ == "__main__":
    unittest.main()
