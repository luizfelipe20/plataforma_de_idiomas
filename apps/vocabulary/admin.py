from django.contrib import admin

from .models import DifficultTerm


@admin.register(DifficultTerm)
class DifficultTermAdmin(admin.ModelAdmin):
    list_display = ("term", "owner", "language", "times_marked", "updated_at")
    list_filter = ("language",)
    search_fields = ("term", "meaning", "context")
