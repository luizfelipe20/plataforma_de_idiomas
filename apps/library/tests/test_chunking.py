from django.test import SimpleTestCase

from apps.library.services.chunking import split_text


class SplitTextTests(SimpleTestCase):
    def test_short_text_is_single_chunk(self):
        text = "Hello world. " * 10
        self.assertEqual(split_text(text, 500), [text])

    def test_exactly_limit_is_single_chunk(self):
        text = "a" * 500
        self.assertEqual(split_text(text, 500), [text])

    def test_empty_text(self):
        self.assertEqual(split_text("", 500), [])

    def test_long_text_chunks_respect_limit_and_preserve_content(self):
        text = " ".join(f"Sentence number {i} talks about something interesting." for i in range(60))
        chunks = split_text(text, 500)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 500 for c in chunks))
        self.assertEqual("".join(chunks), text)  # conteúdo integral e na ordem

    def test_prefers_sentence_boundaries(self):
        text = " ".join(f"Sentence number {i} talks about something interesting." for i in range(60))
        for chunk in split_text(text, 500)[:-1]:
            self.assertTrue(chunk.rstrip().endswith("."), repr(chunk[-30:]))

    def test_falls_back_to_word_boundaries(self):
        words = " ".join(["palavra"] * 200)  # sem pontuação
        chunks = split_text(words, 100)
        self.assertEqual("".join(chunks), words)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 100)
            self.assertNotIn("palavr ", chunk)
            self.assertTrue(chunk.strip().split(" ")[-1] == "palavra")

    def test_hard_split_when_no_spaces(self):
        text = "x" * 1234
        chunks = split_text(text, 500)
        self.assertEqual([len(c) for c in chunks], [500, 500, 234])
        self.assertEqual("".join(chunks), text)

    def test_unicode_and_newlines_preserved(self):
        text = ("Olá, ação e coração.\n\nこんにちは。世界！" * 40)
        chunks = split_text(text, 500)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(c) <= 500 for c in chunks))
