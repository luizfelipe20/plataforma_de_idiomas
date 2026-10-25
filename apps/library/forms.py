from django import forms

from apps.core.languages import DEFAULT_STUDY_LANGUAGE, DEFAULT_TRANSLATION_LANGUAGE, LANGUAGE_CHOICES

from .models import Tag, Text
from .services.generation import LEVELS, MAX_PROMPT_CHARS, MAX_TEXTS_PER_REQUEST, TEXT_LENGTHS
from .services.texts import parse_tag_names

MAX_CONTENT_CHARS = 30000


class TextForm(forms.ModelForm):
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.none(), required=False, widget=forms.CheckboxSelectMultiple, label="Tags"
    )
    new_tags = forms.CharField(
        label="Novas tags",
        required=False,
        max_length=300,
        help_text="Separe por vírgulas. Ex.: Viagens, Iniciante",
        widget=forms.TextInput(attrs={"placeholder": "Tecnologia, Vocabulário…"}),
    )

    class Meta:
        model = Text
        fields = ["title", "content", "source_language", "target_language", "quiz_language", "tags"]
        widgets = {
            "content": forms.Textarea(attrs={"rows": 14, "placeholder": "Cole ou digite o texto que deseja estudar…"}),
            "title": forms.TextInput(attrs={"placeholder": "Ex.: Notícia sobre tecnologia"}),
        }

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner
        self.fields["tags"].queryset = Tag.objects.filter(owner=owner)
        self.fields["quiz_language"].choices = [("", "Mesmo idioma de estudo")] + list(LANGUAGE_CHOICES)

    def clean_title(self):
        title = " ".join(self.cleaned_data["title"].split())
        if not title:
            raise forms.ValidationError("Informe um título.")
        return title

    def clean_content(self):
        content = self.cleaned_data["content"].replace("\r\n", "\n")
        if not content.strip():
            raise forms.ValidationError("O texto não pode ficar vazio.")
        if len(content) > MAX_CONTENT_CHARS:
            raise forms.ValidationError(f"O texto deve ter no máximo {MAX_CONTENT_CHARS} caracteres.")
        return content

    def clean_new_tags(self):
        names = parse_tag_names(self.cleaned_data.get("new_tags"))
        if len(names) > 20:
            raise forms.ValidationError("Crie no máximo 20 tags por vez.")
        return ", ".join(names)

    def clean(self):
        data = super().clean()
        if data.get("source_language") and data.get("source_language") == data.get("target_language"):
            self.add_error("target_language", "O idioma de tradução deve ser diferente do idioma de estudo.")
        return data


class TextGenerationForm(forms.Form):
    prompt = forms.CharField(
        label="O que a IA deve escrever?",
        max_length=MAX_PROMPT_CHARS,
        widget=forms.Textarea(attrs={
            "rows": 5,
            "placeholder": "Ex.: um diálogo entre um cliente e um atendente de hotel, "
                           "com pedidos de informação e uma reclamação educada.",
        }),
    )
    source_language = forms.ChoiceField(label="Idioma de estudo", choices=LANGUAGE_CHOICES, initial=DEFAULT_STUDY_LANGUAGE)
    target_language = forms.ChoiceField(label="Idioma de tradução", choices=LANGUAGE_CHOICES, initial=DEFAULT_TRANSLATION_LANGUAGE)
    count = forms.IntegerField(
        label="Quantidade de textos", min_value=1, max_value=MAX_TEXTS_PER_REQUEST, initial=1,
        widget=forms.NumberInput(attrs={"inputmode": "numeric"}),
    )
    length = forms.ChoiceField(
        label="Tamanho de cada texto",
        choices=[(k, f"{label} (~{words} palavras)") for k, (label, words) in TEXT_LENGTHS.items()],
        initial="medium",
    )
    level = forms.ChoiceField(label="Nível", choices=[(k, label) for k, (label, _) in LEVELS.items()], required=False)
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.none(), required=False, widget=forms.CheckboxSelectMultiple, label="Tags"
    )
    new_tags = TextForm.base_fields["new_tags"]
    clean_new_tags = TextForm.clean_new_tags

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tags"].queryset = Tag.objects.filter(owner=owner)

    def clean_prompt(self):
        prompt = self.cleaned_data["prompt"].strip()
        if not prompt:
            raise forms.ValidationError("Descreva o texto que a IA deve escrever.")
        return prompt

    def clean(self):
        data = super().clean()
        if data.get("source_language") and data.get("source_language") == data.get("target_language"):
            self.add_error("target_language", "O idioma de tradução deve ser diferente do idioma de estudo.")
        return data


class TagForm(forms.ModelForm):
    class Meta:
        model = Tag
        fields = ["name"]
        widgets = {"name": forms.TextInput(attrs={"placeholder": "Nova tag"})}

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        if not name:
            raise forms.ValidationError("Informe um nome.")
        clash = Tag.objects.filter(owner=self.owner, name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("Já existe uma tag com esse nome.")
        return name

    def save(self, commit=True):
        self.instance.owner = self.owner
        return super().save(commit)
