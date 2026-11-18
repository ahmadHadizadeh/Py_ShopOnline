"""
One-off, idempotent catalog completion for the isolated Py_ShopOnline Demo instance.

Run only on Py_ShopOnline_Demo with its own .env and database:
    python manage.py shell < scripts/complete_demo_catalog.py

The script never changes production settings or databases.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import time
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.text import slugify
from PIL import Image, UnidentifiedImageError

from catalog.models import (
    Category,
    Product,
    ProductImage,
    ProductSpecification,
    ProductVariant,
    Review,
)

GALLERY_TARGET = 6
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
TIMEOUT = 25
MAX_IMAGE_BYTES = 18 * 1024 * 1024

# Exact official/model pages and public product image URLs used as image sources.
# The downloader also extracts additional gallery assets from these product pages.
PRODUCTS = [
    {
        "slug": "demo-phone-a",
        "name": "گوشی سامسونگ Galaxy A56 5G",
        "name_en": "Samsung Galaxy A56 5G",
        "brand": "Samsung",
        "category": "mobile",
        "price": 135500000,
        "old_price": None,
        "stock": 12,
        "description": (
            "Samsung Galaxy A56 5G یک گوشی میان‌ردهٔ قدرتمند با نمایشگر بزرگ "
            "Super AMOLED، نرخ نوسازی ۱۲۰ هرتز و دوربین اصلی ۵۰ مگاپیکسلی مجهز "
            "به لرزش‌گیر اپتیکال است. نسخهٔ این صفحه با حافظهٔ پایهٔ ۱۲۸ گیگابایت "
            "معرفی شده و گزینهٔ ۲۵۶ گیگابایتی نیز در انتخاب‌ها قرار دارد. "
            "ظرفیت حافظه و پیکربندی نهایی باید با برچسب جعبه و کد دقیق دستگاه تطبیق داده شود."
        ),
        "highlights": (
            "نمایشگر ۶٫۷ اینچ FHD+ Super AMOLED تا ۱۲۰ هرتز، "
            "پردازنده Exynos 1580، دوربین اصلی ۵۰ مگاپیکسل با OIS، "
            "باتری ۵۰۰۰ میلی‌آمپرساعت، مقاومت IP67"
        ),
        "review": (
            "Galaxy A56 5G برای کاربرانی طراحی شده که نمایشگر باکیفیت، دوربین "
            "اصلی مجهز به OIS و پشتیبانی از شبکهٔ 5G را در یک گوشی متعادل می‌خواهند.\n\n"
            "نمایشگر ۶٫۷ اینچی Super AMOLED وضوح FHD+ و نرخ نوسازی حداکثر ۱۲۰ هرتز "
            "دارد. مجموعهٔ دوربین پشتی شامل حسگر اصلی ۵۰ مگاپیکسلی با لرزش‌گیر "
            "اپتیکال، دوربین فوق‌عریض ۱۲ مگاپیکسلی و دوربین ماکرو ۵ مگاپیکسلی است. "
            "دوربین سلفی ۱۲ مگاپیکسل است.\n\n"
            "پردازندهٔ Exynos 1580، باتری ۵۰۰۰ میلی‌آمپرساعتی، شارژ سیمی ۴۵ وات "
            "و گواهی IP67 از مشخصات اصلی این مدل هستند. حافظه‌های ۱۲۸ و ۲۵۶ "
            "گیگابایت در بازارهای مختلف عرضه می‌شوند و پیکربندی رم نیز ممکن است "
            "با توجه به منطقه متفاوت باشد."
        ),
        "specs": [
            ("نمایشگر", "۶٫۷ اینچ FHD+ Super AMOLED، حداکثر ۱۲۰ هرتز"),
            ("پردازنده", "Samsung Exynos 1580"),
            ("دوربین پشتی", "۵۰ مگاپیکسل اصلی با OIS + ۱۲ مگاپیکسل فوق‌عریض + ۵ مگاپیکسل ماکرو"),
            ("دوربین سلفی", "۱۲ مگاپیکسل"),
            ("باتری", "۵۰۰۰ میلی‌آمپرساعت"),
            ("شارژ", "شارژ سیمی تا ۴۵ وات"),
            ("مقاومت", "IP67 در برابر گردوغبار و آب در شرایط آزمایشگاهی"),
            ("سیستم‌عامل زمان عرضه", "Android 15 با One UI 7"),
        ],
        "variants": [
            ("حافظه", "۱۲۸ گیگابایت", 0),
            ("حافظه", "۲۵۶ گیگابایت", 10400000),
        ],
        "pages": [
            "https://www.samsungmobilepress.com/media-assets/galaxy-a56-5g?tab=images&type=product",
            "https://www.samsung.com/ae/smartphones/galaxy-a/galaxy-a56-5g-awesome-graphite-128gb-sm-a566bzkvmda/",
            "https://www.samsung-online.com.ua/ua/smartfon-samsung-galaxy-a56-5g-8-128gb-sm-a566bzkaeuc-awesome-graphite17418716581741871673",
        ],
        "image_urls": [
            "https://api.samsungmobilepress.com/api/v1/file/40C8A8320E66A4CAC1D4C645A46F70A08A6D3C1507FC8C4E8B5824624F49D488520B61EDFFEB06BFCA15F1EBBCAE7E12A54C4F5DEA14FB8E988573124D9F9D5CE6FEA7132429C5C5BA97F28D0AD1D02ACF46167294946C7BFF3383555E03789FB445E76A0C97032FAB04199698385B4326A14D6D82F1172D6DE91F72028D4AFC6022AF43EBD8CBAC88561FE5A8DEC663",
            "https://www.samsung-online.com.ua/uploads/shop/products/large/8f83d93860572ccfc20de6f81911de66.jpg",
        ],
    },
    {
        "slug": "demo-phone-b",
        "name": "گوشی شیائومی Redmi Note 14 Pro 5G",
        "name_en": "Xiaomi Redmi Note 14 Pro 5G",
        "brand": "Xiaomi",
        "category": "mobile",
        "price": 64900000,
        "old_price": None,
        "stock": 8,
        "description": (
            "Redmi Note 14 Pro 5G به نمایشگر ۱٫۵K AMOLED با نرخ نوسازی ۱۲۰ هرتز، "
            "دوربین اصلی ۲۰۰ مگاپیکسلی مجهز به OIS و پردازندهٔ MediaTek Dimensity "
            "7300-Ultra مجهز است. این صفحه پیکربندی ۲۵۶ گیگابایتی را مبنا قرار می‌دهد؛ "
            "رنگ و ظرفیت دقیق بسته به موجودی منطقه‌ای قابل انتخاب است."
        ),
        "highlights": (
            "نمایشگر ۶٫۶۷ اینچ CrystalRes AMOLED، وضوح 1.5K و ۱۲۰ هرتز، "
            "دوربین اصلی ۲۰۰ مگاپیکسل با OIS، باتری ۵۱۱۰ میلی‌آمپرساعت، "
            "شارژ ۴۵ وات، مقاومت IP68"
        ),
        "review": (
            "نقطهٔ قوت Redmi Note 14 Pro 5G ترکیب نمایشگر دقیق و دوربین اصلی "
            "با وضوح بالا است. نمایشگر ۶٫۶۷ اینچی CrystalRes AMOLED وضوح "
            "۲۷۱۲×۱۲۲۰ و نرخ نوسازی حداکثر ۱۲۰ هرتز دارد.\n\n"
            "دوربین اصلی ۲۰۰ مگاپیکسلی از لرزش‌گیر اپتیکال استفاده می‌کند و در کنار "
            "آن دوربین فوق‌عریض ۸ مگاپیکسلی و ماکرو ۲ مگاپیکسلی قرار گرفته‌اند. "
            "پردازندهٔ ۴ نانومتری Dimensity 7300-Ultra، باتری ۵۱۱۰ میلی‌آمپرساعتی "
            "و شارژ ۴۵ وات از مشخصات کلیدی دستگاه هستند.\n\n"
            "گواهی IP68 و شیشهٔ Gorilla Glass Victus 2 نیز در مشخصات نسخهٔ جهانی "
            "ذکر شده‌اند. جزئیات ظرفیت رم و حافظه در بازارهای مختلف متفاوت است."
        ),
        "specs": [
            ("نمایشگر", "۶٫۶۷ اینچ CrystalRes AMOLED، وضوح ۲۷۱۲×۱۲۲۰، حداکثر ۱۲۰ هرتز"),
            ("پردازنده", "MediaTek Dimensity 7300-Ultra، فناوری ساخت ۴ نانومتری"),
            ("دوربین پشتی", "۲۰۰ مگاپیکسل OIS + ۸ مگاپیکسل فوق‌عریض + ۲ مگاپیکسل ماکرو"),
            ("دوربین سلفی", "۲۰ مگاپیکسل"),
            ("باتری", "۵۱۱۰ میلی‌آمپرساعت"),
            ("شارژ", "شارژ سریع ۴۵ وات"),
            ("مقاومت", "IP68 و محافظ نمایشگر Gorilla Glass Victus 2"),
            ("حافظه‌های عرضه‌شده", "۸/۲۵۶، ۱۲/۲۵۶ و ۱۲/۵۱۲ گیگابایت؛ بسته به بازار"),
        ],
        "variants": [
            ("رنگ", "مشکی نیمه‌شب", 0),
            ("رنگ", "بنفش اسطوخودوسی", 0),
        ],
        "pages": [
            "https://www.mi.com/global/product/redmi-note-14-pro-5g/",
            "https://www.mistore-greece.gr/Products/Smartphones/Redmi-Series/smartphone-redmi-note-14-pro-5g-purple-8-256gb.aspx",
        ],
        "image_urls": [
            "https://i02.appmifile.com/mi-com-product/fly-birds/redmi-note-14-pro-5g/PC/redmi-note-14-pc1.webp",
            "https://i02.appmifile.com/mi-com-product/fly-birds/redmi-note-14-pro-5g/PC/e481327f4ed5d5d3a230364c93ec857f.jpg",
            "https://i02.appmifile.com/mi-com-product/fly-birds/redmi-note-14-pro-5g/PC/04479d6b5fed559931976b0d3d141539.jpg",
            "https://i02.appmifile.com/mi-com-product/fly-birds/redmi-note-14-pro-5g/PC/3776f93ef1293344e6d636e5e02e9288.jpg",
            "https://i02.appmifile.com/mi-com-product/fly-birds/redmi-note-14-pro-5g/PC/fd0bcf27ac82bdf2d9d2aba8fdd96f53.jpg",
            "https://i02.appmifile.com/mi-com-product/fly-birds/redmi-note-14-pro-5g/PC/iamge_3.jpg",
            "https://i02.appmifile.com/mi-com-product/fly-birds/redmi-note-14-pro-5g/PC/iamge_4.jpg",
            "https://i05.appmifile.com/481_item_uk/06/01/2025/4567bfdb92c367a0a1af3845579ff954.png?q=85&thumb=1",
            "https://www.mistore-greece.gr/getmetafile/5c2b264e-0503-4cff-a922-9b60808d532d/note14propurple-5g.aspx",
        ],
    },
    {
        "slug": "demo-tablet",
        "name": "تبلت Samsung Galaxy Tab A9+",
        "name_en": "Samsung Galaxy Tab A9+",
        "brand": "Samsung",
        "category": "mobile",
        "price": 89000000,
        "old_price": None,
        "stock": 10,
        "description": (
            "Galaxy Tab A9+ تبلتی ۱۱ اینچی برای مطالعه، کلاس آنلاین، تماشای محتوا "
            "و کارهای روزمره است. نمایشگر WUXGA با نرخ نوسازی ۹۰ هرتز، چهار بلندگو "
            "با Dolby Atmos و پشتیبانی از کارت microSD از ویژگی‌های مهم آن هستند."
        ),
        "highlights": (
            "نمایشگر ۱۱ اینچ WUXGA با نرخ ۹۰ هرتز، پردازنده Qualcomm SM6375، "
            "۴ یا ۸ گیگابایت RAM، حافظهٔ ۶۴ یا ۱۲۸ گیگابایت، باتری ۷۰۴۰ میلی‌آمپرساعت"
        ),
        "review": (
            "Galaxy Tab A9+ برای استفادهٔ روزانه، مطالعه، آموزش آنلاین و سرگرمی "
            "طراحی شده است. نمایشگر ۱۱ اینچی TFT LCD با وضوح ۱۹۲۰×۱۲۰۰ و نرخ "
            "نوسازی ۹۰ هرتز فضای مناسبی برای خواندن و تماشای ویدئو فراهم می‌کند.\n\n"
            "این مدل از پلتفرم Qualcomm SM6375، چهار بلندگو با Dolby Atmos، "
            "دوربین پشتی ۸ مگاپیکسل و دوربین جلویی ۵ مگاپیکسل بهره می‌برد. "
            "پیکربندی‌های ۴/۶۴ و ۸/۱۲۸ گیگابایت عرضه شده‌اند و کارت microSD "
            "نیز برای افزایش فضای ذخیره‌سازی پشتیبانی می‌شود.\n\n"
            "باتری ۷۰۴۰ میلی‌آمپرساعتی و درگاه USB-C از دیگر ویژگی‌های دستگاه هستند. "
            "مدل Wi-Fi و نسخهٔ 5G باید هنگام ثبت سفارش از یکدیگر تفکیک شوند."
        ),
        "specs": [
            ("نمایشگر", "۱۱ اینچ TFT LCD، وضوح WUXGA ۱۹۲۰×۱۲۰۰، نرخ ۹۰ هرتز"),
            ("پردازنده", "Qualcomm SM6375، گرافیک Adreno 619"),
            ("دوربین پشتی", "۸ مگاپیکسل با فوکوس خودکار"),
            ("دوربین جلو", "۵ مگاپیکسل"),
            ("حافظه", "۴GB RAM / ۶۴GB یا ۸GB RAM / ۱۲۸GB"),
            ("باتری", "۷۰۴۰ میلی‌آمپرساعت"),
            ("صدا", "چهار بلندگو با پشتیبانی Dolby Atmos"),
            ("درگاه", "USB Type-C و microSD تا ۱ ترابایت"),
        ],
        "variants": [
            ("رم و حافظه", "۴ گیگابایت RAM / ۶۴ گیگابایت", 0),
            ("رم و حافظه", "۸ گیگابایت RAM / ۱۲۸ گیگابایت", 12000000),
        ],
        "pages": [
            "https://item.rakuten.co.jp/samsungonline/tab_a9plus/",
            "https://www.samsung.com/us/tablets/galaxy-tab-a9-plus/",
        ],
        "image_urls": [
            "https://shop.r10s.jp/samsungonline/cabinet/prd/tab_a9plus/a9plus.jpg",
            "https://images.tcdn.com.br/img/img_prod/777993/tablet_samsung_galaxy_tab_a9_wi_fi_11_64gb_4gb_octa_core_2_2ghz_android_bivolt_grafite_129723_1_69fe5f4ff3c44e5951e36b63a2050446.jpg",
            "https://cdn.cimri.io/image/1000x1000/samsung-galaxy-tab-a9-plus-sm-x210-wi-fi-128gb-11-inc-gumus-tablet-pc_824618537.jpg",
        ],
    },
    {
        "slug": "demo-laptop-a",
        "name": "لپ‌تاپ ASUS Vivobook 15 X1504VA",
        "name_en": "ASUS Vivobook 15 X1504VA",
        "brand": "ASUS",
        "category": "computer",
        "price": 194500000,
        "old_price": None,
        "stock": 5,
        "description": (
            "ASUS Vivobook 15 از خانوادهٔ X1504 برای کاربری روزمره، اداری و تحصیلی "
            "ساخته شده است. صفحه‌نمایش ۱۵٫۶ اینچی Full HD، حافظهٔ SSD پرسرعت و "
            "قابلیت ارتقای RAM در برخی پیکربندی‌ها از ویژگی‌های این خانواده است. "
            "پردازنده و ظرفیت حافظه باید با کد SKU درج‌شده روی جعبه تطبیق داده شود."
        ),
        "highlights": (
            "نمایشگر ۱۵٫۶ اینچ Full HD، حافظه SSD از نوع M.2 NVMe، "
            "رم DDR5 قابل ارتقا در پیکربندی‌های منتخب، درگاه USB-C، HDMI"
        ),
        "review": (
            "Vivobook 15 X1504 یک خانوادهٔ چندپیکربندی است؛ بنابراین مشخصات "
            "پردازنده و رم در همهٔ SKUها یکسان نیست. نسخه‌های عرضه‌شده می‌توانند "
            "با پردازنده‌های مختلف Intel Core و ترکیب‌های متفاوت حافظه ارائه شوند.\n\n"
            "نمایشگر ۱۵٫۶ اینچی Full HD با نرخ نوسازی ۶۰ هرتز و روکش مات، SSD "
            "از نوع M.2 NVMe PCIe 4.0 و مجموعه‌ای از درگاه‌های USB، USB-C، HDMI "
            "و جک صوتی از ویژگی‌های این سری هستند.\n\n"
            "در برخی پیکربندی‌های X1504 حافظهٔ DDR5 قابل ارتقا است و سقف پشتیبانی "
            "می‌تواند تا ۱۶ گیگابایت باشد؛ اما بعضی SKUها محدودیت متفاوت دارند. "
            "برای همین، مقدار رم انتخاب‌شده باید با شماره‌مدل دقیق دستگاه بررسی شود."
        ),
        "specs": [
            ("خانواده مدل", "ASUS Vivobook 15 X1504VA؛ مشخصات دقیق وابسته به SKU"),
            ("نمایشگر", "۱۵٫۶ اینچ Full HD، ۱۹۲۰×۱۰۸۰، ۶۰ هرتز، روکش مات"),
            ("پردازنده", "بسته به SKU؛ در مدل‌های عرضه‌شده Core 3 / Core 5 / Core 7"),
            ("حافظه RAM", "DDR5؛ برخی پیکربندی‌ها تا ۱۶ گیگابایت"),
            ("ذخیره‌سازی", "SSD از نوع M.2 NVMe PCIe 4.0؛ ظرفیت وابسته به SKU"),
            ("درگاه‌ها", "USB-A، USB-C، HDMI 1.4، جک ترکیبی ۳٫۵ میلی‌متری"),
            ("کاربری", "اداری، آموزشی و استفادهٔ روزمره"),
        ],
        "variants": [
            ("حافظه RAM", "۱۶ گیگابایت", 0),
        ],
        "pages": [
            "https://www.asus.com/laptops/for-students/vivobook/asus-vivobook-15-x1504/",
            "https://www.asus.com/bt/laptops/for-home/vivobook/asus-vivobook-15-x1504/",
        ],
        "image_urls": [
            "https://dlcdnwebimgs.asus.com/gain/0dea7490-edcd-4bfb-80e4-0324085f46fc/",
            "https://dlcdnwebimgs.asus.com/gain/97b2619d-f306-4952-835d-2f80efb2dec3/",
            "https://dlcdnwebimgs.asus.com/gain/7d48961d-95e3-4f95-a4b9-37c0a1dbd42c/",
            "https://dlcdnwebimgs.asus.com/gain/0cfaceed-08d5-4480-ad9e-b75289dce4e2/",
        ],
    },
    {
        "slug": "demo-laptop-b",
        "name": "لپ‌تاپ Lenovo IdeaPad Slim 3 15AMN8",
        "name_en": "Lenovo IdeaPad Slim 3 15AMN8",
        "brand": "Lenovo",
        "category": "computer",
        "price": 99000000,
        "old_price": None,
        "stock": 7,
        "description": (
            "IdeaPad Slim 3 15AMN8 در پیکربندی مرجع این صفحه با Ryzen 5 7520U، "
            "رم ۸ گیگابایت LPDDR5 و SSD با ظرفیت ۵۱۲ گیگابایت معرفی می‌شود. "
            "رم این پیکربندی روی برد لحیم شده و قابل ارتقا نیست؛ کد مدل 82XQ00VCAU "
            "برای تطبیق دقیق مشخصات درج شده است."
        ),
        "highlights": (
            "AMD Ryzen 5 7520U، گرافیک Radeon 610M، رم ۸ گیگابایت LPDDR5، "
            "SSD ۵۱۲ گیگابایت، نمایشگر ۱۵٫۶ اینچ Full HD"
        ),
        "review": (
            "IdeaPad Slim 3 15AMN8 گزینه‌ای اقتصادی برای وب‌گردی، کارهای اداری، "
            "آموزش آنلاین و نرم‌افزارهای روزمره است. پیکربندی مرجع 82XQ00VCAU "
            "از پردازندهٔ AMD Ryzen 5 7520U با چهار هسته و هشت رشته استفاده می‌کند.\n\n"
            "گرافیک مجتمع Radeon 610M، حافظهٔ ۸ گیگابایت LPDDR5-5500 و SSD "
            "۵۱۲ گیگابایتی از اجزای اصلی این SKU هستند. رم این نسخه روی مادربرد "
            "لحیم شده و اسلات ارتقا ندارد؛ بنابراین ظرفیت ۸ گیگابایت باید پیش از "
            "خرید به‌دقت بررسی شود.\n\n"
            "نمایشگر ۱۵٫۶ اینچی Full HD، وب‌کم HD با درپوش حریم خصوصی، Wi-Fi 6، "
            "Bluetooth 5.2 و باتری ۴۷ وات‌ساعتی از مشخصات تکمیلی نسخهٔ مرجع هستند."
        ),
        "specs": [
            ("مدل دقیق مرجع", "82XQ00VCAU"),
            ("پردازنده", "AMD Ryzen 5 7520U، چهار هسته و هشت رشته"),
            ("گرافیک", "AMD Radeon 610M مجتمع"),
            ("حافظه RAM", "۸ گیگابایت LPDDR5-5500 لحیم‌شده؛ غیرقابل ارتقا"),
            ("ذخیره‌سازی", "SSD M.2 NVMe با ظرفیت ۵۱۲ گیگابایت"),
            ("نمایشگر", "۱۵٫۶ اینچ Full HD، پنل TN در SKU مرجع"),
            ("باتری", "۴۷ وات‌ساعت"),
            ("اتصالات", "Wi-Fi 6 و Bluetooth 5.2"),
            ("وب‌کم", "HD 720p با درپوش حریم خصوصی"),
        ],
        "variants": [],
        "pages": [
            "https://psref.lenovo.com/Detail/2183?M=82XQ00VCAU",
            "https://www.regard.ru/product/790189/noutbuk-lenovo-ideapad-slim-3-15amn8-arctic-grey-82xq01c9ps",
        ],
        "image_urls": [
            "https://www.regard.ru/api/photo/goods/6617258.png",
            "https://media.falabella.com/falabellaPE/141657575_03/w%3D1500%2Ch%3D1500%2Cfit%3Dcover",
        ],
    },
    {
        "slug": "demo-router",
        "name": "روتر دو بانده TP-Link Archer C6 AC1200",
        "name_en": "TP-Link Archer C6 AC1200 Dual-Band Router",
        "brand": "TP-Link",
        "category": "network",
        "price": 3250000,
        "old_price": 3900000,
        "stock": 20,
        "description": (
            "Archer C6 یک روتر بی‌سیم دو بانده AC1200 برای خانه و دفتر کوچک است. "
            "این مدل از باندهای ۲٫۴ و ۵ گیگاهرتز، پورت‌های گیگابیتی و فناوری MU-MIMO "
            "پشتیبانی می‌کند. این محصول تک‌بانده نیست؛ نسخهٔ سخت‌افزاری دستگاه "
            "هنگام خرید باید با برچسب زیر روتر تطبیق داده شود."
        ),
        "highlights": (
            "Wi-Fi دو بانده AC1200، سرعت اسمی ۳۰۰Mbps در ۲٫۴GHz و ۸۶۷Mbps در ۵GHz، "
            "چهار آنتن خارجی، پورت گیگابیتی، MU-MIMO"
        ),
        "review": (
            "TP-Link Archer C6 برای ایجاد یا گسترش شبکهٔ بی‌سیم خانگی مناسب است. "
            "این روتر از استاندارد بی‌سیم AC1200 استفاده می‌کند و باند ۲٫۴ گیگاهرتز "
            "تا ۳۰۰ مگابیت‌برثانیه و باند ۵ گیگاهرتز تا ۸۶۷ مگابیت‌برثانیه سرعت "
            "پیوند اسمی ارائه می‌دهند؛ سرعت واقعی به محیط، دستگاه و تداخل رادیویی "
            "وابسته است.\n\n"
            "چهار آنتن خارجی، یک آنتن داخلی، پورت‌های گیگابیتی و پشتیبانی MU-MIMO "
            "از ویژگی‌های این مدل هستند. نسخهٔ سخت‌افزاری و منطقهٔ عرضه ممکن است "
            "برخی قابلیت‌ها را تغییر دهد؛ اطلاعات زیر دستگاه ملاک نهایی است."
        ),
        "specs": [
            ("استاندارد بی‌سیم", "IEEE 802.11ac/n/a در ۵GHz و 802.11n/b/g در ۲٫۴GHz"),
            ("سرعت اسمی", "تا ۸۶۷Mbps در ۵GHz و ۳۰۰Mbps در ۲٫۴GHz"),
            ("آنتن", "چهار آنتن خارجی و یک آنتن داخلی؛ بسته به نسخه"),
            ("درگاه شبکه", "پورت‌های Gigabit Ethernet؛ تعداد و نقش WAN/LAN وابسته به نسخه"),
            ("فناوری", "MU-MIMO و Access Point Mode"),
            ("نوع محصول", "روتر بی‌سیم دو بانده؛ نه تک بانده"),
        ],
        "variants": [
            ("مدل", "Archer C6 AC1200 دو بانده", 0),
        ],
        "pages": [
            "https://www.tp-link.com/us/home-networking/wifi-router/archer-c6/",
            "https://www.sysllc.ae/ac1200-wireless-mu-mimo-gigabit-router.html",
        ],
        "image_urls": [
            "https://www.sysllc.ae/pub/media/catalog/product/a/r/archer-c6_1.jpg",
            "https://bizweb.dktcdn.net/100/592/668/products/screenshot-2025-09-10-145900.png?v=1757491226440",
            "https://cdn.shopify.com/s/files/1/0587/4348/8710/products/11.0TL-ARCHERC6_a70a3389-bdab-4673-8e50-e1ece83a9c39.png?v=1653358036",
        ],
    },
    {
        "slug": "demo-switch",
        "name": "سوئیچ شبکه TP-Link TL-SG108 هشت پورت گیگابیتی",
        "name_en": "TP-Link TL-SG108 8-Port Gigabit Desktop Switch",
        "brand": "TP-Link",
        "category": "network",
        "price": 2150000,
        "old_price": None,
        "stock": 15,
        "description": (
            "TL-SG108 یک سوئیچ دسکتاپ غیرمدیریتی با هشت درگاه گیگابیتی است. "
            "بدنهٔ فلزی، طراحی بدون فن و نصب Plug and Play آن را برای گسترش "
            "شبکهٔ خانگی و اداری کوچک مناسب می‌کند."
        ),
        "highlights": (
            "۸ پورت ۱۰/۱۰۰/۱۰۰۰، بدنه فلزی، طراحی بدون فن، "
            "Plug and Play، پشتیبانی QoS و IGMP Snooping"
        ),
        "review": (
            "سوئیچ TL-SG108 برای افزودن هشت اتصال سیمی گیگابیتی به شبکه طراحی شده است. "
            "هر هشت درگاه از Auto-Negotiation و Auto-MDI/MDIX پشتیبانی می‌کنند و "
            "برای راه‌اندازی اولیه به پیکربندی نرم‌افزاری نیاز نیست.\n\n"
            "بدنهٔ فلزی، طراحی بدون فن، کنترل جریان IEEE 802.3x، قابلیت QoS و "
            "IGMP Snooping از ویژگی‌های مهم آن هستند. این مدل یک سوئیچ غیرمدیریتی "
            "است و جایگزین سوئیچ‌های مدیریتی لایهٔ سازمانی محسوب نمی‌شود."
        ),
        "specs": [
            ("تعداد درگاه", "۸ عدد RJ45"),
            ("سرعت هر درگاه", "۱۰/۱۰۰/۱۰۰۰ مگابیت‌برثانیه"),
            ("استانداردها", "IEEE 802.3i / 802.3u / 802.3ab / 802.3x / 802.1p"),
            ("قابلیت‌ها", "Auto-Negotiation، Auto-MDI/MDIX، QoS، IGMP Snooping"),
            ("بدنه", "فلزی، بدون فن، رومیزی یا نصب دیواری"),
            ("ابعاد", "۱۵۸ × ۱۰۱ × ۲۵ میلی‌متر"),
            ("توان مصرفی حداکثر", "۳٫۷۵ وات"),
        ],
        "variants": [],
        "pages": [
            "https://www.tp-link.com/us/business-networking/soho-switch-unmanaged/tl-sg108/",
        ],
        "image_urls": [
            "https://static.tp-link.com/upload/image-line/TL-SG108_UN_5.0_01_normal_20231024111419n.jpg",
            "https://static.tp-link.com/upload/image-line/TL-SG108_UN_5.0_02_normal_20231024111419p.jpg",
            "https://static.tp-link.com/upload/image-line/TL-SG108%28UN%29V9_B_S_normal_20231024111602f.jpg",
            "https://static.tp-link.com/upload/image-line/TL-SG108_UN_5.0_03_normal_20231024111420i.jpg",
            "https://static.tp-link.com/upload/image-line/TL-SG108_UN_5.0_04_normal_20231024111420b.jpg",
            "https://static.tp-link.com/upload/image-line/TL-SG108_UN_5.0_05_normal_20231024111421f.jpg",
            "https://gfx3.senetic.com/akeneo-catalog/f/6/3/a/f63aee7af250f564db4f8aa58d0955678e803897_1662055_TL_SG108_image2.jpg",
        ],
    },
    {
        "slug": "demo-headphone",
        "name": "هدفون بی‌سیم Sony WH-CH520",
        "name_en": "Sony WH-CH520 Wireless Headphones",
        "brand": "Sony",
        "category": "accessories",
        "price": 10972500,
        "old_price": None,
        "stock": 18,
        "description": (
            "WH-CH520 هدفون بی‌سیم سبک سونی با اتصال Bluetooth 5.2، شارژ USB-C "
            "و طراحی روگوشی است. باتری برای استفادهٔ طولانی طراحی شده و زمان شارژ "
            "کامل اعلام‌شده در مشخصات پشتیبانی حدود سه ساعت است."
        ),
        "highlights": (
            "Bluetooth 5.2، درایور ۳۰ میلی‌متری، شارژ USB-C، "
            "پشتیبانی از SBC و AAC، وزن حدود ۱۴۷ گرم"
        ),
        "review": (
            "Sony WH-CH520 یک هدفون بی‌سیم روگوشی برای گوش‌دادن روزمره، تماس و "
            "استفاده در مسیر رفت‌وآمد است. اتصال Bluetooth 5.2 و پشتیبانی از "
            "کدک‌های SBC و AAC در مشخصات رسمی این مدل ذکر شده‌اند.\n\n"
            "شارژ از طریق USB Type-C انجام می‌شود و زمان شارژ کامل حدود سه ساعت "
            "اعلام شده است. سونی حداکثر ۴۰ ساعت زمان مکالمهٔ پیوسته را در صفحهٔ "
            "مشخصات پشتیبانی درج کرده است؛ زمان پخش موسیقی می‌تواند متفاوت باشد."
        ),
        "specs": [
            ("نوع", "هدفون بی‌سیم روگوشی، پشت‌بسته"),
            ("نسخه بلوتوث", "Bluetooth 5.2"),
            ("درایور", "۳۰ میلی‌متر"),
            ("کدک‌های صوتی", "SBC و AAC"),
            ("درگاه شارژ", "USB Type-C"),
            ("زمان شارژ", "حدود ۳ ساعت"),
            ("زمان مکالمه پیوسته", "حداکثر ۴۰ ساعت طبق مشخصات پشتیبانی سونی"),
            ("وزن", "حدود ۱۴۷ گرم"),
        ],
        "variants": [
            ("رنگ", "مشکی", 0),
            ("رنگ", "سفید", 0),
        ],
        "pages": [
            "https://www.sony.com/electronics/support/wireless-headphones-bluetooth-headphones/wh-ch520/specifications",
            "https://powermaccenter.com/products/sony-wh-ch520-wireless-headphones-black",
        ],
        "image_urls": [
            "https://powermaccenter.com/cdn/shop/files/SNYI099BLK012000x2000.jpg?v=1692058746&width=1445",
            "https://digitalcontent.api.tesco.com/v2/media/ghs/9c71980c-0884-4699-97c1-1c6ea45be5b0/bcab04a8-91eb-490e-ac31-3d298d72b4f5_1570758841.jpeg",
        ],
    },
    {
        "slug": "demo-women-cotton-tshirt",
        "name": "تی‌شرت زنانه نخی H&M مدل 1270848006",
        "name_en": "H&M Women's Cotton T-Shirt 1270848006",
        "brand": "H&M",
        "category": "women-clothing",
        "price": 1990000,
        "old_price": None,
        "stock": 12,
        "description": (
            "تی‌شرت زنانه H&M با کد محصول 1270848006 از جرسی پنبه‌ای ۱۰۰٪ تهیه شده است. "
            "فرم Regular Fit، یقه گرد و آستین کوتاه دارد و برای استایل روزمره و لایه‌پوشی "
            "سبک مناسب است. رنگ این صفحه آبی روشن متمایل به خاکستری است."
        ),
        "highlights": "۱۰۰٪ پنبه، جرسی نرم، فرم Regular Fit، یقه گرد، آستین کوتاه",
        "review": (
            "این تی‌شرت از مجموعهٔ Basics برند H&M است. ترکیب اصلی پارچه طبق صفحهٔ "
            "محصول ۱۰۰٪ پنبه و وزن اعلام‌شده حدود ۱۲۵ گرم است. برش Regular Fit، "
            "یقه گرد و آستین کوتاه، طراحی ساده و قابل استفادهٔ روزانه ایجاد می‌کنند.\n\n"
            "به دلیل تفاوت الگوی سایزبندی و احتمال جمع‌شدن پارچه پس از شست‌وشو، "
            "راهنمای اندازه‌گیری همان محصول و دستور شست‌وشوی درج‌شده روی لیبل "
            "پیش از انتخاب سایز بررسی شود."
        ),
        "specs": [
            ("کد محصول", "1270848006"),
            ("جنس", "۱۰۰٪ پنبه"),
            ("نوع پارچه", "Cotton Jersey"),
            ("فرم", "Regular Fit"),
            ("یقه", "گرد با نوار باریک"),
            ("آستین", "کوتاه"),
            ("وزن اعلام‌شده", "حدود ۱۲۵ گرم"),
            ("رنگ مرجع", "آبی روشن متمایل به خاکستری"),
        ],
        "variants": [
            ("سایز", "S", 0),
            ("سایز", "M", 0),
            ("سایز", "L", 0),
            ("سایز", "XL", 0),
        ],
        "pages": [
            "https://www2.hm.com/en_us/productpage.1270848006.html",
            "https://www2.hm.com/en_in/productpage.1270848006.html",
        ],
        "image_urls": [
            "https://image.hm.com/assets/hm/5c/11/5c11687324e74485a3ad14ca1e85687212f8a1af.jpg?imwidth=2160",
        ],
    },
    {
        "slug": "demo-fit-me-foundation",
        "name": "کرم‌پودر Maybelline Fit Me Matte + Poreless شماره 128",
        "name_en": "Maybelline Fit Me Matte + Poreless Foundation 128 Warm Nude",
        "brand": "Maybelline New York",
        "category": "women-cosmetics",
        "price": 1450000,
        "old_price": None,
        "stock": 10,
        "description": (
            "کرم‌پودر Fit Me Matte + Poreless با جلوهٔ مات طبیعی و پوشش قابل لایه‌سازی "
            "برای پوست نرمال تا چرب معرفی شده است. شمارهٔ 128 با نام Warm Nude شناخته "
            "می‌شود؛ انتخاب رنگ نهایی باید با تناژ پوست و نور طبیعی انجام شود."
        ),
        "highlights": (
            "جلوه مات، فرمول بدون چربی، غیرکومدون‌زا، بدون عطر، "
            "پودرهای جاذب چربی برای کاهش ظاهر براقیت"
        ),
        "review": (
            "فرمول Fit Me Matte + Poreless برای ایجاد جلوهٔ مات و کاهش ظاهر براقیت "
            "طراحی شده است. Maybelline در صفحهٔ رسمی محصول به فرمول بدون چربی، "
            "غیرکومدون‌زا و بدون عطر اشاره می‌کند.\n\n"
            "شمارهٔ 128 با نام Warm Nude در طیف رنگی این محصول عرضه شده است. "
            "به دلیل تفاوت نور، نمایشگر و تناژ طبیعی پوست، مشاهدهٔ سواچ رنگ در نور "
            "طبیعی پیش از خرید توصیه می‌شود. پوشش و ماندگاری نهایی به نوع پوست و "
            "روش آماده‌سازی و تثبیت آرایش وابسته است."
        ),
        "specs": [
            ("نوع محصول", "کرم‌پودر مایع"),
            ("سری", "Fit Me Matte + Poreless"),
            ("شماره رنگ", "128 Warm Nude"),
            ("جلوه", "مات طبیعی"),
            ("فرمول", "بدون چربی، غیرکومدون‌زا و بدون عطر طبق معرفی سازنده"),
            ("کاربری پیشنهادی", "پوست نرمال تا چرب؛ انتخاب نهایی متناسب با پوست"),
        ],
        "variants": [
            ("رنگ", "128 Warm Nude", 0),
        ],
        "pages": [
            "https://www.maybelline.com/face-makeup/foundation-makeup/fit-me-matte-poreless-foundation",
            "https://www.whites.sa/en-sa/maybelline-fit-me-matte-poreless-foundation-glass-8/",
        ],
        "image_urls": [
            "https://www.maybelline.de/-/media/project/loreal/brand-sites/mny/emea/dach/foundation-finder/may_fitme_foundation_finder_shades_02.jpg?ch=1440&cw=1440&cx=0.25&cy=0.31&hash=7B28FE809713B787328BE4C39D53304D&rev=f9a9ec2371ee402b98dbaabe668beff8",
            "https://cd3c14-whites.akinoncloudcdn.com/products/2025/12/02/28151/b04019e4-9b0f-4b34-ad69-d7b34f9d0419_size3840x3840_cropCenter.jpg",
        ],
    },
    {
        "slug": "demo-sky-high-mascara",
        "name": "ریمل Maybelline Lash Sensational Sky High مشکی",
        "name_en": "Maybelline Lash Sensational Sky High Mascara 01 Black",
        "brand": "Maybelline New York",
        "category": "women-cosmetics",
        "price": 1590000,
        "old_price": None,
        "stock": 10,
        "description": (
            "ریمل Lash Sensational Sky High در رنگ مشکی برای بلندتر نشان‌دادن مژه‌ها "
            "و ایجاد جلوهٔ تفکیک‌شده معرفی شده است. مدل مرجع این صفحه 7.2 میلی‌لیتری "
            "و رنگ 01 Black است."
        ),
        "highlights": "رنگ 01 Black، حجم ۷٫۲ میلی‌لیتر، برس انعطاف‌پذیر، جلوهٔ بلندکننده",
        "review": (
            "Sky High از سری Lash Sensational برای تأکید بر طول و تفکیک مژه‌ها "
            "طراحی شده است. برس انعطاف‌پذیر محصول به پوشش مژه‌ها از ریشه تا نوک کمک "
            "می‌کند. نتیجهٔ نهایی به تعداد لایه‌ها، فرم مژه و روش استفاده وابسته است.\n\n"
            "مدل ثبت‌شده در این صفحه رنگ مشکی 01 و حجم ۷٫۲ میلی‌لیتر است. "
            "برای حفظ بهداشت محصول آرایشی چشم، از اشتراک‌گذاری ریمل خودداری کنید "
            "و در صورت تغییر بو، بافت یا تاریخ مصرف از آن استفاده نکنید."
        ),
        "specs": [
            ("نوع محصول", "ریمل بلندکننده مژه"),
            ("سری", "Lash Sensational Sky High"),
            ("رنگ", "01 Black"),
            ("حجم", "۷٫۲ میلی‌لیتر"),
            ("کاربری", "بلندکنندگی و تفکیک مژه‌ها"),
            ("ناحیه استفاده", "مژه؛ از تماس مستقیم با داخل چشم پرهیز شود"),
        ],
        "variants": [
            ("رنگ", "01 Black", 0),
        ],
        "pages": [
            "https://www.maybelline.com/eye-makeup/mascara/lash-sensational-sky-high-washable-mascara",
            "https://www.lookfantastic.ru/p/maybelline-lash-sensational-sky-high-mascara-01-black-7.2ml/12765086/",
        ],
        "image_urls": [
            "https://static.thcdn.com/productimg/original/12765086-3255256174959631.jpg",
            "https://cdn11.bigcommerce.com/s-vflxca1joe/images/stencil/1280x1280/products/18576/429326/103418-001_01__18466.1753246521.png?c=1",
            "https://media.superdrug.com/medias/sys_master/prd-images/h21/h36/12778053533726/prd-front-801812_600x600/prd-front-801812-600x600.jpg",
        ],
    },
    {
        "slug": "demo-chanel-chance-eau-tendre",
        "name": "عطر زنانه Chanel Chance Eau Tendre EDT حجم ۱۰۰ میلی‌لیتر",
        "name_en": "Chanel Chance Eau Tendre Eau de Toilette 100ml",
        "brand": "Chanel",
        "category": "perfume-women",
        "price": 38400000,
        "old_price": None,
        "stock": 5,
        "description": (
            "Chance Eau Tendre از خانوادهٔ رایحه‌های گلی-میوه‌ای شنل است. نسخهٔ "
            "ثبت‌شده در این صفحه Eau de Toilette با حجم ۱۰۰ میلی‌لیتر است؛ آن را "
            "با نسخهٔ Eau de Parfum یکسان در نظر نگیرید."
        ),
        "highlights": "غلظت Eau de Toilette، حجم ۱۰۰ میلی‌لیتر، گروه بویایی گلی-میوه‌ای",
        "review": (
            "Chance Eau Tendre یک رایحهٔ گلی-میوه‌ای با فضای لطیف و شاداب است. "
            "در توصیف نسخهٔ Eau Tendre، نت‌های میوه‌ای در کنار گل‌هایی مانند یاس "
            "و رز و پایه‌ای نرم و مُشکی قرار می‌گیرند.\n\n"
            "این محصول در این صفحه با غلظت Eau de Toilette و حجم ۱۰۰ میلی‌لیتر "
            "ثبت شده است. نسخهٔ EDT و EDP از نظر غلظت و ساختار رایحه متفاوت‌اند؛ "
            "بنابراین هنگام خرید به عبارت درج‌شده روی جعبه و شیشه توجه شود."
        ),
        "specs": [
            ("برند", "CHANEL"),
            ("نام", "Chance Eau Tendre"),
            ("غلظت", "Eau de Toilette (EDT)"),
            ("حجم", "۱۰۰ میلی‌لیتر"),
            ("گروه بویایی", "گلی-میوه‌ای"),
            ("ساختار رایحه", "میوه‌های تازه، یاس و رز با پایهٔ مُشکی"),
            ("جنسیت عرضه", "زنانه"),
        ],
        "variants": [
            ("حجم", "۱۰۰ میلی‌لیتر EDT", 0),
        ],
        "pages": [
            "https://www.douglas.pl/pl/p/3000034541",
            "https://elissa-perfume.com/product/%D8%B4%D9%86%D9%84-%DA%86%D9%86%D8%B3-%D8%A7%D9%88-%D8%AA%D9%86%D8%AF%D8%B1-chanel-chance-eau-tendre-2/",
        ],
        "image_urls": [
            "https://media.douglas.pl/medias/gWaHGk843601-0-global.jpg?context=bWFzdGVyfGltYWdlc3w2Mzg5N3xpbWFnZS9qcGVnfGFESmlMMmhtTXk4M09UUTRNVEE0TVRNNU56STNPQzluVjJGSVIyczRORE0yTURGZk1GOW5iRzlpWVd3dWFuQm58MjU3OTcwZjAzMWI3ZWZhNDRiZGE1YjJlNzE2OWJlZjliMzFiYWIzMTJkNDM2OWEwMTIwYWNkZWU0MGRjODgwYQ&grid=true",
            "https://pinoyfragrance.com/cdn/shop/products/N_c-hoa-n_-CHANEL-Chance-Eau-Tendre-EDT-100ml-1.jpg?v=1618656873",
            "https://veronnaperfumeria.com/cdn/shop/files/WhatsApp_Image_2026-03-19_at_10.39.34_PM_1080x.jpg?v=1773978157",
        ],
    },
    {
        "slug": "demo-dior-sauvage-edp",
        "name": "عطر مردانه Dior Sauvage Eau de Parfum حجم ۱۰۰ میلی‌لیتر",
        "name_en": "Dior Sauvage Eau de Parfum 100ml",
        "brand": "Dior",
        "category": "perfume-men",
        "price": 36300000,
        "old_price": None,
        "stock": 5,
        "description": (
            "Sauvage Eau de Parfum نسخهٔ ادوپرفیوم از مجموعهٔ Sauvage دیور است. "
            "در ساختار رایحهٔ آن ترنج تازه، ادویه‌ها و وانیل در کنار پایه‌ای "
            "چوبی-کهربایی حضور دارند. حجم این محصول ۱۰۰ میلی‌لیتر است."
        ),
        "highlights": "غلظت Eau de Parfum، حجم ۱۰۰ میلی‌لیتر، رایحهٔ مرکباتی و چوبی-کهربایی",
        "review": (
            "Dior Sauvage Eau de Parfum برداشتی گرم‌تر و عمیق‌تر از فضای مرکباتی "
            "و ادویه‌ای مجموعهٔ Sauvage است. ترنج در شروع رایحه برجسته است و "
            "در ادامه، نت‌های ادویه‌ای و وانیلی در کنار پایهٔ چوبی-کهربایی قرار می‌گیرند.\n\n"
            "این محصول با غلظت Eau de Parfum ثبت شده و نباید با نسخهٔ Eau de Toilette "
            "یا Parfum اشتباه گرفته شود. برای اطمینان از اصالت، کد بچ، بسته‌بندی "
            "و تأمین‌کنندهٔ معتبر هنگام عرضهٔ واقعی بررسی شوند."
        ),
        "specs": [
            ("برند", "DIOR"),
            ("نام", "Sauvage"),
            ("غلظت", "Eau de Parfum (EDP)"),
            ("حجم", "۱۰۰ میلی‌لیتر"),
            ("گروه رایحه", "مرکباتی، ادویه‌ای و چوبی-کهربایی"),
            ("نت شاخص", "ترنج و وانیل"),
            ("جنسیت عرضه", "مردانه"),
        ],
        "variants": [
            ("حجم", "۱۰۰ میلی‌لیتر EDP", 0),
        ],
        "pages": [
            "https://www.dior.com/en_int/beauty/products/sauvage-eau-de-parfum-Y0785220.html",
            "https://www.perfumesk.ir/product/dior-savage-eau-de-parfum/",
        ],
        "image_urls": [
            "https://www.plaza.com.mv/cdn/shop/files/SauvageEDP_1024x1024.jpg?v=1762621037",
            "https://wearstf.com/cdn/shop/files/SAUVAGEEDP100ML_1024x1024_65805b81-453b-491b-b0d3-47fb7460828c.webp?v=1698097187",
        ],
    },
    {
        "slug": "demo-grand-court-women",
        "name": "کفش زنانه adidas Grand Court 2.0",
        "name_en": "adidas Grand Court 2.0 Women's Shoes GW9213",
        "brand": "adidas",
        "category": "shoes-women",
        "price": 8900000,
        "old_price": None,
        "stock": 8,
        "description": (
            "Grand Court 2.0 با الهام از کفش‌های تنیس کلاسیک طراحی شده است. "
            "رویهٔ مصنوعی، بند بسته‌شونده، آستر پارچه‌ای و زیرهٔ لاستیکی، "
            "این مدل را برای استفادهٔ روزمره و استایل کژوال مناسب می‌کنند."
        ),
        "highlights": "رویه مصنوعی، بنددار، آستر پارچه‌ای، کفی Cloudfoam Comfort، زیره لاستیکی",
        "review": (
            "adidas Grand Court 2.0 یک کفش روزمره با ظاهر الهام‌گرفته از مدل‌های "
            "تنیس کلاسیک است. رویهٔ مصنوعی و سه خط شناخته‌شدهٔ برند، ظاهر مینیمال "
            "و قابل ست‌کردن ایجاد می‌کنند.\n\n"
            "کفی Cloudfoam Comfort برای نرمی زیر پا و آستر پارچه‌ای برای راحتی "
            "در استفادهٔ روزمره در توضیحات محصول ذکر شده‌اند. زیرهٔ لاستیکی "
            "تماس مناسبی با سطوح شهری فراهم می‌کند. برای انتخاب سایز، جدول اندازه "
            "و طول پای درج‌شده توسط فروشنده بررسی شود."
        ),
        "specs": [
            ("مدل مرجع", "GW9213"),
            ("رویه", "مصنوعی"),
            ("آستر", "پارچه‌ای"),
            ("بستن کفش", "بندی"),
            ("کفی", "Cloudfoam Comfort"),
            ("زیره", "لاستیکی"),
            ("کاربری", "روزمره و استایل کژوال"),
        ],
        "variants": [
            ("سایز", "۳۷", 0),
            ("سایز", "۳۸", 0),
            ("سایز", "۳۹", 0),
            ("سایز", "۴۰", 0),
        ],
        "pages": [
            "https://www.cosmossport.gr/en/458962-adidas-grand-court-20.html",
            "https://www.adidas.com/us/grand_court",
        ],
        "image_urls": [
            "https://www.cosmossport.gr/3096106-product_large/adidas-grand-court-20.jpg",
            "https://image-cdn-flare.qdm.cloud/q652eaf60aecbe/image/data/2024/07/26/29425361c89394515a26de41a0681f84.jpg",
        ],
    },
    {
        "slug": "demo-runfalcon-men",
        "name": "کفش مردانه adidas Runfalcon 5",
        "name_en": "adidas Runfalcon 5 Men's Running Shoes",
        "brand": "adidas",
        "category": "shoes-men",
        "price": 7900000,
        "old_price": None,
        "stock": 8,
        "description": (
            "Runfalcon 5 کفشی برای دویدن سبک و استفادهٔ روزمره است. رویهٔ مش، "
            "میان‌زیرهٔ Cloudfoam و زیرهٔ Adiwear در کنار یقه و زبانهٔ پددار، "
            "ترکیبی برای راحتی روزانه فراهم می‌کنند."
        ),
        "highlights": "رویه مش، میان‌زیره Cloudfoam، کفی OrthoLite، زیره Adiwear، بنددار",
        "review": (
            "adidas Runfalcon 5 برای تمرین‌های سبک، پیاده‌روی و استفادهٔ روزانه "
            "طراحی شده است. رویهٔ مش به گردش هوا کمک می‌کند و میان‌زیرهٔ Cloudfoam "
            "برای جذب ضربه و احساس نرم‌تر زیر پا به کار رفته است.\n\n"
            "کفی OrthoLite، یقه و زبانهٔ پددار و زیرهٔ Adiwear از اجزای اعلام‌شده "
            "در مشخصات محصول هستند. این مدل را نباید با کفش‌های تخصصی مسابقه یا "
            "دویدن حرفه‌ای جاده‌ای یکسان دانست."
        ),
        "specs": [
            ("رویه", "مش تنفس‌پذیر"),
            ("میان‌زیره", "Cloudfoam"),
            ("کفی", "OrthoLite"),
            ("زیره", "Adiwear"),
            ("بستن کفش", "بندی"),
            ("کاربری", "دویدن سبک، پیاده‌روی و روزمره"),
        ],
        "variants": [
            ("سایز", "۴۰", 0),
            ("سایز", "۴۱", 0),
            ("سایز", "۴۲", 0),
            ("سایز", "۴۳", 0),
            ("سایز", "۴۴", 0),
        ],
        "pages": [
            "https://en-sa.sssports.com/adidas-mens-runfalcon-5-running-shoes/4067889145723.html",
        ],
        "image_urls": [
            "https://en-sa.sssports.com/dw/image/v2/BDVB_PRD/on/demandware.static/-/Sites-akeneo-master-catalog/default/dwe77aebad/sss/SSS2/A/D/I/E/8/SSS2_ADIE8812_4067889145778_1.jpg",
            "https://media.centrepointstores.com/i/centrepoint/IE8809BLACK-IE8809-SMAW24240624_02-2100.jpg",
        ],
    },
    {
        "slug": "demo-anker-a1384-powerbank",
        "name": "پاوربانک Anker A1384 ظرفیت ۲۰۰۰۰ میلی‌آمپرساعت",
        "name_en": "Anker Power Bank 20K 30W A1384",
        "brand": "Anker",
        "category": "accessories",
        "price": 5299000,
        "old_price": None,
        "stock": 10,
        "description": (
            "پاوربانک Anker A1384 با ظرفیت اسمی ۲۰۰۰۰ میلی‌آمپرساعت و خروجی USB-C "
            "تا ۳۰ وات برای شارژ گوشی و برخی تبلت‌ها طراحی شده است. دو درگاه USB-C "
            "و یک درگاه USB-A دارد؛ توان خروجی کل در حالت چنددرگاهی محدود می‌شود."
        ),
        "highlights": "ظرفیت ۲۰۰۰۰mAh، خروجی USB-C تا ۳۰W، خروجی USB-A تا ۱۸W، نمایشگر درصد شارژ",
        "review": (
            "Anker A1384 یک پاوربانک ۲۰۰۰۰ میلی‌آمپرساعتی با پشتیبانی از شارژ "
            "USB-C تا ۳۰ وات است. طبق راهنمای سازنده، هر یک از درگاه‌های USB-C "
            "می‌تواند در حالت تک‌خروجی تا ۳۰ وات ارائه کند و USB-A تا ۱۸ وات "
            "پشتیبانی می‌کند.\n\n"
            "این مدل دو درگاه USB-C و یک USB-A دارد و نمایشگر درصد شارژ برای "
            "بررسی انرژی باقی‌مانده در نظر گرفته شده است. توان مجموع خروجی‌ها "
            "هنگام استفادهٔ هم‌زمان از چند درگاه کمتر از بیشینهٔ تک‌درگاه خواهد بود."
        ),
        "specs": [
            ("مدل", "A1384"),
            ("ظرفیت اسمی سلول", "۲۰۰۰۰ میلی‌آمپرساعت"),
            ("ورودی USB-C", "حداکثر ۳۰ وات"),
            ("خروجی USB-C", "حداکثر ۳۰ وات برای هر درگاه به‌صورت جداگانه"),
            ("خروجی USB-A", "حداکثر ۱۸ وات"),
            ("درگاه‌ها", "۲ عدد USB-C و ۱ عدد USB-A"),
            ("نمایشگر", "نمایش درصد شارژ باقی‌مانده"),
        ],
        "variants": [
            ("ظرفیت", "۲۰۰۰۰ میلی‌آمپرساعت / ۳۰ وات", 0),
        ],
        "pages": [
            "https://support.anker.com/s/article-description?urlName=Anker-Power-Bank-20000mAh-30W-User-Guide-A1384",
            "https://faceit.ir/p/%D9%BE%D8%A7%D9%88%D8%B1%D8%A8%D8%A7%D9%86%DA%A9-30-%D9%88%D8%A7%D8%AA-anker-%D9%85%D8%AF%D9%84-a1384-%D8%B8%D8%B1%D9%81%DB%8C%D8%AA-20000-%D9%85%DB%8C%D9%84%DB%8C-%D8%A2%D9%85%D9%BE%D8%B1-%D8%B3%D8%A7%D8%B9%D8%AA-%D8%A8%D9%87-%D9%87%D9%85%D8%B1%D8%A7%D9%87-%DA%A9%D8%A7%D8%A8%D9%84-usb-c/",
        ],
        "image_urls": [
            "https://down-sg.img.susercontent.com/file/sg-11134207-824gn-mfnfqx6t1y4p23",
            "https://down-vn.img.susercontent.com/file/sg-11134201-7rdyt-lzik5rnzpqk7f3",
        ],
    },
    {
        "slug": "demo-casio-mtp-v006d",
        "name": "ساعت مردانه Casio MTP-V006D-7B",
        "name_en": "Casio MTP-V006D-7B Men's Analog Watch",
        "brand": "Casio",
        "category": "watches-men",
        "price": 10700000,
        "old_price": None,
        "stock": 6,
        "description": (
            "MTP-V006D-7B ساعتی آنالوگ با صفحهٔ روشن، اعداد رومی، نمایش روز هفته "
            "و تاریخ و بند استیل است. این مدل مقاومت معمول در برابر آب دارد؛ "
            "به معنی مناسب‌بودن برای شنا یا غواصی نیست."
        ),
        "highlights": "موتور کوارتز، نمایش روز و تاریخ، بند استیل، شیشه معدنی، باتری حدود ۲ سال",
        "review": (
            "Casio MTP-V006D-7B با طراحی کلاسیک، صفحهٔ روشن و نشانگرهای رومی "
            "برای استفادهٔ روزمره و رسمی مناسب است. نمایش روز هفته و تاریخ، "
            "خوانایی اطلاعات را افزایش می‌دهد.\n\n"
            "بدنه و بند از استیل ساخته شده‌اند و شیشه از نوع معدنی است. ساعت با "
            "باتری SR626SW کار می‌کند و عمر تقریبی باتری حدود دو سال اعلام شده است. "
            "درجهٔ مقاومت این مدل برای استفادهٔ معمول روزانه است و نباید آن را "
            "برای شنا یا تماس طولانی با آب به کار برد."
        ),
        "specs": [
            ("مدل", "MTP-V006D-7B"),
            ("نوع موتور", "کوارتز آنالوگ"),
            ("نمایش", "ساعت، دقیقه، ثانیه، روز هفته و تاریخ"),
            ("ابعاد قاب", "۴۵٫۱ × ۳۸ × ۹٫۴ میلی‌متر"),
            ("وزن", "۹۳ گرم"),
            ("جنس بند", "استیل ضدزنگ"),
            ("شیشه", "Mineral Glass"),
            ("باتری", "SR626SW، عمر تقریبی ۲ سال"),
            ("مقاومت در برابر آب", "Water Resistant؛ برای شنا مناسب اعلام نشده است"),
        ],
        "variants": [
            ("رنگ صفحه", "نقره‌ای", 0),
        ],
        "pages": [
            "https://www.casio.com/intl/watches/casio/product.MTP-V006D-7B/",
            "https://diamondwatchgallery.ir/product/casio-mtp-v006d-7b/",
            "https://jahedwatch.ir/casio/eco-sport/mtp-v006d-7b/",
        ],
        "image_urls": [
            "https://i.ebayimg.com/00/s/MTYwMFgxNjAw/z/nZ0AAOSwQTVmJhMO/%24_57.JPG?set_id=880000500F",
            "https://i.ebayimg.com/images/g/R6wAAOSwlB1j21iJ/s-l400.png",
        ],
    },
    {
        "slug": "demo-casio-ltp-v007d",
        "name": "ساعت زنانه Casio LTP-V007D-7B",
        "name_en": "Casio LTP-V007D-7B Women's Analog Watch",
        "brand": "Casio",
        "category": "watches-women",
        "price": 8500000,
        "old_price": None,
        "stock": 6,
        "description": (
            "LTP-V007D-7B ساعت زنانهٔ آنالوگ با قاب مستطیلی، صفحهٔ روشن و بند "
            "استیل است. ضخامت کم و طراحی مینیمال آن برای استایل روزمره و رسمی "
            "مناسب است. مقاومت آن در برابر آب از نوع معمول روزانه است."
        ),
        "highlights": "قاب ۳۱×۲۲ میلی‌متر، بند استیل، شیشه معدنی، باتری حدود ۳ سال، نمایش آنالوگ",
        "review": (
            "Casio LTP-V007D-7B با قاب مستطیلی و بند استیل، ظاهری ساده و کلاسیک "
            "دارد. نمایش زمان با سه عقربه انجام می‌شود و شیشهٔ معدنی روی صفحه "
            "قرار گرفته است.\n\n"
            "ابعاد قاب ۳۱×۲۲×۷٫۵ میلی‌متر و وزن حدود ۵۱ گرم است. باتری SR626SW "
            "با عمر تقریبی سه سال در مشخصات سازنده ذکر شده است. عبارت Water Resistant "
            "به معنی ضدآب‌بودن برای شنا یا غواصی نیست."
        ),
        "specs": [
            ("مدل", "LTP-V007D-7B"),
            ("نوع موتور", "کوارتز آنالوگ سه عقربه"),
            ("ابعاد قاب", "۳۱ × ۲۲ × ۷٫۵ میلی‌متر"),
            ("وزن", "۵۱ گرم"),
            ("جنس بند", "استیل ضدزنگ"),
            ("جنس قاب", "فلز با روکش کروم"),
            ("شیشه", "Mineral Glass"),
            ("باتری", "SR626SW، عمر تقریبی ۳ سال"),
            ("مقاومت در برابر آب", "Water Resistant؛ برای شنا مناسب اعلام نشده است"),
        ],
        "variants": [
            ("رنگ بند", "نقره‌ای", 0),
        ],
        "pages": [
            "https://www.casio.com/intl/watches/casio/product.LTP-V007D-7B/",
            "https://www.casio.com/in/watches/casio/product.LTP-V007D-7B/",
        ],
        "image_urls": [
            "https://www.casio.com/content/dam/casio/product-info/locales/in/en/timepiece/product/watch/L/LT/LTP/LTP-V007D-7B/assets/LTP-V007D-7B_model-free_wrist02.jpg.transform/main-visual-sp/image.jpg",
            "https://tshop.r10s.jp/gryps-/cabinet/analog/ltp-v007d-7b_1.jpg?fitin=720%3A720",
        ],
    },
    {
        "slug": "demo-logitech-m185",
        "name": "ماوس بی‌سیم Logitech M185",
        "name_en": "Logitech M185 Wireless Mouse",
        "brand": "Logitech",
        "category": "accessories",
        "price": 1200000,
        "old_price": None,
        "stock": 12,
        "description": (
            "Logitech M185 یک ماوس بی‌سیم ساده با گیرندهٔ USB نانو و اتصال رادیویی "
            "۲٫۴ گیگاهرتز است. حسگر اپتیکال ۱۰۰۰ DPI، کلید روشن/خاموش و باتری قلمی "
            "AA برای استفادهٔ اداری و روزمره در نظر گرفته شده‌اند."
        ),
        "highlights": "اتصال بی‌سیم ۲٫۴GHz، گیرنده USB Nano، حسگر ۱۰۰۰ DPI، باتری AA تا ۱۲ ماه",
        "review": (
            "M185 برای کارهای اداری، وب‌گردی و استفادهٔ روزمره طراحی شده است. "
            "اتصال آن از طریق گیرندهٔ USB نانو انجام می‌شود و نیازی به جفت‌سازی "
            "بلوتوث ندارد.\n\n"
            "حسگر اپتیکال با دقت اسمی ۱۰۰۰ DPI، چرخ اسکرول و کلید روشن/خاموش "
            "از امکانات اصلی این مدل هستند. عمر باتری تا ۱۲ ماه در شرایط استفادهٔ "
            "آزمایشگاهی اعلام شده و با نوع باتری و الگوی استفاده تغییر می‌کند."
        ),
        "specs": [
            ("مدل", "M185"),
            ("نوع اتصال", "بی‌سیم ۲٫۴ گیگاهرتز از طریق گیرنده USB Nano"),
            ("حسگر", "Logitech Advanced Optical Tracking"),
            ("دقت", "حدود ۱۰۰۰ DPI"),
            ("باتری", "یک عدد AA"),
            ("عمر باتری", "تا ۱۲ ماه؛ وابسته به شرایط استفاده"),
            ("ابعاد", "حدود ۹۹٫۵ × ۶۰ × ۳۸٫۶ میلی‌متر"),
            ("وزن", "حدود ۷۲ گرم بدون گیرنده"),
        ],
        "variants": [
            ("رنگ", "مشکی", 0),
        ],
        "pages": [
            "https://support.logi.com/hc/en-150/articles/360023302074-M185-Technical-Specifications",
            "https://www.bestbuy.ca/en-ca/product/logitech-m185-wireless-optical-mouse-black/14577508",
            "https://www.logitech.com/en-us/products/mice/m185-wireless-mouse.html",
        ],
        "image_urls": [
            "https://multimedia.bbycastatic.ca/multimedia/products/1500x1500/145/14577/14577508_4.jpg",
            "https://cdn.grupoelcorteingles.es/SGFM/dctm/MEDIA03/201701/10/00128635601603____2__1200x1200.jpg",
        ],
    },
]

CATEGORY_COVERS = {
    "women-clothing": "demo-women-cotton-tshirt",
    "women-cosmetics": "demo-fit-me-foundation",
    "perfume": "demo-chanel-chance-eau-tendre",
    "perfume-men": "demo-dior-sauvage-edp",
    "perfume-women": "demo-chanel-chance-eau-tendre",
    "shoes": "demo-grand-court-women",
    "shoes-men": "demo-runfalcon-men",
    "shoes-women": "demo-grand-court-women",
    "watches": "demo-casio-mtp-v006d",
    "watches-men": "demo-casio-mtp-v006d",
    "watches-women": "demo-casio-ltp-v007d",
}

REVIEW_TEXTS = [
    (
        5,
        "🧪 نظر نمونه نمایشی: توضیحات و مشخصات فنی محصول مرتب و قابل مقایسه ثبت شده است. "
        "این متن صرفاً برای نمایش بخش نظرات در نسخهٔ Demo است و تجربهٔ خرید واقعی نیست. ✨"
    ),
    (
        4,
        "🧪 نظر نمونه نمایشی: تصاویر گالری، ویژگی‌های کلیدی و جزئیات انتخاب محصول "
        "برای بررسی اولیه مناسب‌اند. این نظر ساختگیِ نمایشی است و نباید به‌عنوان "
        "بازخورد خریدار واقعی برداشت شود. 👍"
    ),
]

class PageImages(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"img", "source"}:
            alt = attrs.get("alt", "") or attrs.get("title", "")
            for key in (
                "src", "data-src", "data-original", "data-lazy-src",
                "data-zoom-image", "data-image", "data-large-image",
            ):
                value = attrs.get(key)
                if value:
                    self.items.append((value, alt))
            for key in ("srcset", "data-srcset"):
                value = attrs.get(key)
                if value:
                    for entry in value.split(","):
                        url = entry.strip().split(" ")[0]
                        if url:
                            self.items.append((url, alt))


IMAGE_MATCH_TERMS = {
    "demo-phone-a": ("galaxy a56", "galaxy-a56", "sm-a566", "a56-5g"),
    "demo-phone-b": ("redmi note 14 pro", "redmi-note-14-pro", "note-14-pro-5g"),
    "demo-tablet": ("galaxy tab a9", "galaxy-tab-a9", "tab a9+", "tab-a9-plus", "sm-x210"),
    "demo-laptop-a": ("vivobook 15", "vivobook-15", "x1504", "x1504va"),
    "demo-laptop-b": ("ideapad slim 3", "ideapad-slim-3", "15amn8", "82xq"),
    "demo-router": ("archer c6", "archer-c6"),
    "demo-switch": ("tl-sg108", "tl sg108"),
    "demo-headphone": ("wh-ch520", "wh ch520", "whch520"),
    "demo-women-cotton-tshirt": ("1270848006", "cotton t-shirt", "cotton-t-shirt", "light dusty blue"),
    "demo-fit-me-foundation": ("fit me", "fit-me", "maybelline fit"),
    "demo-sky-high-mascara": ("sky high", "sky-high", "lash sensational"),
    "demo-chanel-chance-eau-tendre": ("chance eau tendre", "chance-eau-tendre"),
    "demo-dior-sauvage-edp": ("sauvage", "dior sauvage"),
    "demo-grand-court-women": ("grand court", "grand-court", "gw9213"),
    "demo-runfalcon-men": ("runfalcon", "runfalcon 5", "ie8809"),
    "demo-anker-a1384-powerbank": ("a1384", "anker power bank", "anker 20k"),
    "demo-casio-mtp-v006d": ("mtp-v006d", "mtp v006d"),
    "demo-casio-ltp-v007d": ("ltp-v007d", "ltp v007d"),
    "demo-logitech-m185": ("m185", "logitech m185"),
}


def candidate_matches_product(item, url, alt):
    haystack = f"{url} {alt}".lower()
    return any(term in haystack for term in IMAGE_MATCH_TERMS.get(item["slug"], ()))



def get_bytes(url, limit=MAX_IMAGE_BYTES):
    req = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    })
    with urlopen(req, timeout=TIMEOUT) as response:
        content_type = (response.headers.get("Content-Type") or "").lower()
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("image exceeds size limit")
        return data, content_type


def clean_url(value, base_url):
    value = html.unescape(value or "").replace("\\/", "/").strip()
    if not value or value.startswith(("data:", "blob:", "javascript:")):
        return None
    value = urljoin(base_url, value)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return None
    return urlunparse(parsed._replace(fragment=""))


def page_candidates(item, page_url):
    try:
        req = Request(page_url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
        })
        with urlopen(req, timeout=TIMEOUT) as response:
            raw = response.read(8 * 1024 * 1024)
        source = raw.decode("utf-8", "ignore")
        parser = PageImages()
        parser.feed(source)
        out = []
        for raw_url, alt in parser.items:
            url = clean_url(raw_url, page_url)
            # Reject generic OpenGraph art, site banners, unrelated recommendations
            # and image URLs found inside scripts. Page assets must identify this model.
            if url and candidate_matches_product(item, url, alt):
                out.append((url, alt))
        return out
    except Exception as exc:
        print("PAGE_SKIP", page_url, type(exc).__name__, flush=True)
        return []


def normalize_image(data):
    try:
        with Image.open(BytesIO(data)) as source:
            source.verify()
        with Image.open(BytesIO(data)) as source:
            width, height = source.size
            if min(width, height) < 220 or max(width, height) < 450:
                return None
            if width / max(height, 1) > 2.7:
                return None
            image = source.copy()
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGB")
            image.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="WEBP", quality=88, method=6)
            return output.getvalue(), image.size
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def gather_images(item):
    candidates = []
    seen_urls = set()
    for url in item.get("image_urls", []):
        cleaned = clean_url(url, url)
        if cleaned and cleaned not in seen_urls:
            seen_urls.add(cleaned)
            candidates.append((cleaned, item["name"]))

    for page in item.get("pages", []):
        for url, alt in page_candidates(item, page):
            if url not in seen_urls:
                seen_urls.add(url)
                candidates.append((url, alt))

    selected = []
    seen_hashes = set()
    rejected = 0
    for url, alt in candidates:
        if len(selected) >= GALLERY_TARGET:
            break
        test = (url + " " + alt).lower()
        if any(word in test for word in (
            "logo", "icon", "sprite", "avatar", "flag", "payment",
            "badge", "placeholder", "social", "favicon", "qr-code",
        )):
            continue
        try:
            data, content_type = get_bytes(url)
            if content_type and not content_type.startswith("image/"):
                # A few CDN endpoints report octet-stream; Pillow verifies the bytes.
                if "octet-stream" not in content_type:
                    continue
            normalized = normalize_image(data)
            if not normalized:
                continue
            webp, dimensions = normalized
            digest = hashlib.sha256(webp).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            selected.append({
                "bytes": webp,
                "url": url,
                "alt": alt or item["name"],
                "sha": digest,
                "dimensions": dimensions,
            })
            print(
                "IMAGE_OK", item["slug"], len(selected), dimensions,
                url[:150], flush=True
            )
        except Exception:
            rejected += 1
            continue

    return selected


def set_variants(product, definitions):
    current = list(product.variants.order_by("pk"))
    if len(current) > len(definitions):
        extras = current[len(definitions):]
        for variant in extras:
            if variant.cart_items.exists():
                raise RuntimeError(
                    f"Variant {variant.pk} of {product.slug} is used by a cart; "
                    "refusing to delete or alter it."
                )
        for variant in extras:
            variant.delete()

    current = list(product.variants.order_by("pk"))
    for index, (name, value, adjustment) in enumerate(definitions):
        if index < len(current):
            variant = current[index]
            changed = (
                variant.name != name
                or variant.value != value
                or variant.price_adjustment != adjustment
            )
            if changed and variant.cart_items.exists():
                raise RuntimeError(
                    f"Variant {variant.pk} of {product.slug} is used by a cart; "
                    "refusing to alter it."
                )
        else:
            variant = ProductVariant(product=product)

        variant.name = name
        variant.value = value
        variant.price_adjustment = adjustment
        variant.full_clean()
        variant.save()


def apply_reviews(products):
    User = get_user_model()
    demo_users = []
    for number in range(1, 4):
        username = f"demo_buyer_{number:02d}"
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "is_active": True,
                "is_staff": False,
                "is_superuser": False,
            },
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        demo_users.append(user)

    for index, product in enumerate(products):
        for offset, (rating, comment) in enumerate(REVIEW_TEXTS):
            user = demo_users[(index + offset) % len(demo_users)]
            Review.objects.update_or_create(
                product=product,
                user=user,
                defaults={
                    "rating": rating,
                    "comment": comment,
                    "is_approved": True,
                },
            )


def run():
    print("DEMO_CATALOG: image download/preflight starts", flush=True)
    categories = {
        slug: Category.objects.get(slug=slug)
        for slug in {
            item["category"] for item in PRODUCTS
        } | set(CATEGORY_COVERS.keys())
    }

    prepared = {}
    missing = []
    for item in PRODUCTS:
        images = gather_images(item)
        prepared[item["slug"]] = images
        if len(images) < GALLERY_TARGET:
            missing.append((item["slug"], len(images)))
    if missing:
        print("IMAGE_GALLERY_SHORTFALL:", json.dumps(missing, ensure_ascii=False), flush=True)
        print(
            "Continuing with the verified assets available; missing gallery slots "
            "will remain empty and can be topped up on a later run.",
            flush=True,
        )

    products_by_slug = {}
    with transaction.atomic():
        for item in PRODUCTS:
            category = categories[item["category"]]
            product, created = Product.objects.get_or_create(
                slug=item["slug"],
                defaults={
                    "name": item["name"],
                    "category": category,
                    "price": item["price"],
                    "stock": item["stock"],
                },
            )
            product.name = item["name"]
            product.name_en = item["name_en"]
            product.brand = item["brand"]
            product.category = category
            product.description = item["description"] + (
                "\\n\\nقیمت و موجودی نسخهٔ Demo نمونهٔ نمایشی هستند و قیمت روز بازار یا "
                "تعهد فروش واقعی محسوب نمی‌شوند."
            )
            product.review = item["review"]
            product.highlights = item["highlights"]
            # Never overwrite prices, discounts, inventory, guarantee or delivery
            # information on products that already existed in the Demo database.
            # New items use an explicitly labeled Demo placeholder price only.
            if created:
                product.price = item["price"]
                product.old_price = None
                product.stock = item["stock"]
                product.guarantee = "اطلاعات گارانتی نمونه؛ پیش از فروش واقعی تکمیل شود"
                product.delivery_info = (
                    "قیمت و موجودی این کالا صرفاً نمونهٔ نمایشی Demo است؛ "
                    "قیمت بازار و موجودی واقعی تأیید نشده است."
                )
            product.is_active = True
            product.save()
            products_by_slug[item["slug"]] = product

            # Keep existing uploaded main images. Fill only when no main image exists.
            images = prepared[item["slug"]]
            if not product.image and images:
                product.image.save(
                    f"{item['slug']}-cover.webp",
                    ContentFile(images[0]["bytes"]),
                    save=True,
                )

            # Keep all existing gallery images; top up to six unique downloaded images.
            existing_count = product.images.count()
            needed = max(0, GALLERY_TARGET - existing_count)
            for image_index, image_data in enumerate(images[:needed], start=existing_count + 1):
                product_image = ProductImage(
                    product=product,
                    alt_text=f"{item['name']} - نمای {image_index}",
                )
                product_image.image.save(
                    f"{item['slug']}-{image_index:02d}.webp",
                    ContentFile(image_data["bytes"]),
                    save=True,
                )

            # Merge verified model specifications without deleting existing data.
            for title, value in item["specs"]:
                ProductSpecification.objects.update_or_create(
                    product=product,
                    title=title,
                    defaults={"value": value},
                )
            set_variants(product, item["variants"])

        # Fill only missing category covers. Existing four category images stay untouched.
        for category_slug, product_slug in CATEGORY_COVERS.items():
            category = categories[category_slug]
            if category.image:
                continue
            product = products_by_slug[product_slug]
            if product.image:
                with product.image.open("rb") as source:
                    cover_bytes = source.read()
                category.image.save(
                    f"{category_slug}-cover.webp",
                    ContentFile(cover_bytes),
                    save=True,
                )
            else:
                print("CATEGORY_COVER_PENDING:", category_slug, flush=True)

        apply_reviews(list(products_by_slug.values()))

    print("DEMO_CATALOG_APPLY: success", flush=True)
    print("PRODUCTS_PROCESSED:", len(products_by_slug), flush=True)
    print("TOTAL_PRODUCTS:", Product.objects.count(), flush=True)
    print("TOTAL_CATEGORIES:", Category.objects.count(), flush=True)
    gallery_summary = [
        (p.slug, p.images.count())
        for p in Product.objects.order_by("slug")
    ]
    print(
        "PRODUCTS_WITH_AT_LEAST_6_GALLERY_IMAGES:",
        sum(1 for slug, count in gallery_summary if count >= GALLERY_TARGET),
        flush=True,
    )
    print(
        "GALLERY_COUNTS:",
        json.dumps(gallery_summary, ensure_ascii=False),
        flush=True,
    )
    print("SAMPLE_REVIEWS:", Review.objects.filter(
        comment__startswith="🧪 نظر نمونه نمایشی"
    ).count(), flush=True)
    print("CATEGORY_COVERS_FILLED:", len(CATEGORY_COVERS), flush=True)


run()
