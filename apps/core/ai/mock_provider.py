"""
Provedor offline e determinístico.

Usado em testes e no desenvolvimento local sem chave de API. Gera traduções
marcadas como simuladas e perguntas dos seis tipos a partir das frases do
próprio texto, de modo que todas as regras de negócio possam ser exercitadas.
Quando o texto não oferece material para um tipo (ex.: nenhuma palavra do
pequeno dicionário de sinônimos), o tipo é informado em `skipped`.
"""
import difflib
import random
import re

from .base import AIProvider, GeneratedQuestion, GeneratedQuiz, SkippedKind

SENTENCE_RE = re.compile(r"[^.!?。！？\n]+[.!?。！？]?")
WORD_RE = re.compile(r"[^\W\d_]{4,}", re.UNICODE)

STATEMENTS = {
    "pt": {
        "fill_blank": "Escolha a alternativa que completa a frase: “{s}”",
        "comprehension": "Qual destas frases aparece no texto?",
        "listening": "Ouça o trecho em áudio. Qual palavra foi dita?",
        "synonym": "No texto, qual é o SINÔNIMO de “{w}”?",
        "antonym": "No texto, qual é o ANTÔNIMO de “{w}”?",
        "word_order": "Coloque as palavras na ordem correta: {w}",
        "find_error": "Qual destas frases está gramaticalmente INCORRETA?",
        "explain_cloze": "O trecho original é: “{o}”.",
        "explain_heard": "O áudio diz: “{o}”.",
        "explain_syn": "“{w}” e “{a}” têm significados {rel}.",
        "explain_order": "A ordem correta é: “{o}”.",
        "explain_error": "A frase “{e}” repete uma palavra; a forma correta é “{o}”.",
        "no_synonyms": "O texto não tem palavras com sinônimos/antônimos conhecidos pelo provedor offline.",
        "no_sentences": "O texto não tem frases suficientes para este tipo de pergunta.",
    },
    "en": {
        "fill_blank": "Choose the option that completes the sentence: “{s}”",
        "comprehension": "Which of these sentences appears in the text?",
        "listening": "Listen to the audio passage. Which word did you hear?",
        "synonym": "In the text, which word is a SYNONYM of “{w}”?",
        "antonym": "In the text, which word is an ANTONYM of “{w}”?",
        "word_order": "Put the words in the correct order: {w}",
        "find_error": "Which of these sentences is grammatically INCORRECT?",
        "explain_cloze": "The original passage reads: “{o}”.",
        "explain_heard": "The audio says: “{o}”.",
        "explain_syn": "“{w}” and “{a}” have {rel} meanings.",
        "explain_order": "The correct order is: “{o}”.",
        "explain_error": "“{e}” repeats a word; the correct form is “{o}”.",
        "no_synonyms": "The text has no words with synonyms/antonyms known to the offline provider.",
        "no_sentences": "The text does not have enough sentences for this question type.",
    },
}
# Pequeno dicionário (inglês) para sinônimos e antônimos: palavra -> (sinônimo, antônimo)
LEXICON = {
    "happy": ("glad", "sad"), "big": ("large", "small"), "small": ("little", "big"), "start": ("begin", "finish"),
    "begin": ("start", "end"), "fast": ("quick", "slow"), "quick": ("fast", "slow"), "easy": ("simple", "difficult"),
    "difficult": ("hard", "easy"), "hard": ("difficult", "easy"), "improve": ("enhance", "worsen"),
    "increase": ("rise", "decrease"), "reduce": ("decrease", "increase"), "healthy": ("fit", "unhealthy"),
    "early": ("soon", "late"), "late": ("delayed", "early"), "warm": ("mild", "cold"), "cold": ("chilly", "hot"),
    "old": ("ancient", "new"), "new": ("recent", "old"), "first": ("initial", "last"), "short": ("brief", "long"),
    "long": ("lengthy", "short"), "harder": ("tougher", "easier"), "clear": ("obvious", "unclear"),
    "prefer": ("favor", "dislike"), "enjoy": ("relish", "hate"), "save": ("keep", "waste"), "allow": ("permit", "forbid"),
    "worry": ("fret", "relax"), "agree": ("concur", "disagree"), "famous": ("well-known", "unknown"),
    "narrow": ("tight", "wide"), "sunny": ("bright", "cloudy"), "small": ("little", "large"),
}
FILLER_WORDS = ["always", "never", "perhaps", "yesterday", "city", "office", "journey", "school", "quiet", "bright"]


def _norm(text):
    return re.sub(r"\W+", " ", text.lower()).strip()


