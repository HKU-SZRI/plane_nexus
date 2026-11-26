"""Filter protected work item references without changing stored relationships."""


def protect_issue_links(instance, data, context):
    from crum import get_current_user
    from plane.db.models import Issue
    from plane.utils.issue_relation_visibility import visible_relation_issues

    if not isinstance(instance, Issue):
        return data
    has_parent = instance.parent_id and any(key in data for key in ("parent", "parent_id"))
    has_relations = any(isinstance(data.get(key), list) for key in ("issue_relation", "issue_related"))
    if not has_parent and not has_relations:
        return data
    request = context.get("request")
    user = request.user if request else get_current_user()
    visible = visible_relation_issues(user, instance.workspace.slug)
    if has_parent and not visible.filter(id=instance.parent_id).exists():
        for key in ("parent", "parent_id"):
            if key in data:
                data[key] = None
    for key, manager, related_field in [
        ("issue_relation", "issue_relation", "related_issue"),
        ("issue_related", "issue_related", "issue"),
    ]:
        if key not in data or not isinstance(data[key], list):
            continue
        relations = getattr(instance, manager).filter(**{
            related_field + "_id__in": visible.values("id"),
        })
        ids = {str(value) for value in relations.values_list("id", flat=True)}
        ids.update(str(value) for value in relations.values_list(related_field + "_id", flat=True))
        data[key] = [entry for entry in data[key] if isinstance(entry, dict) and str(entry.get("id")) in ids]
    return data
