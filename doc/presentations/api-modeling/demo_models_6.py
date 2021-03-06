from feat.common import adapter
from feat.models import model, value, action
from feat.models import effect, call, getter, setter

import demo_service


@adapter.register(demo_service.Service, model.IModel)
class Service(model.Model):
    model.identity("service")
    model.attribute("size", value.Integer(),
                    call.source_call("count_documents"))
    model.child("documents", model="service.documents")


class CreateDocument(action.Action):
    action.param("category", value.String())
    action.param("name", value.String())
    action.param("url", value.String())
    action.effect(call.source_perform("add_document"))


class Documents(model.Collection):
    model.identity("service.documents")
    model.child_model("service.documents.CATEGORY")
    model.child_names(call.source_call("iter_categories"))
    model.child_view(effect.context_value("key"))
    model.action("post", CreateDocument,
                 label="Create Document")


class Category(model.Collection):
    model.identity("service.documents.CATEGORY")
    model.child_model("service.documents.CATEGORY.NAME")
    model.child_names(call.model_call("_iter_documents"))
    model.child_view(getter.model_get("_get_document"))

    def _iter_documents(self):
        return self.source.iter_names(self.view)

    def _get_document(self, name):
        return self.source.get_document(self.view, name)


class Document(model.Model):
    model.identity("service.documents.CATEGORY.NAME")
    model.attribute("category", value.String(), getter.view_getattr())
    model.attribute("name", value.String(), getter.view_getattr())
    model.attribute("url", value.String(),
                    getter.view_getattr(), setter.view_setattr())
    model.attribute("content", value.Binary("text/html"),
                    getter.view_getattr())
