from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Get an item from a dictionary by key."""
    if dictionary is None:
        return None
    return dictionary.get(key)


@register.filter
def is_in_list(value, the_list):
    """Check if a value is in a list."""
    if the_list is None:
        return False
    return value in the_list


@register.filter
def dictsumby(lst, key):
    """Sum the values of a key across a list of dicts."""
    if not lst:
        return 0
    total = 0
    for item in lst:
        val = item.get(key, 0) if isinstance(item, dict) else getattr(item, key, 0)
        try:
            total += float(val or 0)
        except (TypeError, ValueError):
            pass
    return round(total, 2)


@register.filter
def dictsumby_nested(lst, key_path):
    """Sum the values of a dotted key path (e.g. "ded_cols.late") across a list of dicts/objects."""
    if not lst:
        return 0
    keys = key_path.split('.')
    total = 0
    for item in lst:
        val = item
        for key in keys:
            if val is None:
                break
            val = val.get(key, None) if isinstance(val, dict) else getattr(val, key, None)
        try:
            total += float(val or 0)
        except (TypeError, ValueError):
            pass
    return round(total, 2)
