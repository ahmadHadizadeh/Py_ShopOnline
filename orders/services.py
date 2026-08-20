# orders/services.py
from decimal import Decimal

from django.db import transaction

from orders.models import Order, OrderAddressSnapshot, OrderItem, Payment


class OrderService:
    @staticmethod
    def calculate_order_totals(cart, shipping_method=None):
        """محاسبه دقیق مبالغ سفارش بر اساس سبد خرید و روش ارسال."""
        subtotal = Decimal(str(cart.total_price))
        discount = Decimal(str(cart.discount_amount))

        shipping_amount = Decimal("0")
        if shipping_method:
            shipping_amount = Decimal(
                str(shipping_method.calculate_shipping_cost(subtotal))
            )

        final_amount = (subtotal - discount) + shipping_amount
        if final_amount < 0:
            final_amount = Decimal("0")

        return {
            "subtotal": subtotal,
            "discount": discount,
            "shipping": shipping_amount,
            "final_amount": final_amount,
        }

    @staticmethod
    @transaction.atomic
    def create_order(user, cart, shipping_address, shipping_method, customer_note=""):
        """ایجاد اتمیک سفارش، آیتم‌ها، اسنپ‌شات آدرس و رکورد پرداخت."""

        totals = OrderService.calculate_order_totals(cart, shipping_method)

        # ۱. ایجاد سفارش اصلی
        order = Order.objects.create(
            user=user,
            order_number=Order.generate_order_number(),
            status=Order.Status.PENDING,
            shipping_method=shipping_method,
            subtotal_amount=totals["subtotal"],
            discount_amount=totals["discount"],
            shipping_amount=totals["shipping"],
            final_amount=totals["final_amount"],
            customer_note=customer_note,
        )

        # ۲. ایجاد اسنپ‌شات آدرس (نگاشت phone_number به recipient_mobile)
        OrderAddressSnapshot.objects.create(
            order=order,
            recipient_name=shipping_address.recipient_name,
            recipient_mobile=shipping_address.phone_number,
            postal_code=shipping_address.postal_code,
            province=shipping_address.province,
            city=shipping_address.city,
            address_line=shipping_address.address_line,
        )

        # ۳. ایجاد آیتم‌های سفارش از سبد خرید
        order_items = []
        for item in cart.items.select_related("product").all():
            # اولویت با قیمت اسنپ‌شات آیتم سبد، سپس واریانت و در نهایت محصول
            unit_price = item.unit_price_snapshot
            if not unit_price:
                unit_price = item.product.price

            order_items.append(
                OrderItem(
                    order=order,
                    product=item.product,
                    variant_id=None,
                    product_name=item.product.name,
                    variant_name="",
                    sku=getattr(item.product, "sku", ""),
                    quantity=item.quantity,
                    unit_price=unit_price,
                    subtotal_price=unit_price * item.quantity,
                )
            )

        if order_items:
            OrderItem.objects.bulk_create(order_items)

        # ۴. ایجاد رکورد پرداخت اولیه
        Payment.objects.create(
            order=order,
            user=user,
            amount=order.final_amount,
            status=Payment.Status.PENDING,
        )

        # ۵. تغییر وضعیت سبد خرید (حذف فیزیکی انجام نمی‌شود تا در Callback مدیریت شود)
        # اما آیتم‌های فعال این سبد را پاک می‌کنیم تا سبد برای خرید بعدی آماده شود
        cart.items.all().delete()

        return order
