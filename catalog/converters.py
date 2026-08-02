class UnicodeSlugConverter:
    regex = r"[-\w\u0600-\u06FF]+"

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value
