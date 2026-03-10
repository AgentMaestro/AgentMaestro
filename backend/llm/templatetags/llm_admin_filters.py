from django import template

register = template.Library()


@register.filter
def singleton_list(value):
    if value is None:
        return []
    return [value]


@register.filter
def cl_query_with_remove(changelist, remove_ids):
    if not hasattr(changelist, "get_query_string"):
        return ""
    remove_list = remove_ids or []
    return changelist.get_query_string(remove=remove_list)
