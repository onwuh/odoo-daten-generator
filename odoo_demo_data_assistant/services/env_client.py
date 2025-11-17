"""
Wrapper, der die Aufrufe aus odoo_actions gegen das Odoo-ORM (self.env) weiterleitet.
So kann der vorhandene Service-Code sowohl mit dem JSON-Client als auch innerhalb
des Moduls verwendet werden.
"""


class OdooEnvClient:
    """Leichter Wrapper um das Odoo-Environment, der dieselbe API wie OdooJson2Client bereitstellt."""

    def __init__(self, env):
        self.env = env

    # -------------------------------------------------------------------------
    # Basismethoden
    # -------------------------------------------------------------------------
    def create(self, model: str, values: dict, context=None) -> int:
        record = self.env[model].with_context(context or {}).create(values)
        return record.id

    def write(self, model: str, ids, values, context=None) -> bool:
        recs = self.env[model].with_context(context or {}).browse(ids)
        recs.write(values)
        return True

    def search(self, model: str, domain, context=None):
        return self.env[model].with_context(context or {}).search(domain).ids

    def search_read(self, model: str, domain, fields=None, limit=None, context=None):
        return self.env[model].with_context(context or {}).search_read(domain, fields=fields, limit=limit)

    def call_method(self, model: str, method: str, ids=None, args=None, kwargs=None, context=None):
        args = args or []
        kwargs = kwargs or {}
        model_env = self.env[model].with_context(context or {})
        if ids is not None:
            recs = model_env.browse(ids)
            return getattr(recs, method)(*args, **kwargs)
        return getattr(model_env, method)(*args, **kwargs)

    def model_method(self, model: str, method: str, payload):
        """Für Kompatibilität – ruft das Modell mit payload-Argumenten auf."""
        model_env = self.env[model]
        return getattr(model_env, method)(**payload)

    def get_errors(self):
        """Kompatibilitäts-Methode – im Env-Kontext nicht benötigt."""
        return []
