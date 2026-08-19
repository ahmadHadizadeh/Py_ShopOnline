# order/service


from django.db import transaction
from django.core.exceptions import ValidationError

from orders.models.orders import Order
from orders.models.order_item import OrderItem
from orders.models.order_address_snapshot import OrderAddressSnapshot
from orders.models.payment import Payment
from orders.utils import calculate_order_totals


class OrderService:
    @staticmethod
    @transaction.atomic
    def create_order(
        *,
        user,
        cart,
        shipping_method,
        shipping_address,
        customer_note="",
    ):
        if not cart:
            raise ValidationError("سبد خرید معتبر نیست.")

        if not shipping_method or not getattr(shipping_method, "is_active", False):
            raise ValidationError("روش ارسال معتبر نیست.")

        cart_items = list(cart.items.select_related("product", "variant").all())

        if not cart_items:
            raise ValidationError("سبد خرید شما خالی است.")

        order_items = []
        subtotal_amount = 0
        discount_amount = 0

        for cart_item in cart_items:
            product = cart_item.product

            if not product.is_active:
                raise ValidationError(f"محصول «{product.title}» غیرفعال است.")

            variant = getattr(cart_item, "variant", None)
            if variant is not None:
                if not getattr(variant, "is_active", True):
                    raise ValidationError(
                        f"تنوع انتخابی محصول «{product.title}» غیرفعال است."
                    )
                if variant.stock_quantity < cart_item.quantity:
                    raise ValidationError(
                        f"موجودی تنوع انتخابی محصول «{product.title}» کافی نیست."
                    )
                unit_price = variant.price
            else:
                if product.stock_quantity < cart_item.quantity:
                    raise ValidationError(f"موجودی محصول «{product.title}» کافی نیست.")
                unit_price = product.price

            line_total = unit_price * cart_item.quantity
            subtotal_amount += line_total

            order_items.append(
                OrderItem(
                    product=product,
                    variant=variant,
                    quantity=cart_item.quantity,
                    price=unit_price,
                    total_price=line_total,
                )
            )

        shipping_amount = shipping_method.calculate_shipping_cost(subtotal_amount)
        final_amount = subtotal_amount - discount_amount + shipping_amount

        order = Order.objects.create(
            user=user,
            cart=cart,
            shipping_method=shipping_method,
            order_number=Order.generate_order_number(),
            status=Order.OrderStatus.PENDING,
            subtotal_amount=subtotal_amount,
            discount_amount=discount_amount,
            shipping_amount=shipping_amount,
            final_amount=final_amount,
            customer_note=customer_note or "",
        )

        for item in order_items:
            item.order = order

        OrderItem.objects.bulk_create(order_items)

        OrderAddressSnapshot.objects.create(
            order=order,
            full_name=getattr(shipping_address, "full_name", ""),
            phone_number=getattr(
                shipping_address,
                "phone_number",
                getattr(shipping_address, "recipient_mobile", ""),
            ),
            province=getattr(shipping_address, "province", ""),
            city=getattr(shipping_address, "city", ""),
            address_line=getattr(shipping_address, "address_line", ""),
            postal_code=getattr(shipping_address, "postal_code", ""),
            note=getattr(shipping_address, "note", ""),
        )

        Payment.objects.create(
            order=order,
            user=user,
            amount=order.final_amount,
            status=Payment.Status.PENDING,
            gateway_name="default_gateway",
        )

        cart.items.all().delete()

        return order
