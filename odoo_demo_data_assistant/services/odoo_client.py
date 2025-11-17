import requests
from typing import Any, Dict, List, Optional


class OdooJson2Client:
    def __init__(self, base_url: str, database: str, api_key: str, user_agent: str = "odoo-daten-generator") -> None:
        self.base_url = base_url.rstrip('/') + "/json/2"
        self.database = database
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "X-Odoo-Database": self.database,
            "User-Agent": user_agent,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self.errors: List[Dict[str, Any]] = []  # Track all API errors

    def _post(self, path: str, payload: Dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"
        print(f"[HTTP] POST {url}")
        print(f"[HTTP] Payload keys: {list(payload.keys())}")
        response = self.session.post(url, json=payload, timeout=60)
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            error_body = ""
            try:
                error_body = response.text[:500]
                print(f"[HTTP] Error Body: {error_body}")
            except Exception:
                pass
            
            # Record the error
            error_info = {
                "url": url,
                "method": "POST",
                "status_code": response.status_code if response else None,
                "error_message": str(e),
                "error_body": error_body,
                "payload_keys": list(payload.keys())
            }
            self.errors.append(error_info)
            
            if response is not None and response.status_code == 401:
                # Retry without X-Odoo-Database (SaaS often infers DB from subdomain)
                orig_db = self.session.headers.pop("X-Odoo-Database", None)
                resp2 = self.session.post(url, json=payload, timeout=60)
                if resp2.status_code == 401 and orig_db:
                    # Retry with db query parameter
                    self.session.headers["X-Odoo-Database"] = orig_db  # restore for next try
                    resp3 = self.session.post(f"{url}?db={self.database}", json=payload, timeout=60)
                    resp3.raise_for_status()
                    # Remove error from list since retry succeeded
                    if self.errors and self.errors[-1]["url"] == url:
                        self.errors.pop()
                    print(f"[HTTP] Success after db query param: {resp3.status_code}")
                    return resp3.json()
                resp2.raise_for_status()
                # Remove error from list since retry succeeded
                if self.errors and self.errors[-1]["url"] == url:
                    self.errors.pop()
                print(f"[HTTP] Success after removing X-Odoo-Database: {resp2.status_code}")
                return resp2.json()
            raise
        # Some endpoints return JSON results directly, others wrap; assume JSON body is the result
        print(f"[HTTP] {response.status_code} OK")
        return response.json()

    def _post_with_variants(self, paths: List[str], payload: Dict[str, Any]) -> Any:
        last_error: Optional[Exception] = None
        for p in paths:
            try:
                return self._post(p, payload)
            except requests.HTTPError as e:
                last_error = e
                # try with trailing slash variant too
                try:
                    if not p.endswith('/'):
                        return self._post(p + '/', payload)
                except requests.HTTPError as e2:
                    last_error = e2
                    continue
        if last_error is not None:
            raise last_error
        raise RuntimeError("No paths provided for request")

    def model_method(self, model: str, method: str, payload: Dict[str, Any]) -> Any:

        # Prefer direct model path first (most endpoints exist there), then call_kw, then call
        return self._post_with_variants([
            f"/{model}/{method}",
            f"/call_kw/{model}/{method}",
            f"/call/{model}/{method}",
        ], payload)

    def search(self, model: str, domain: List[Any], context: Optional[Dict[str, Any]] = None) -> List[int]:
        payload: Dict[str, Any] = {"domain": domain}
        if context is not None:
            payload["context"] = context
        return self.model_method(model, "search", payload)

    def search_read(
        self,
        model: str,
        domain: List[Any],
        fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {"domain": domain}
        if fields is not None:
            payload["fields"] = fields
        if limit is not None:
            payload["limit"] = limit
        if context is not None:
            payload["context"] = context
        return self.model_method(model, "search_read", payload)

    def create(self, model: str, values: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> int:
        # Debug: Print the values being sent
        print(f"   [DEBUG] OdooClient.create called for model={model}")
        print(f"   [DEBUG] Values keys: {list(values.keys())}")
        for key, val in values.items():
            print(f"   [DEBUG]   {key} = {val} (type: {type(val).__name__})")
        
        # Try documented JSON-2 example format first: vals_list
        vals_list_payload: Dict[str, Any] = {"vals_list": [values]}
        if context is not None:
            vals_list_payload["context"] = context
        try:
            result = self._post_with_variants([
                f"/{model}/create",
            ], vals_list_payload)
            if isinstance(result, list):
                return result[0]
            return int(result)
        except requests.HTTPError as e:
            # Fallback to call variants using args/kwargs
            if e.response is not None and e.response.status_code in (404, 422):
                call_payload: Dict[str, Any] = {"args": [values], "kwargs": {}}
                if context is not None:
                    call_payload["context"] = context
                try:
                    result2 = self._post_with_variants([
                        f"/call/{model}/create",
                        f"/call_kw/{model}/create",
                    ], call_payload)
                    if isinstance(result2, list):
                        return result2[0]
                    return int(result2)
                except requests.HTTPError as e2:
                    if e2.response is not None and e2.response.status_code in (404, 422):
                        # Last fallback to direct {values}
                        payload: Dict[str, Any] = {"values": values}
                        if context is not None:
                            payload["context"] = context
                        result3 = self._post_with_variants([
                            f"/{model}/create",
                        ], payload)
                        if isinstance(result3, list):
                            return result3[0]
                        return int(result3)
                    raise
            raise

    def write(self, model: str, ids: List[int], values: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> bool:
        # Direct JSON-2 expects 'vals' key
        payload: Dict[str, Any] = {"ids": ids, "vals": values}
        if context is not None:
            payload["context"] = context
        try:
            result = self._post_with_variants([
                f"/{model}/write",
            ], payload)
            return bool(result)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (404, 422):
                # Fallback to call variants
                call_payload: Dict[str, Any] = {"args": [ids, values], "kwargs": {}}
                if context is not None:
                    call_payload["context"] = context
                result2 = self._post_with_variants([
                    f"/call_kw/{model}/write",
                    f"/call/{model}/write",
                ], call_payload)
                return bool(result2)
            raise

    def call_method(self, model: str, method: str, ids: Optional[List[int]] = None, args: Optional[List[Any]] = None, kwargs: Optional[Dict[str, Any]] = None, context: Optional[Dict[str, Any]] = None) -> Any:
        # Helper to call model methods possibly on recordsets
        args = args or []
        kwargs = kwargs or {}
        # 1) Try direct endpoint with 'ids' payload (JSON-2 recordset pattern)
        if ids is not None:
            direct_payload: Dict[str, Any] = {"ids": ids}
            direct_payload.update(kwargs)
            if context is not None:
                direct_payload["context"] = context
            try:
                return self._post_with_variants([
                    f"/{model}/{method}",
                ], direct_payload)
            except requests.HTTPError as e:
                if not (e.response is not None and e.response.status_code in (404, 422)):
                    raise
        # 2) Try call_kw with args/kwargs
        call_payload: Dict[str, Any] = {"args": ([] if ids is None else [ids]) + args, "kwargs": kwargs}
        if context is not None:
            call_payload["context"] = context
        try:
            return self._post_with_variants([
                f"/call_kw/{model}/{method}",
                f"/call/{model}/{method}",
                f"/{model}/{method}",
            ], call_payload)
        except requests.HTTPError:
            # 3) Last fallback: direct without args/kwargs
            fallback_payload: Dict[str, Any] = {}
            if context is not None:
                fallback_payload["context"] = context
            return self._post_with_variants([
                f"/{model}/{method}",
            ], fallback_payload)

    def get_errors(self) -> List[Dict[str, Any]]:
        """Return a list of all API errors that occurred during execution."""
        return self.errors.copy()

