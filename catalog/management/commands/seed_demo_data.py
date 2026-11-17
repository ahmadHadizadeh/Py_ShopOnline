import json
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Address, Profile
from cart.models import Cart
from catalog.models import (
    Category,
    InventoryMovement,
    Product,
    ProductImage,
    ProductSpecification,
    ProductVariant,
    Review,
    StorefrontSettings,
    Wishlist,
)
from orders.models import (
    Order,
    OrderAddressSnapshot,
    OrderItem,
    Payment,
    ShippingMethod,
)
from orders.services import OrderService


User = get_user_model()
SAMPLE_USERNAME_PREFIX = "sample_buyer_"


CATEGORIES = (
    {
        "key": "digital",
        "name": "موبایل و دیجیتال",
        "slug": "digital-gadgets",
    },
    {
        "key": "accessories",
        "name": "لوازم جانبی",
        "slug": "accessories",
    },
    {
        "key": "smart-home",
        "name": "خانه هوشمند",
        "slug": "smart-home",
    },
)

PRODUCTS = (
    {
        "slug": "nova-x5-phone",
        "name": "گوشی هوشمند Nova X5",
        "name_en": "Nova X5 Smartphone",
        "brand": "Nova",
        "category": "digital",
        "price": "42900000",
        "old_price": "46500000",
        "stock": 24,
        "description": "نمایشگر چشم‌نواز، عملکرد سریع و دوربین چندگانه برای استفاده روزمره و حرفه‌ای.",
        "highlights": "نمایشگر AMOLED\nحافظه داخلی پرسرعت\nدوربین چندگانه\nباتری بادوام",
        "guarantee": "۱۸ ماه گارانتی معتبر",
        "variants": (
            ("حافظه داخلی", "۱۲۸ گیگابایت", "0"),
            ("حافظه داخلی", "۲۵۶ گیگابایت", "2500000"),
        ),
        "specs": (
            ("نمایشگر", "AMOLED با نرخ نوسازی بالا"),
            ("حافظه", "۱۲۸ / ۲۵۶ گیگابایت"),
            ("اتصالات", "5G، Wi-Fi و Bluetooth"),
        ),
    },
    {
        "slug": "soundpods-pro",
        "name": "هدفون بی‌سیم SoundPods Pro",
        "name_en": "SoundPods Pro",
        "brand": "SoundPods",
        "category": "accessories",
        "price": "3890000",
        "old_price": "4290000",
        "stock": 38,
        "description": "صدای شفاف، طراحی سبک و حذف نویز برای موسیقی، تماس و تمرکز روزانه.",
        "highlights": "حذف نویز فعال\nاتصال بی‌سیم پایدار\nکیس شارژ همراه\nکنترل لمسی",
        "guarantee": "۱۲ ماه گارانتی معتبر",
        "variants": (
            ("رنگ", "مشکی گرافیتی", "0"),
            ("رنگ", "سفید صدفی", "0"),
        ),
        "specs": (
            ("نوع اتصال", "Bluetooth"),
            ("قابلیت", "حذف نویز فعال"),
            ("شارژدهی", "تا ۲۴ ساعت با کیس"),
        ),
    },
    {
        "slug": "fittime-smartwatch",
        "name": "ساعت هوشمند FitTime S",
        "name_en": "FitTime Smartwatch",
        "brand": "FitTime",
        "category": "digital",
        "price": "7450000",
        "old_price": "8200000",
        "stock": 19,
        "description": "همراه سبک زندگی فعال؛ اعلان‌ها، فعالیت روزانه و اطلاعات تمرین را یک‌جا ببینید.",
        "highlights": "نمایشگر رنگی\nپایش فعالیت روزانه\nاعلان تماس و پیام\nبند قابل تعویض",
        "guarantee": "۱۲ ماه گارانتی معتبر",
        "variants": (
            ("اندازه قاب", "۴۲ میلی‌متر", "0"),
            ("اندازه قاب", "۴۶ میلی‌متر", "500000"),
        ),
        "specs": (
            ("نمایشگر", "لمسی رنگی"),
            ("حسگرها", "شتاب‌سنج و پایش فعالیت"),
            ("سازگاری", "Android و iOS"),
        ),
    },
    {
        "slug": "powermax-20000",
        "name": "پاوربانک PowerMax 20000",
        "name_en": "PowerMax 20000",
        "brand": "PowerMax",
        "category": "accessories",
        "price": "2150000",
        "old_price": "2450000",
        "stock": 42,
        "description": "ظرفیت مناسب برای شارژ همراه، با طراحی جمع‌وجور و نشانگر میزان انرژی.",
        "highlights": "ظرفیت ۲۰۰۰۰ میلی‌آمپر\nنشانگر شارژ\nمحافظت چندگانه\nمناسب سفر",
        "guarantee": "۱۸ ماه گارانتی معتبر",
        "variants": (
            ("رنگ", "خاکستری", "0"),
            ("رنگ", "سرمه‌ای", "0"),
        ),
        "specs": (
            ("ظرفیت", "۲۰۰۰۰ میلی‌آمپر ساعت"),
            ("درگاه", "USB-A و USB-C"),
            ("حفاظت", "کنترل جریان و دما"),
        ),
    },
    {
        "slug": "wave-mini-speaker",
        "name": "اسپیکر قابل حمل Wave Mini",
        "name_en": "Wave Mini Speaker",
        "brand": "Wave",
        "category": "accessories",
        "price": "2980000",
        "old_price": "3350000",
        "stock": 27,
        "description": "اسپیکر کوچک و خوش‌ساخت برای میز کار، خانه و دورهمی‌های دوستانه.",
        "highlights": "صدای استریو\nاتصال Bluetooth\nطراحی قابل حمل\nباتری داخلی",
        "guarantee": "۱۲ ماه گارانتی معتبر",
        "variants": (
            ("رنگ", "آبی اقیانوسی", "0"),
            ("رنگ", "ذغالی", "0"),
        ),
        "specs": (
            ("اتصال", "Bluetooth و AUX"),
            ("توان خروجی", "۲۰ وات"),
            ("باتری", "قابل شارژ"),
        ),
    },
    {
        "slug": "rgb-mechanical-keyboard",
        "name": "کیبورد مکانیکی RGB",
        "name_en": "RGB Mechanical Keyboard",
        "brand": "KeyPro",
        "category": "accessories",
        "price": "4650000",
        "old_price": "5100000",
        "stock": 16,
        "description": "تجربه تایپ دقیق با کلیدهای مکانیکی و نورپردازی قابل تنظیم.",
        "highlights": "کلیدهای مکانیکی\nنورپردازی RGB\nبدنه مقاوم\nاتصال USB",
        "guarantee": "۱۲ ماه گارانتی معتبر",
        "variants": (
            ("نوع سوئیچ", "قهوه‌ای", "0"),
            ("نوع سوئیچ", "قرمز", "0"),
        ),
        "specs": (
            ("چیدمان", "Full Size"),
            ("نورپردازی", "RGB قابل تنظیم"),
            ("اتصال", "USB"),
        ),
    },
    {
        "slug": "homeglow-smart-lamp",
        "name": "چراغ هوشمند HomeGlow",
        "name_en": "HomeGlow Smart Lamp",
        "brand": "HomeGlow",
        "category": "smart-home",
        "price": "1780000",
        "old_price": "2050000",
        "stock": 31,
        "description": "نورپردازی هوشمند برای فضای کار و خانه با تنظیم شدت و حالت نور.",
        "highlights": "تنظیم شدت نور\nحالت‌های رنگی\nکنترل آسان\nمصرف کم انرژی",
        "guarantee": "۱۲ ماه گارانتی معتبر",
        "variants": (
            ("رنگ بدنه", "سفید", "0"),
            ("رنگ بدنه", "مشکی", "0"),
        ),
        "specs": (
            ("نوع نور", "LED"),
            ("کنترل", "لمسی"),
            ("کاربری", "میز کار و اتاق خواب"),
        ),
    },
    {
        "slug": "compact-brew-coffee-maker",
        "name": "قهوه‌ساز Compact Brew",
        "name_en": "Compact Brew Coffee Maker",
        "brand": "BrewLab",
        "category": "smart-home",
        "price": "5990000",
        "old_price": "6550000",
        "stock": 12,
        "description": "قهوه‌سازی جمع‌وجور با طراحی مینیمال برای شروع دل‌چسب روز.",
        "highlights": "طراحی کم‌جا\nکاربری ساده\nمخزن آب جداشونده\nمناسب خانه و دفتر",
        "guarantee": "۱۸ ماه گارانتی معتبر",
        "variants": (
            ("رنگ", "مشکی مات", "0"),
            ("رنگ", "نقره‌ای", "0"),
        ),
        "specs": (
            ("توان", "۹۰۰ وات"),
            ("ظرفیت مخزن", "۱ لیتر"),
            ("کاربری", "قهوه دمی"),
        ),
    },
)


