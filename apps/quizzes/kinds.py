"""
Tipos de pergunta disponíveis.

Fonte única para a interface (nome e descrição), para a IA (instruções de
geração) e para a validação (regras estruturais específicas de cada tipo).
"""
from dataclasses import dataclass

FILL_BLANK = "fill_blank"
COMPREHENSION = "comprehension"
LISTENING = "listening"
SYNONYM_ANTONYM = "synonym_antonym"
WORD_ORDER = "word_order"
FIND_ERROR = "find_error"

BLANK = "_____"


@dataclass(frozen=True)
class QuestionKind:
    key: str
    label: str
    description: str
    ai_instructions: str
    icon: str


KINDS = {
    k.key: k
    for k in [
        QuestionKind(
            FILL_BLANK,
            "Complete a frase",
            "Uma frase do texto com uma palavra ou expressão faltando; escolha a que completa corretamente.",
            "Take a sentence from the text (or a very close paraphrase) and replace ONE word or short expression "
            f"with '{BLANK}'. The question MUST contain '{BLANK}'. Options are candidate words or expressions in "
            "the text's language; only one fits both the grammar and the context. Distractors should be the same "
            "part of speech or tempting forms (other tenses, prepositions, collocations).",
            "✍️",
        ),
        QuestionKind(
            COMPREHENSION,
            "Interpretação de texto",
            "Informações explícitas, ideia principal, detalhes e conclusões que podem ser deduzidas do texto.",
            "Ask about explicit information, the main idea, specific details or conclusions that can be logically "
            "inferred from the text. Every answer must be supported by the text; never require outside knowledge.",
            "📖",
        ),
        QuestionKind(
            LISTENING,
            "Compreensão auditiva",
            "O estudante ouve um trecho em áudio (o texto fica oculto) e responde sobre o que ouviu.",
            "The student will only HEAR a passage, not read it. Set `passage` to an excerpt copied VERBATIM from the "
            "text (one to three consecutive sentences). Ask about what is said in that passage; it must be "
            "answerable from the passage alone. Do not quote or reveal the passage in the question.",
            "🎧",
        ),
        QuestionKind(
            SYNONYM_ANTONYM,
            "Sinônimos e antônimos",
            "Uma palavra ou expressão do texto; identifique seu sinônimo ou antônimo, conforme o enunciado.",
            "Pick a word or expression that appears in the text. The question must quote it and state clearly "
            "whether a SYNONYM or an ANTONYM is wanted, considering its meaning in the text. Options are single "
            "words or short expressions in the text's language; exactly one matches.",
            "🔁",
        ),
        QuestionKind(
            WORD_ORDER,
            "Ordem correta das palavras",
            "Palavras ou fragmentos embaralhados; escolha a frase com a ordem gramatical correta.",
            "Take a sentence from the text with 4 to 12 words. In the question, show its words or fragments "
            "scrambled and separated by ' / '. Every option must be a complete sentence using EXACTLY the same "
            "words; only the correct option has grammatical word order.",
            "🔀",
        ),
        QuestionKind(
            FIND_ERROR,
            "Identifique o erro",
            "Cinco frases em inglês; encontre a que tem erro gramatical (o enunciado diz o que procurar).",
            "Each option is a sentence in the text's language related to the text. EXACTLY ONE option contains a "
            "grammatical error and it is the correct answer; the other four must be completely correct. The "
            "question must say explicitly that the student must find the grammatically INCORRECT sentence "
            "(or the sentence with the specific kind of error you name).",
            "🔎",
        ),
    ]
}

KIND_CHOICES = [(k.key, k.label) for k in KINDS.values()]


def kind_label(key):
    kind = KINDS.get(key)
    return kind.label if kind else key


def normalize_plan(raw):
    """{tipo: quantidade} só com tipos válidos e quantidades positivas, na ordem de KINDS."""
    raw = raw or {}
    plan = {}
    for key in KINDS:
        try:
            count = int(raw.get(key, 0) or 0)
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            plan[key] = count
    return plan
