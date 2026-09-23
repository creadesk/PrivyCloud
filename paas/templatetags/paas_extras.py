from django import template
register = template.Library()

@register.filter
def get_item(dictionary, key):
    """returns dictionary[key] or None"""
    return dictionary.get(key)