class Command(BaseCommand):
    help = "Reset and seed the isolated Py_ShopOnline sales demo database."

    def handle(self, *args, **options):
        if not settings.DEMO_MODE:
            raise CommandError("This command is available only when DEMO_MODE=1.")

        if settings.DEBUG:
            raise CommandError("Refusing to seed a Demo database while DEBUG=True.")

        with transaction.atomic():
            self._reset_demo_records()
            categories = self._create_categories()
            products = self._create_products(categories)
            sample_users = self._create_sample_users()
            self._create_reviews(products, sample_users)
            self._create_wishlist(products, sample_users[0])
            shipping_methods = self._create_shipping_methods()
            self._create_sample_orders(products, sample_users, shipping_methods)
            self._configure_storefront()

        self.stdout.write(
            self.style.SUCCESS(
                "Demo data reset and seeded: 3 categories, 8 products, "
                "sample reviews, shipping methods and 5 sample orders."
            )
        )

    def _delete_file_field(self, queryset, field_name):
        for obj in queryset.only("pk", field_name).iterator():
            file_field = getattr(obj, field_name)
            if file_field:
                storage = file_field.storage
                name = file_field.name
                transaction.on_commit(
                    lambda storage=storage, name=name: storage.delete(name)
                )

    def _reset_demo_records(self):
        # The Demo database is exclusive to this application. Clear dependent
        # transaction rows before catalog and user rows to preserve FK rules.
        Order.objects.all().delete()
        Cart.objects.all().delete()
        Address.objects.all().delete()
        Review.objects.all().delete()
        Wishlist.objects.all().delete()
        InventoryMovement.objects.all().delete()

        self._delete_file_field(ProductImage.objects.all(), "image")
        self._delete_file_field(Category.objects.all(), "image")
        self._delete_file_field(Product.objects.all(), "image")
        self._delete_file_field(StorefrontSettings.objects.all(), "logo")

        ProductImage.objects.all().delete()
        ProductSpecification.objects.all().delete()
        ProductVariant.objects.all().delete()
        Product.objects.all().delete()
        Category.objects.all().delete()
        ShippingMethod.objects.all().delete()

        User.objects.filter(
            username__startswith=SAMPLE_USERNAME_PREFIX
        ).delete()
        User.objects.filter(
            username__startswith="demo_customer_"
        ).delete()
        Session.objects.all().delete()

        StorefrontSettings.objects.filter(pk=StorefrontSettings.SINGLETON_PK).update(
            logo=""
        )

    def _create_categories(self):
        categories = {}
        for item in CATEGORIES:
            categories[item["key"]] = Category.objects.create(
                name=item["name"],
                slug=item["slug"],
                is_active=True,
            )
        return categories

    def _create_products(self, categories):
        products = {}
        for item in PRODUCTS:
            product = Product.objects.create(
                name=item["name"],
                name_en=item["name_en"],
                brand=item["brand"],
                category=categories[item["category"]],
                slug=item["slug"],
                description=item["description"],
                review=item["description"],
                price=Decimal(item["price"]),
                old_price=Decimal(item["old_price"]),
                stock=item["stock"],
                is_active=True,
                highlights=item["highlights"],
                guarantee=item["guarantee"],
                delivery_info="ارسال آزمایشی؛ هیچ کالایی واقعاً ارسال نمی‌شود.",
            )

            for variant_name, variant_value, adjustment in item["variants"]:
                variant = ProductVariant(
                    product=product,
                    name=variant_name,
                    value=variant_value,
                    price_adjustment=Decimal(adjustment),
                )
                variant.full_clean()
                variant.save()

            ProductSpecification.objects.bulk_create(
                [
                    ProductSpecification(
                        product=product,
                        title=title,
                        value=value,
                    )
                    for title, value in item["specs"]
                ]
            )
            products[item["slug"]] = product

        return products

    def _create_sample_users(self):
        users = []
        for index in range(1, 6):
            user = User.objects.create_user(
                username=f"{SAMPLE_USERNAME_PREFIX}{index}",
                email=f"buyer{index}@example.invalid",
                first_name=f"مشتری نمونه {index}",
                password=None,
            )
            Profile.objects.get_or_create(user=user)
            users.append(user)
        return users

    def _create_reviews(self, products, users):
        comments = (
            "کیفیت ساخت و تجربه استفاده بسیار خوب بود.",
            "توضیحات محصول با چیزی که دریافت کردم هماهنگ بود.",
            "طراحی زیبا و کاربری ساده‌ای دارد.",
            "برای استفاده روزمره انتخاب مناسبی است.",
            "بسته‌بندی و تجربه خرید در نمونه آزمایشی قابل قبول بود.",
        )
        for index, item in enumerate(PRODUCTS):
            Review.objects.create(
                product=products[item["slug"]],
                user=users[index % len(users)],
                rating=5 if index % 3 else 4,
                comment=comments[index % len(comments)],
                is_approved=True,
            )

    def _create_wishlist(self, products, user):
        wishlist, _ = Wishlist.objects.get_or_create(user=user)
        wishlist.products.add(
            products["soundpods-pro"],
            products["fittime-smartwatch"],
        )

    def _create_shipping_methods(self):
        methods = (
            ("ارسال استاندارد (نمونه)", "85000", "3"),
            ("ارسال سریع (نمونه)", "145000", "1"),
            ("تحویل حضوری (نمونه)", "0", "1"),
        )
        return [
            ShippingMethod.objects.create(
                name=name,
                description="این روش فقط در محیط آزمایشی نمایش داده می‌شود؛ ارسال واقعی انجام نمی‌شود.",
                cost=Decimal(cost),
                estimated_delivery_days=int(days),
                is_active=True,
            )
            for name, cost, days in methods
        ]

    def _create_sample_orders(self, products, users, shipping_methods):
        sample_orders = (
            {
                "status": "paid",
                "product": "nova-x5-phone",
                "quantity": 1,
                "shipping": 0,
            },
            {
                "status": "processing",
                "product": "soundpods-pro",
                "quantity": 2,
                "shipping": 1,
            },
            {
                "status": "completed",
                "product": "fittime-smartwatch",
                "quantity": 1,
                "shipping": 0,
            },
            {
                "status": "pending",
                "product": "powermax-20000",
                "quantity": 1,
                "shipping": 1,
            },
            {
                "status": "cancelled",
                "product": "wave-mini-speaker",
                "quantity": 1,
                "shipping": 0,
            },
        )

        for index, spec in enumerate(sample_orders):
            user = users[index]
            product = products[spec["product"]]
            quantity = spec["quantity"]
            shipping_method = shipping_methods[spec["shipping"]]
            subtotal = product.price * quantity
            shipping = shipping_method.calculate_shipping_cost(subtotal)

            order = Order.objects.create(
                user=user,
                status=Order.Status.PENDING,
                shipping_method=shipping_method,
                subtotal_amount=subtotal,
                discount_amount=Decimal("0"),
                shipping_amount=shipping,
                customer_note="سفارش نمونه؛ هیچ ارسال یا پرداخت واقعی انجام نمی‌شود.",
            )
            OrderAddressSnapshot.objects.create(
                order=order,
                recipient_name=f"مشتری نمونه {index + 1}",
                recipient_mobile="00000000000",
                postal_code="0000000000",
                province="تهران",
                city="تهران",
                address_line="نشانی آزمایشی — ارسال واقعی انجام نمی‌شود.",
            )
            OrderItem.objects.create(
                order=order,
                product=product,
                variant_id=None,
                product_name=product.name,
                variant_name="",
                sku=None,
                quantity=quantity,
                unit_price=product.price,
                subtotal_price=subtotal,
            )
            payment = Payment.objects.create(
                order=order,
                user=user,
                amount=order.final_amount,
                status=Payment.Status.PENDING,
                gateway_name="mock_gateway",
                transaction_code=f"DEMO-TRX-{index + 1:04d}",
            )

            if spec["status"] in {"paid", "processing", "completed"}:
                payment.update_status_and_order(
                    new_status=Payment.Status.SUCCESS,
                    transaction_id=payment.transaction_code,
                    reference_id=f"DEMO-REF-{index + 1:04d}",
                    gateway_response=json.dumps(
                        {
                            "demo": True,
                            "provider": "mock_gateway",
                            "result": "success",
                        }
                    ),
                )
                if spec["status"] in {"processing", "completed"}:
                    OrderService.transition_status(
                        order_id=order.pk,
                        new_status=Order.Status.PROCESSING,
                    )
                if spec["status"] == "completed":
                    OrderService.transition_status(
                        order_id=order.pk,
                        new_status=Order.Status.COMPLETED,
                    )

            elif spec["status"] == "cancelled":
                payment.update_status_and_order(
                    new_status=Payment.Status.FAILED,
                    transaction_id=payment.transaction_code,
                    gateway_response=json.dumps(
                        {
                            "demo": True,
                            "provider": "mock_gateway",
                            "result": "failed",
                        }
                    ),
                )
                OrderService.transition_status(
                    order_id=order.pk,
                    new_status=Order.Status.CANCELLED,
                )

    def _configure_storefront(self):
        settings_row, _ = StorefrontSettings.objects.get_or_create(
            pk=StorefrontSettings.SINGLETON_PK
        )
        settings_row.store_name = "فروشگاه نمونه Py_ShopOnline"
        settings_row.logo = ""
        settings_row.hero_title_line_1 = "یک تجربه کامل از فروشگاه آنلاین شما"
        settings_row.hero_title_line_2 = "سریع، زیبا و قابل مدیریت؛ از محصول تا سفارش"
        settings_row.instagram_url = ""
        settings_row.whatsapp_url = ""
        settings_row.telegram_url = ""
        settings_row.bale_url = ""
        settings_row.save()
