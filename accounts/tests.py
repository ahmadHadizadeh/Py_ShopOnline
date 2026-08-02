import pytest
from django.urls import reverse
from accounts.models import Address

@pytest.mark.django_db
def test_address_default_logic(client, user_factory):
    # استفاده از factory برای ساخت یوزر
    user = user_factory()
    
    # ساخت آدرس اول و پیش‌فرض
    addr1 = Address.objects.create(user=user, recipient_name='A1', city='Amol', is_default=True)
    
    # ساخت آدرس دوم و پیش‌فرض کردن آن
    addr2 = Address.objects.create(user=user, recipient_name='A2', city='Tehran', is_default=True)
    
    addr1.refresh_from_db()
    
    assert addr2.is_default is True
    assert addr1.is_default is False

@pytest.mark.django_db
def test_address_idor_prevention(client, user_factory):
    # یوزر A آدرس می‌سازد
    user_a = user_factory()
    addr_a = Address.objects.create(user=user_a, recipient_name='A', city='Amol')
    
    # یوزر B لاگین می‌کند
    user_b = user_factory()
    client.force_login(user_b)
    
    # تلاش یوزر B برای دسترسی به آدرس یوزر A
    response = client.get(reverse('accounts:address_update', kwargs={'pk': addr_a.pk}))
    
    # باید ۴۰۴ دریافت کند (چون در get_queryset فیلتر کردیم)
    assert response.status_code == 404
