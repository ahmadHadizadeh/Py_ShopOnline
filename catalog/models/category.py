from django.db import models
from django.urls import reverse
from django.core.exceptions import ValidationError
from catalog.utils import generate_unique_slug


class Category(models.Model):
    name = models.CharField(max_length=200, verbose_name="نام")
    slug = models.SlugField(
        max_length=200,
        unique=True,
        allow_unicode=True,
        verbose_name="اسلاگ",
    )
    is_active = models.BooleanField(default=True, verbose_name="فعال")
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="مادر",
    )

    class Meta:
        verbose_name = "دسته‌بندی"
        verbose_name_plural = "دسته‌بندی‌ها"
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.parent_id == self.pk:
            raise ValidationError({"parent": "دسته‌بندی نمی‌تواند والد خودش باشد."})

        parent = self.parent
        visited = set()
        while parent:
            if parent.pk in visited:
                raise ValidationError(
                    {"parent": "ساختار والد دسته‌بندی‌ها چرخه‌ای شده است."}
                )
            visited.add(parent.pk)
            if parent.pk == self.pk:
                raise ValidationError(
                    {"parent": "دسته‌بندی نمی‌تواند در زیرمجموعه خودش قرار بگیرد."}
                )
            parent = parent.parent

    def save(self, *args, **kwargs):
        if not self.slug and self.name:
            self.slug = generate_unique_slug(self, self.name)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("catalog:product_list_by_category", args=[self.slug])
