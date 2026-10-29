from django import forms

from apps.core.languages import DEFAULT_STUDY_LANGUAGE, DEFAULT_TRANSLATION_LANGUAGE, LANGUAGE_CHOICES


class UploadForm(forms.Form):
    title = forms.CharField(label="Título", max_length=200, required=False,
                            widget=forms.TextInput(attrs={"placeholder": "Opcional: usa o nome do arquivo"}))
    language = forms.ChoiceField(label="Idioma falado no vídeo", choices=LANGUAGE_CHOICES, initial=DEFAULT_STUDY_LANGUAGE)
    target_language = forms.ChoiceField(label="Idioma de tradução", choices=LANGUAGE_CHOICES,
                                        initial=DEFAULT_TRANSLATION_LANGUAGE)

    def clean(self):
        data = super().clean()
        if data.get("language") and data.get("language") == data.get("target_language"):
            self.add_error("target_language", "O idioma de tradução deve ser diferente do idioma falado.")
        return data
