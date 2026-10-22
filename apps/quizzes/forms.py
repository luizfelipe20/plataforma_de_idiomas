from django import forms
from django.conf import settings

from apps.core.languages import LANGUAGE_CHOICES

from .kinds import KINDS
from .models import StudyMode


class QuizBuilderForm(forms.Form):
    """Configuração de um novo quiz: tipos de pergunta e quantidade de cada um."""

    title = forms.CharField(
        label="Título (opcional)", max_length=120, required=False,
        widget=forms.TextInput(attrs={"placeholder": "Ex.: Revisão de vocabulário"}),
    )
    language = forms.ChoiceField(label="Idioma dos enunciados", choices=LANGUAGE_CHOICES)
    mode = forms.ChoiceField(label="Modo da tentativa", choices=StudyMode.choices, required=False)

    def __init__(self, *args, text, **kwargs):
        super().__init__(*args, **kwargs)
        self.text = text
        self.fields["language"].initial = text.effective_quiz_language
        default_plan = settings.QUIZ_DEFAULT_PLAN
        for key, kind in KINDS.items():
            self.fields[f"use_{key}"] = forms.BooleanField(
                label=kind.label, required=False, initial=key in default_plan
            )
            self.fields[f"count_{key}"] = forms.IntegerField(
                label=f"Quantidade — {kind.label}", min_value=0, max_value=settings.QUIZ_MAX_PER_KIND,
                required=False, initial=default_plan.get(key, 0),
                widget=forms.NumberInput(attrs={"inputmode": "numeric"}),
            )

    def kind_rows(self):
        """Campos agrupados por tipo, para o template."""
        return [
            {"kind": kind, "use": self[f"use_{key}"], "count": self[f"count_{key}"]}
            for key, kind in KINDS.items()
        ]

    def clean(self):
        data = super().clean()
        plan = {}
        for key, kind in KINDS.items():
            use = data.get(f"use_{key}")
            count = data.get(f"count_{key}") or 0
            if use and count == 0:
                self.add_error(f"count_{key}", f"Informe quantas perguntas de “{kind.label}” deseja.")
            # Uma quantidade preenchida também seleciona o tipo (funciona sem JavaScript).
            if count > 0:
                plan[key] = count
        total = sum(plan.values())
        if not plan and not self.errors:
            raise forms.ValidationError("Selecione pelo menos um tipo de pergunta.")
        if total > settings.QUIZ_MAX_QUESTIONS:
            raise forms.ValidationError(
                f"O quiz pode ter no máximo {settings.QUIZ_MAX_QUESTIONS} perguntas (você pediu {total})."
            )
        data["plan"] = plan
        return data
