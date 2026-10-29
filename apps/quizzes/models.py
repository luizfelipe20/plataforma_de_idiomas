from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.urls import reverse

from apps.core.languages import LANGUAGE_CHOICES
from apps.library.models import Text

from .kinds import COMPREHENSION, KIND_CHOICES, KINDS, LISTENING


class StudyMode(models.TextChoices):
    READING = "reading", "Leitura"
    READING_AUDIO = "reading_audio", "Leitura e áudio"
    AUDIO_ONLY = "audio_only", "Somente áudio"


class Quiz(models.Model):
    text = models.ForeignKey(Text, on_delete=models.CASCADE, related_name="quizzes")
    version = models.PositiveIntegerField("versão")
    title = models.CharField("título", max_length=120, blank=True)
    # Configuração usada na geração: {"plan": {tipo: quantidade}, "requested": total, ...}
    config = models.JSONField("configuração", default=dict, blank=True)
    # Quantidade de alternativas por pergunta (quizzes antigos têm 4; os novos, 5).
    options_per_question = models.PositiveSmallIntegerField(default=5)
    language = models.CharField("idioma das perguntas", max_length=10, choices=LANGUAGE_CHOICES)
    ai_model = models.CharField(max_length=100, blank=True)
    generation_notes = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        ordering = ["text", "-version"]
        constraints = [models.UniqueConstraint(fields=["text", "version"], name="unique_quiz_version")]

    def __str__(self):
        return f"Quiz #{self.version} — {self.text.title}"

    @property
    def label(self):
        return f"Quiz #{self.version}" + (f" · {self.title}" if self.title else "")

    @property
    def kinds_used(self):
        """Tipos de pergunta do quiz, na ordem de KINDS, com a quantidade de cada um."""
        counts = {}
        for kind in self.questions.values_list("kind", flat=True):
            counts[kind] = counts.get(kind, 0) + 1
        return [(KINDS[k], counts[k]) for k in KINDS if k in counts]

    @property
    def kinds_summary(self):
        """[(tipo, quantidade)] a partir da configuração salva (sem consultas extras)."""
        plan = (self.config or {}).get("generated") or (self.config or {}).get("plan") or {}
        return [(KINDS[k], plan[k]) for k in KINDS if plan.get(k)]

    @property
    def has_listening(self):
        return self.questions.filter(kind=LISTENING).exists()

    def get_absolute_url(self):
        return reverse("quizzes:quiz_detail", args=[self.pk])


def _question_audio_path(question, filename):
    # Fora da pasta do vídeo: o áudio do quiz continua existindo se o vídeo for excluído.
    return f"quiz-audio/{question.quiz.text.owner_id}/{question.quiz_id}/{filename}"


