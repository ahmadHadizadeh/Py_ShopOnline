from decimal import Decimal
from django.db import transaction
from django.shortcuts import get_object_or_404
from orders.models import Order, OrderItem, OrderAddressSnapshot, Payment
from orders.utils import calculate_order_totals


class OrderService:
    @staticmethod
    @transaction.atomic
    def create_order(user, cart, shipping_address, customer_note=""):
        """
        ایجاد سفارش اتمیک شامل: Order, OrderItems, Snapshot, Payment
        """
        # ۱. محاسبه نهایی
        totals = calculate_order_totals(cart, shipping_address)

        # ۲. ایجاد سفارش
        order = Order.objects.create(
            user=user,
            order_number=Order.generate_order_number(),
            status=Order.Status.PENDING,
            subtotal_amount=Decimal(str(totals["subtotal"])),
            discount_amount=Decimal(str(totals["discount"])),
            shipping_amount=Decimal(str(totals["shipping"])),
            final_amount=Decimal(str(totals["final_amount"])),
            customer_note=customer_note,
        )

        # ۳. ایجاد آیتم‌های سفارش
        order_items = []
        for item in cart.items.all():
            unit_price = item.variant.price if item.variant else item.product.price
            order_items.append(
                OrderItem(
                    order=order,
                    product_id=item.product.id,
                    variant_id=item.variant.id if item.variant else None,
                    product_name=item.product.name,
                    variant_name=item.variant.name if item.variant else "",
                    sku=item.variant.sku if item.variant else item.product.sku,
                    quantity=item.quantity,
                    unit_price=unit_price,
                    subtotal_price=Decimal(str(item.quantity)) * unit_price,
                )
            )
        OrderItem.objects.bulk_create(order_items)

        # ۴. ایجاد اسنپ‌شات آدرس
        OrderAddressSnapshot.objects.create(
            order=order,
            recipient_name=shipping_address.recipient_name,
            recipient_mobile=shipping_address.recipient_mobile,
            postal_code=shipping_address.postal_code,
            province=shipping_address.province,
            city=shipping_address.city,
            address_line=shipping_address.address_line,
        )

        # ۵. ایجاد رکورد پرداخت اولیه
        Payment.objects.create(
            order=order,
            user=user,
            amount=order.final_amount,
            status=Payment.Status.PENDING,
            gateway_name="default_gateway",
        )

        # ۶. تخلیه سبد خرید (بعد از اطمینان از صحت تمامی عملیات)
        cart.items.all().delete()

        return order