class MockProvider(AIProvider):
    name = "mock"

    def translate_segments(self, segments, source_language, target_language):
        return [f"[{target_language.code.upper()} · tradução simulada] {seg}" for seg in segments]

    # ------------------------------------------------------------------
    def generate_quiz(self, request):
        rng = random.Random(f"{len(request.text)}:{request.text[:50]}:{request.variation_seed}")
        tpl = STATEMENTS.get(request.quiz_language.code, STATEMENTS["en"])
        explain = STATEMENTS.get(request.explanation_language.code, STATEMENTS["en"])
        sentences = list(dict.fromkeys(s.strip() for s in SENTENCE_RE.findall(request.text) if WORD_RE.search(s)))
        vocabulary = sorted({w.lower(): w for w in WORD_RE.findall(request.text)}.values(), key=str.lower)
        # Cada item evitado é "enunciado | resposta"; compara trechos e respostas contidos nele.
        avoid = [_norm(q) for q in request.avoid_questions]
        ctx = {"rng": rng, "tpl": tpl, "explain": explain, "sentences": sentences, "vocabulary": vocabulary,
               "avoid": avoid, "n": request.num_options, "text": request.text}
        builders = {
            "fill_blank": self._fill_blank, "comprehension": self._comprehension, "listening": self._listening,
            "synonym_antonym": self._synonym_antonym, "word_order": self._word_order, "find_error": self._find_error,
        }
        questions, skipped = [], []
        for item in request.plan:
            made = builders[item["kind"]](ctx, item["count"])
            for q in made:
                q.kind = item["kind"]
            questions.extend(made)
            if len(made) < item["count"]:
                reason = explain["no_synonyms"] if item["kind"] == "synonym_antonym" else explain["no_sentences"]
                skipped.append(SkippedKind(item["kind"], reason))
        return GeneratedQuiz(questions=questions, skipped=skipped, model="mock")

    # ------------------------------------------------------------------
    @staticmethod
    def _fresh_first(ctx, candidates, key):
        """Embaralha e coloca primeiro os candidatos diferentes dos usados em quizzes anteriores."""
        ctx["rng"].shuffle(candidates)

        def seen(c):
            k = _norm(key(c))
            return bool(k) and any(k in a for a in ctx["avoid"])

        return [c for c in candidates if not seen(c)] + [c for c in candidates if seen(c)]

    @staticmethod
    def _pick_distinct(candidates, key, count):
        """Escolhe até `count` candidatos cujos textos não se pareçam entre si."""
        chosen = []
        for strict in (True, False):
            for c in candidates:
                if len(chosen) == count:
                    return chosen
                if c in chosen:
                    continue
                if strict and any(difflib.SequenceMatcher(None, _norm(key(c)), _norm(key(o))).ratio() > 0.6 for o in chosen):
                    continue
                chosen.append(c)
        return chosen

    def _options(self, ctx, answer, pool):
        pool = [w for w in dict.fromkeys(pool) if _norm(w) != _norm(answer)]
        ctx["rng"].shuffle(pool)
        distractors = pool[: ctx["n"] - 1]
        for filler in FILLER_WORDS:
            if len(distractors) >= ctx["n"] - 1:
                break
            if _norm(filler) not in {_norm(d) for d in distractors} | {_norm(answer)}:
                distractors.append(filler)
        options = distractors + [answer]
        ctx["rng"].shuffle(options)
        return options, options.index(answer)

    def _cloze_candidates(self, ctx):
        candidates = []
        for sentence in ctx["sentences"]:
            tokens = sentence.split()
            for pos, token in enumerate(tokens):
                match = WORD_RE.search(token)
                if not match:
                    continue
                word = match.group(0)
                window = tokens[max(0, pos - 5): pos] + [token.replace(word, "_____", 1)] + tokens[pos + 1: pos + 6]
                snippet = ("… " if pos > 5 else "") + " ".join(window) + (" …" if pos + 6 < len(tokens) else "")
                candidates.append((snippet, word, sentence))
        return candidates

    def _fill_blank(self, ctx, count):
        candidates = self._fresh_first(ctx, self._cloze_candidates(ctx), key=lambda c: c[0])
        out = []
        for snippet, word, sentence in self._pick_distinct(candidates, key=lambda c: c[0], count=count):
            options, correct = self._options(ctx, word, ctx["vocabulary"])
            out.append(GeneratedQuestion(ctx["tpl"]["fill_blank"].format(s=snippet), options, correct,
                                         ctx["explain"]["explain_cloze"].format(o=sentence)))
        return out

    def _altered(self, ctx, sentence):
        """Versão da frase com uma palavra trocada (parece plausível, mas não está no texto)."""
        words = WORD_RE.findall(sentence)
        if not words:
            return None
        target = ctx["rng"].choice(words)
        replacement = ctx["rng"].choice([w for w in ctx["vocabulary"] + FILLER_WORDS if _norm(w) != _norm(target)])
        return sentence.replace(target, replacement, 1)

    def _comprehension(self, ctx, count):
        sentences = self._fresh_first(ctx, [s for s in ctx["sentences"] if len(s.split()) >= 4], key=lambda s: s)
        out = []
        for sentence in sentences[:count]:
            fakes = []
            for _ in range(20):
                other = self._altered(ctx, ctx["rng"].choice(ctx["sentences"]))
                if other and _norm(other) not in {_norm(s) for s in ctx["sentences"]} | {_norm(f) for f in fakes}:
                    fakes.append(other)
                if len(fakes) == ctx["n"] - 1:
                    break
            if len(fakes) < ctx["n"] - 1:
                continue
            options = fakes + [sentence]
            ctx["rng"].shuffle(options)
            out.append(GeneratedQuestion(ctx["tpl"]["comprehension"], options, options.index(sentence),
                                         ctx["explain"]["explain_cloze"].format(o=sentence)))
        return out

    def _listening(self, ctx, count):
        sentences = self._fresh_first(ctx, [s for s in ctx["sentences"] if WORD_RE.search(s)], key=lambda s: s)
        out = []
        for sentence in sentences[:count]:
            word = ctx["rng"].choice(WORD_RE.findall(sentence))
            pool = [w for w in ctx["vocabulary"] if _norm(w) not in _norm(sentence).split()]
            options, correct = self._options(ctx, word, pool)
            out.append(GeneratedQuestion(ctx["tpl"]["listening"], options, correct,
                                         ctx["explain"]["explain_heard"].format(o=sentence), passage=sentence))
        return out

    def _synonym_antonym(self, ctx, count):
        found = [w for w in ctx["vocabulary"] if w.lower() in LEXICON]
        found = self._fresh_first(ctx, found, key=lambda w: w)
        out = []
        for i, word in enumerate(found[:count]):
            synonym, antonym = LEXICON[word.lower()]
            want_synonym = (i + ctx["rng"].randint(0, 1)) % 2 == 0
            answer, wrong_relation = (synonym, antonym) if want_synonym else (antonym, synonym)
            pool = [wrong_relation] + [v[0] for k, v in LEXICON.items() if k != word.lower()]
            options, correct = self._options(ctx, answer, pool)
            key = "synonym" if want_synonym else "antonym"
            rel = ("semelhantes" if want_synonym else "opostos") if ctx["explain"] is STATEMENTS["pt"] else (
                "similar" if want_synonym else "opposite")
            out.append(GeneratedQuestion(ctx["tpl"][key].format(w=word), options, correct,
                                         ctx["explain"]["explain_syn"].format(w=word, a=answer, rel=rel)))
        return out

    def _word_order(self, ctx, count):
        sentences = [s for s in ctx["sentences"] if 4 <= len(s.split()) <= 12]
        sentences = self._fresh_first(ctx, sentences, key=lambda s: s)
        out = []
        for sentence in sentences[:count]:
            words = sentence.rstrip(".!?").split()
            ending = sentence[len(sentence.rstrip(".!?")):]
            variants = {" ".join(words)}
            for _ in range(60):
                shuffled = words[:]
                ctx["rng"].shuffle(shuffled)
                variants.add(" ".join(shuffled))
                if len(variants) == ctx["n"]:
                    break
            if len(variants) < ctx["n"]:
                continue
            correct_text = " ".join(words) + ending
            options = [v + ending for v in variants]
            ctx["rng"].shuffle(options)
            scrambled = words[:]
            ctx["rng"].shuffle(scrambled)
            out.append(GeneratedQuestion(ctx["tpl"]["word_order"].format(w=" / ".join(w.strip(",;:") for w in scrambled)),
                                         options, options.index(correct_text),
                                         ctx["explain"]["explain_order"].format(o=correct_text)))
        return out

    def _find_error(self, ctx, count):
        sentences = [s for s in ctx["sentences"] if len(s.split()) >= 3]
        if len(sentences) < ctx["n"]:
            return []
        candidates = self._fresh_first(ctx, sentences[:], key=lambda s: s)
        out = []
        for sentence in candidates[:count]:
            words = sentence.split()
            pos = ctx["rng"].randrange(len(words))
            wrong = " ".join(words[: pos + 1] + [words[pos].strip(",.;:!?").lower()] + words[pos + 1:])
            others = [s for s in sentences if s != sentence]
            ctx["rng"].shuffle(others)
            options = others[: ctx["n"] - 1] + [wrong]
            ctx["rng"].shuffle(options)
            out.append(GeneratedQuestion(ctx["tpl"]["find_error"], options, options.index(wrong),
                                         ctx["explain"]["explain_error"].format(e=wrong, o=sentence)))
        return out
