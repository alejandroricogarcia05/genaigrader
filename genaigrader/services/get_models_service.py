from genaigrader.models import Model


def get_models_for_user(user):
    """
    Returns a tuple (local_models, external_models) where:
    - local_models: all local models (with null api_url and api_key)
    - external_models: external models filtered by the given user
    """
    local_models = Model.objects.filter(
        api_url__isnull=True, api_key__isnull=True
    ).select_related("family")
    external_models = (
        Model.objects.exclude(api_url__isnull=True, api_key__isnull=True)
        .filter(user=user)
        .select_related("family")
    )
    return local_models, external_models


def get_model_names_for_user(user):
    local_models, external_models = get_models_for_user(user)
    names = {m.description for m in local_models} | {
        m.description for m in external_models
    }
    return sorted(names)
