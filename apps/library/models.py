from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from django.urls import reverse

from apps.core.languages import (
    DEFAULT_STUDY_LANGUAGE,
    DEFAULT_TRANSLATION_LANGUAGE,
    LANGUAGE_CHOICES,
)


class Tag(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tags")
    name = models.CharField("nome", max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = [Lower("name")]
        constraints = [
            models.UniqueConstraint(Lower("name"), "owner", name="unique_tag_name_per_owner"),
        ]

    def __str__(self):
        return self.name


class Text(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendente"
        PROCESSING = "processing", "Processando"
        READY = "ready", "Pronto"
        ERROR = "error", "Erro na tradução"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="texts")
    title = models.CharField("título", max_length=200)
    content = models.TextField("conteúdo original")
    source_language = models.CharField(
        "idioma de estudo", max_length=10, choices=LANGUAGE_CHOICES, default=DEFAULT_STUDY_LANGUAGE
    )
    target_language = models.CharField(
        "idioma de tradução", max_length=10, choices=LANGUAGE_CHOICES, default=DEFAULT_TRANSLATION_LANGUAGE
    )
    quiz_language = models.CharField(
        "idioma dos quizzes",
        max_length=10,
        choices=LANGUAGE_CHOICES,
        blank=True,
        help_text="Deixe em branco para usar o idioma de estudo.",
    )
    translation = models.TextField("tradução", blank=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name="texts")
    status = models.CharField("situação", max_length=20, choices=Status.choices, default=Status.PENDING)
    status_message = models.CharField(max_length=500, blank=True)
    translated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["owner", "source_language"])]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("library:text_detail", args=[self.pk])

    @property
    def effective_quiz_language(self):
        return self.quiz_language or self.source_language

    @property
    def translation_is_stale(self):
        return any(chunk.translation_is_stale for chunk in self.chunks.all())

    @property
    def translation_is_simulated(self):
        return any(chunk.is_simulated for chunk in self.chunks.all())


class TextChunk(models.Model):
    """Trecho do texto original (até TEXT_CHUNK_MAX_CHARS caracteres) e sua tradução."""

    text = models.ForeignKey(Text, on_delete=models.CASCADE, related_name="chunks")
    order = models.PositiveIntegerField()
    content = models.TextField()
    content_hash = models.CharField(max_length=64)
    translation = models.TextField(blank=True)
    # Idioma e hash do conteúdo no momento em que a tradução foi gerada.
    translation_language = models.CharField(max_length=10, blank=True)
    translation_source_hash = models.CharField(max_length=64, blank=True)
    # Provedor que gerou a tradução ("anthropic", "mock"...).
    translation_provider = models.CharField(max_length=30, blank=True)

    class Meta:
        ordering = ["order"]
        constraints = [models.UniqueConstraint(fields=["text", "order"], name="unique_chunk_order")]

    def __str__(self):
        return f"{self.text_id}#{self.order}"

    @property
    def is_simulated(self):
        return self.translation_provider == "mock"

    @property
    def translation_is_stale(self):
        return (
            not self.translation
            or self.translation_language != self.text.target_language
            or self.translation_source_hash != self.content_hash
            # Tradução simulada deixa de valer quando uma IA real está configurada.
            or (self.is_simulated and settings.AI_PROVIDER != "mock")
        )