class Question(models.Model):
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="questions")
    order = models.PositiveSmallIntegerField()
    kind = models.CharField("tipo", max_length=20, choices=KIND_CHOICES, default=COMPREHENSION)
    prompt = models.TextField("pergunta")
    # Trecho do texto lido em áudio (perguntas de compreensão auditiva). Guardado na
    # pergunta para que o áudio não mude se o texto for editado depois.
    passage = models.TextField("trecho em áudio", blank=True)
    explanation = models.TextField("explicação", blank=True)
    # Texto vindo de vídeo: intervalo (s, no vídeo original) do áudio real do trecho e o
    # segmento correspondente. Sem eles, o áudio da pergunta é gerado por TTS.
    audio_start = models.FloatField(null=True, blank=True)
    audio_end = models.FloatField(null=True, blank=True)
    media_segment = models.ForeignKey(
        "videos.MediaSegment", on_delete=models.SET_NULL, null=True, blank=True, related_name="questions"
    )
    # Recorte do áudio tratado (menos ruído e música) usado na pergunta, guardado no quiz.
    audio_clip = models.FileField(upload_to=_question_audio_path, max_length=255, blank=True)

    class Meta:
        ordering = ["order"]
        constraints = [models.UniqueConstraint(fields=["quiz", "order"], name="unique_question_order")]

    @property
    def kind_info(self):
        return KINDS.get(self.kind)

    @property
    def has_real_audio(self):
        return self.audio_start is not None and self.audio_end is not None

    # Origem do áudio de uma pergunta de compreensão auditiva.
    AUDIO_TREATED = "treated"  # recorte do áudio tratado do vídeo (guardado ou recortável)
    AUDIO_UNAVAILABLE = "unavailable"  # trecho veio do vídeo, mas o áudio tratado sumiu
    AUDIO_SYNTH = "synth"  # trecho fora do vídeo: voz sintetizada

    @property
    def audio_status(self):
        if self.audio_clip and self.audio_clip.storage.exists(self.audio_clip.name):
            return self.AUDIO_TREATED
        if not self.has_real_audio:
            return self.AUDIO_SYNTH
        media = self.quiz.text.source_media
        if media is not None and media.audio_clean and media.audio_clean.storage.exists(media.audio_clean.name):
            return self.AUDIO_TREATED
        return self.AUDIO_UNAVAILABLE

    @property
    def speak_id(self):
        """id do <script> com o trecho em áudio (usado pela voz do navegador como reserva)."""
        return f"q{self.pk}-passage"

    def __str__(self):
        return self.prompt[:80]

    @property
    def correct_option(self):
        return next((o for o in self.options.all() if o.is_correct), None)


class Option(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="options")
    order = models.PositiveSmallIntegerField()
    text = models.CharField("alternativa", max_length=500)
    is_correct = models.BooleanField(default=False)

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["question", "order"], name="unique_option_order"),
            # Garante no banco que cada pergunta tenha no máximo uma alternativa correta.
            models.UniqueConstraint(
                fields=["question"], condition=Q(is_correct=True), name="single_correct_option"
            ),
        ]

    def __str__(self):
        return self.text

    @property
    def letter(self):
        return "ABCDEFGH"[self.order]


class QuizAttempt(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "Em andamento"
        COMPLETED = "completed", "Concluída"
        ABANDONED = "abandoned", "Abandonada"

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="attempts")
    # Redundante com quiz.text, mas facilita consultas do dashboard e deixa explícito o vínculo.
    text = models.ForeignKey(Text, on_delete=models.CASCADE, related_name="attempts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quiz_attempts")
    mode = models.CharField("modo de estudo", max_length=20, choices=StudyMode.choices, default=StudyMode.READING)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    total_questions = models.PositiveSmallIntegerField(default=0)
    correct_count = models.PositiveSmallIntegerField(default=0)
    wrong_count = models.PositiveSmallIntegerField(default=0)
    score = models.PositiveSmallIntegerField(null=True, blank=True, help_text="0 a 100")

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["user", "status", "completed_at"]),
            models.Index(fields=["text", "status"]),
        ]

    def __str__(self):
        return f"Tentativa {self.pk} — {self.quiz}"

    @property
    def is_completed(self):
        return self.status == self.Status.COMPLETED

    @property
    def number(self):
        """Posição desta tentativa entre as tentativas do mesmo quiz/usuário."""
        return QuizAttempt.objects.filter(
            quiz_id=self.quiz_id, user_id=self.user_id, started_at__lte=self.started_at
        ).count()

    def get_absolute_url(self):
        if self.is_completed:
            return reverse("quizzes:attempt_result", args=[self.pk])
        return reverse("quizzes:attempt_take", args=[self.pk])


class AttemptAnswer(models.Model):
    attempt = models.ForeignKey(QuizAttempt, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="answers")
    selected_option = models.ForeignKey(Option, on_delete=models.CASCADE, related_name="answers")
    is_correct = models.BooleanField()
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["attempt", "question"], name="unique_answer_per_question")
        ]


@receiver(post_delete, sender=Question)
def _delete_audio_clip(sender, instance, **kwargs):
    """Apaga o recorte de áudio quando a pergunta some (revisão, exclusão do quiz ou do texto)."""
    if instance.audio_clip:
        instance.audio_clip.delete(save=False)
