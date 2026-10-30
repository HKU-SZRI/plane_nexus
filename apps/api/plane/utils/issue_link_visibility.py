"""Filter cross-context references without changing stored relationships."""
def protect_issue_links(instance, data, context):
    from crum import get_current_user
    from plane.db.models import Issue, ProjectMember
    if not isinstance(instance, Issue):
        return data
    request = context.get("request")
    user = request.user if request else get_current_user()
    allowed = set()
    if user and getattr(user, "is_authenticated", False):
        allowed = set(ProjectMember.objects.filter(member=user,is_active=True).values_list("project_id",flat=True))
    allowed.add(instance.project_id)
    if instance.parent_id and (instance.parent.deleted_at or instance.parent.project_id not in allowed):
        for key in ("parent","parent_id"):
            if key in data:
                data[key] = None
    for key, manager, related_field in [
        ("issue_relation","issue_relation","related_issue"),
        ("issue_related","issue_related","issue"),
    ]:
        if key not in data or not isinstance(data[key],list):
            continue
        visible = getattr(instance,manager).filter(**{
            related_field+"__project_id__in":allowed,
            related_field+"__deleted_at__isnull":True,
        })
        ids = set(str(v) for v in visible.values_list("id",flat=True))
        ids.update(str(v) for v in visible.values_list(related_field+"_id",flat=True))
        data[key] = [entry for entry in data[key] if isinstance(entry,dict) and str(entry.get("id")) in ids]
    return data
