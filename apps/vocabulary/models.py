from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from django.urls import reverse

from apps.core.languages import LANGUAGE_CHOICES


class DifficultTerm(models.Model):
    """Palavra ou expressão que o usuário marcou como difícil de interpretar."""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="difficult_terms")
    term = models.CharField("termo", max_length=200)
    language = models.CharField("idioma", max_length=10, choices=LANGUAGE_CHOICES)
    context = models.TextField("contexto", blank=True, help_text="Frase em que o termo foi marcado.")
    meaning = models.TextField("significado", blank=True)
    # Texto em que o termo foi marcado pela última vez.
    source_text = models.ForeignKey(
        "library.Text", on_delete=models.SET_NULL, null=True, blank=True, related_name="difficult_terms"
    )
    # Quantas vezes o usuário marcou o termo (em textos diferentes ou no mesmo): indica a dificuldade.
    times_marked = models.PositiveIntegerField("vezes marcado", default=1)
    practice_texts = models.ManyToManyField("library.Text", blank=True, related_name="practiced_terms")
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        ordering = ["-times_marked", "-updated_at"]
        constraints = [
            models.UniqueConstraint(Lower("term"), "language", "owner", name="unique_term_per_owner_language"),
        ]

    def __str__(self):
        return self.term

    def get_absolute_url(self):
        return reverse("vocabulary:term_detail", args=[self.pk])
