from feat.common import adapter
from feat.models import model, value
from feat.models import effect, call

import demo_service


@adapter.register(demo_service.Service, model.IModel)
class Service(model.Model):
    model.identity("service")
    model.attribute("size", value.Integer(),
                    call.source_call("count_documents"))
    model.child("documents", model="service.documents")


class Documents(model.Collection):
    model.identity("service.documents")
    model.child_model("service.documents.CATEGORY")
    model.child_names(call.source_call("iter_categories"))
    model.child_source(effect.context_value("key"))


class Category(model.Model):
    model.identity("service.documents.CATEGORY")
    model.attribute("category", value.String(),
                    effect.context_value("source"))
