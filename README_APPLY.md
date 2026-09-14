# Py_ShopOnline — Final Architecture/Variant Patch

This patch is based on the source snapshot supplied in the conversation. It is intentionally limited to the Cart → Variant → Order integration and the existing cart lifecycle.

## Changed source files
- cart/cart/models.py
- cart/cart/services.py
- cart/cart/views.py
- cart/cart/admin.py
- cart/cart/tests.py
- cart/cart/templates/cart/partials/_cart_item.html
- cart/cart/templates/cart/checkout.html
- cart/cart/migrations/0003_cartitem_variant.py
- catalog/catalog/templates/catalog/details.html
- orders/orders/services.py

## Apply
1. Create a git commit/backup before applying.
2. Extract this archive at the project root so the paths merge with the existing project.
3. Run:
   `python manage.py migrate`
   `python manage.py check`
   `python manage.py test`
4. Do not apply earlier Variant patches from this conversation.

## Important
The environment used to build this patch did not contain Django and had no network access, so Django runtime tests/migration execution could not be run here. Python source compilation and static consistency checks were run successfully.
