import re
from django.utils.text import slugify
def normalize_unicode_slug(text: str) -> str:
    if not text:
        return ""
    text = text.strip().lower()
    replacements = {
        "ي": "ی",
        "ك": "ک",
        "ە": "ه",
        "ؤ": "و",
        "إ": "ا",
        "أ": "ا",
        "ء": "",
        "‌": "-",
        " ": "-",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"[^\w\u0600-\u06FF-]", "", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-_")

def generate_unique_slug(instance, source_text, slug_field="slug"):
    # نرمال‌سازی حروف فارسی
    text = source_text.strip().lower()
    replacements = {"ي": "ی", "ك": "ک", "ە": "ه", "ؤ": "و", "إ": "ا", "أ": "ا", "ء": "", "‌": "-", " ": "-"}
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"[^\w\u0600-\u06FF-]", "", text)
    text = re.sub(r"-{2,}", "-", text)
    base_slug = text.strip("-_") or "item"
    
    model_class = instance.__class__
    slug = base_slug
    counter = 2
    while model_class.objects.filter(**{slug_field: slug}).exclude(pk=instance.pk).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug
