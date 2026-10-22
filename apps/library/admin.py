from django.contrib import admin

from .models import Tag, Text, TextChunk


class TextChunkInline(admin.TabularInline):
    model = TextChunk
    extra = 0
    readonly_fields = ("order", "content", "translation", "translation_language")


@admin.register(Text)
class TextAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "source_language", "target_language", "status", "updated_at")
    list_filter = ("status", "source_language", "target_language")
    search_fields = ("title", "content")
    inlines = [TextChunkInline]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "owner")
    search_fields = ("name",)
