from django import template


register = template.Library()

DEMO_PRODUCT_ASSETS = {
    "nova-x5-phone": "images/demo/products/smartphone.svg",
    "soundpods-pro": "images/demo/products/headphones.svg",
    "fittime-smartwatch": "images/demo/products/smartwatch.svg",
    "powermax-20000": "images/demo/products/powerbank.svg",
    "wave-mini-speaker": "images/demo/products/speaker.svg",
    "rgb-mechanical-keyboard": "images/demo/products/keyboard.svg",
    "homeglow-smart-lamp": "images/demo/products/smart-lamp.svg",
    "compact-brew-coffee-maker": "images/demo/products/coffee-maker.svg",
}


@register.simple_tag
def demo_product_asset(product):
    """Return a bundled, non-uploaded illustration for a seeded Demo product."""
    return DEMO_PRODUCT_ASSETS.get(
        getattr(product, "slug", ""),
        "images/demo/products/default.svg",
    )
