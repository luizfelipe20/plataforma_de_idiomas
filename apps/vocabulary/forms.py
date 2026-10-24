from django import forms

from .models import DifficultTerm
from .services import MAX_PRACTICE_TERMS, PRACTICE_LENGTHS


class TermSelectionForm(forms.Form):
    terms = forms.ModelMultipleChoiceField(
        queryset=DifficultTerm.objects.none(),
        error_messages={"required": "Selecione ao menos um termo.", "invalid_choice": "Termo inválido."},
    )

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["terms"].queryset = DifficultTerm.objects.filter(owner=owner).select_related("source_text")


class PracticeTextForm(TermSelectionForm):
    length = forms.ChoiceField(
        label="Tamanho", choices=[(k, label) for k, (label, _) in PRACTICE_LENGTHS.items()], initial="medium"
    )
    topic = forms.CharField(
        label="Assunto (opcional)",
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={"placeholder": "Ex.: uma viagem de trabalho"}),
    )

    def clean_terms(self):
        terms = self.cleaned_data["terms"]
        if len(terms) > MAX_PRACTICE_TERMS:
            raise forms.ValidationError(f"Selecione no máximo {MAX_PRACTICE_TERMS} termos por texto.")
        if len({t.language for t in terms}) > 1:
            raise forms.ValidationError("Selecione termos de um único idioma para gerar o texto.")
        return terms


class MeaningForm(forms.ModelForm):
    class Meta:
        model = DifficultTerm
        fields = ["meaning"]
