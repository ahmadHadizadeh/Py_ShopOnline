import pytest
from django.urls import reverse
from cart.models import Cart, CartItem
from catalog.models.category import  Category
from catalog.models.product import  Product  

@pytest.mark.django_db
def test_cart_scenarios(client):
    # ۱. ساخت دسته‌بندی (الزامی برای دیتابیس)
    category = Category.objects.create(name="Test Category", slug="test-cat")
    
    # ۲. ساخت محصول با دسته‌بندی
    product = Product.objects.create(
        name="Test", 
        slug="test", 
        price=1000, 
        stock=5, 
        is_active=True, 
        category=category  # اضافه شد
    )
    
    # بقیه کد تست مثل قبل...
    client.post(reverse("cart:add", args=[product.id]), {"quantity": 2})
    cart = Cart.objects.first()
    assert cart.items.get(product=product).quantity == 2
    
    item = cart.items.get(product=product)
    client.post(reverse("cart:update_item", args=[item.id]), {"quantity": 4})
    assert cart.items.get(product=product).quantity == 4
    
    client.post(reverse("cart:save_for_later", args=[item.id]))
    assert cart.items.get(product=product).status == CartItem.STATUS_SAVED
    
    client.post(reverse("cart:move_to_cart", args=[item.id]))
    assert cart.items.get(product=product).status == CartItem.STATUS_ACTIVE
