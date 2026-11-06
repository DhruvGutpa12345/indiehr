from drf_spectacular.extensions import OpenApiAuthenticationExtension


class StaffSessionScheme(OpenApiAuthenticationExtension):
    target_class = "ihr_identity.authentication.StaffSessionAuthentication"
    name = "staffSession"

    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "cookie", "name": "ihr_session"}


class ServiceTokenScheme(OpenApiAuthenticationExtension):
    target_class = "ihr_identity.authentication.ServiceTokenAuthentication"
    name = "serviceToken"

    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "header", "name": "Authorization"}
