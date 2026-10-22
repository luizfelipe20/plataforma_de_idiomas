from django import forms

from apps.core.languages import LANGUAGE_CHOICES

from .models import Tag, Text
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
